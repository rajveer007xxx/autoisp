"""Unified authentication-method backends (Phase 2+).

All MikroTik provisioning/kicking/walled-garden logic is routed through
`get_backend(auth_type)` so `_enforce_user_state` only needs to know which
method the customer uses — not how that method works.

Backends:
    * PPPoEBackend   — /ppp/secret + /ppp/active       (existing behaviour)
    * StaticIPBackend — /ip/firewall/address-list      (with optional MAC via /ip/arp)
    * HotspotBackend  — /ip/hotspot/user + /ip/hotspot/active

Each backend implements four verbs:
    provision(rc, cust)      — create whatever object represents the user
    deprovision(rc, cust)    — remove it entirely
    disable(rc, cust)        — move the user into walled-garden / block state
    enable(rc, cust)         — restore the user
    kick(rc, cust)           — drop any live session so re-auth picks the
                               correct IP pool (this is the "disconnect-once"
                               behaviour requested in s36b5)

rc is an already-entered `RouterOSClient` (context-manager-wrapped) so
backends can share the connection within one `_enforce_user_state` call.

All methods are idempotent: if the object already exists / already absent,
they return a benign result instead of raising.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict

# Walled-garden address-list name used on the router for static-IP parking.
WALLED_GARDEN_LIST = "isp-walled-garden"
ALLOWED_LIST = "isp-active-static"


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------
class AuthBackend(ABC):
    """Uniform contract every auth-method backend must honour."""

    key: str = ""

    @abstractmethod
    def provision(self, rc, cust) -> Dict[str, Any]: ...

    @abstractmethod
    def deprovision(self, rc, cust) -> Dict[str, Any]: ...

    @abstractmethod
    def disable(self, rc, cust) -> Dict[str, Any]:
        """Move user to walled-garden / parking. Does NOT remove their record."""

    @abstractmethod
    def enable(self, rc, cust) -> Dict[str, Any]:
        """Restore user to full-access / real IP pool."""

    @abstractmethod
    def kick(self, rc, cust) -> Dict[str, Any]:
        """Terminate any live session; on next re-auth the user gets a new IP."""


# ----------------------------------------------------------------------
# PPPoE
# ----------------------------------------------------------------------
class PPPoEBackend(AuthBackend):
    key = "pppoe"

    def provision(self, rc, cust):
        # PPPoE provisioning lives in routeros_provision.provision_customer_pppoe;
        # we only implement the state-change verbs here. Returns benign no-op.
        return {"ok": True, "noop": "provision handled by routeros_provision"}

    def deprovision(self, rc, cust):
        try:
            secrets = rc._api.path("ppp/secret")
            for row in secrets:
                if (row.get("name") or "").strip() == cust.username:
                    try:
                        secrets.remove(row[".id"])
                    except TypeError:
                        secrets("remove", **{".id": row[".id"]})
                    return {"ok": True, "removed": cust.username}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "noop": "secret not found"}

    def _toggle_secret(self, rc, cust, disabled: bool):
        touched = False
        secrets = rc._api.path("ppp/secret")
        for row in secrets:
            if (row.get("name") or "").strip() == cust.username:
                secrets.update(**{".id": row[".id"],
                                  "disabled": "yes" if disabled else "no"})
                touched = True
                break
        return touched

    def disable(self, rc, cust):
        try:
            t = self._toggle_secret(rc, cust, True)
            return {"ok": True, "touched": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def enable(self, rc, cust):
        try:
            t = self._toggle_secret(rc, cust, False)
            return {"ok": True, "touched": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kick(self, rc, cust):
        try:
            actives = rc._api.path("ppp/active")
            kicked = 0
            for a in actives:
                if (a.get("name") or "").strip() == cust.username:
                    try:
                        actives.remove(a[".id"])
                    except TypeError:
                        actives("remove", **{".id": a[".id"]})
                    kicked += 1
            return {"ok": True, "kicked": kicked}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Static IP (with optional MAC binding)
# ----------------------------------------------------------------------
class StaticIPBackend(AuthBackend):
    key = "static_ip"

    def _comment_tag(self, cust) -> str:
        return f"isp:{cust.customer_id}"

    def _list_entries(self, rc, list_name, cust):
        """Return rows in /ip/firewall/address-list matching this customer."""
        out = []
        try:
            entries = rc._api.path("ip/firewall/address-list")
            for row in entries:
                if (row.get("list") == list_name
                        and ((row.get("comment") or "").startswith(self._comment_tag(cust))
                             or (row.get("address") == (cust.static_ip_address or "")))):
                    out.append(row)
        except Exception:
            pass
        return out

    def _remove_from_list(self, rc, list_name, cust):
        entries = rc._api.path("ip/firewall/address-list")
        removed = 0
        for row in self._list_entries(rc, list_name, cust):
            try:
                entries.remove(row[".id"])
            except TypeError:
                entries("remove", **{".id": row[".id"]})
            removed += 1
        return removed

    def _add_to_list(self, rc, list_name, cust):
        ip = (cust.static_ip_address or "").strip()
        if not ip:
            return 0
        entries = rc._api.path("ip/firewall/address-list")
        # Idempotent: skip if already present
        for row in entries:
            if (row.get("list") == list_name and row.get("address") == ip):
                return 0
        try:
            entries.add(list=list_name, address=ip,
                        comment=self._comment_tag(cust))
        except TypeError:
            entries("add", list=list_name, address=ip,
                    comment=self._comment_tag(cust))
        return 1

    def _arp_remove(self, rc, cust):
        """Remove any /ip/arp entry for this customer's IP (best-effort)."""
        ip = (cust.static_ip_address or "").strip()
        if not ip:
            return 0
        try:
            arp = rc._api.path("ip/arp")
            removed = 0
            for row in arp:
                if row.get("address") == ip:
                    try:
                        arp.remove(row[".id"])
                    except TypeError:
                        arp("remove", **{".id": row[".id"]})
                    removed += 1
            return removed
        except Exception:
            return 0

    def _arp_add(self, rc, cust, interface: str = "bridge"):
        ip = (cust.static_ip_address or "").strip()
        mac = (cust.mac_address or "").strip().upper()
        if not ip or not mac or mac in ("NONE", "N/A"):
            return 0
        try:
            arp = rc._api.path("ip/arp")
            # Idempotent check
            for row in arp:
                if row.get("address") == ip and (row.get("mac-address") or "").upper() == mac:
                    return 0
            try:
                arp.add(address=ip, **{"mac-address": mac},
                        interface=interface,
                        comment=self._comment_tag(cust))
            except TypeError:
                arp("add", address=ip, **{"mac-address": mac},
                    interface=interface,
                    comment=self._comment_tag(cust))
            return 1
        except Exception:
            return 0

    def _flush_connections(self, rc, cust):
        """Remove firewall connection-tracking rows for this IP so existing
        TCP flows die immediately (static has no 'session' to kick)."""
        ip = (cust.static_ip_address or "").strip()
        if not ip:
            return 0
        try:
            conns = rc._api.path("ip/firewall/connection")
            killed = 0
            for row in conns:
                if (row.get("src-address", "").startswith(ip + ":")
                        or row.get("dst-address", "").startswith(ip + ":")):
                    try:
                        conns.remove(row[".id"])
                    except TypeError:
                        conns("remove", **{".id": row[".id"]})
                    killed += 1
            return killed
        except Exception:
            return 0

    # -- verbs --
    def provision(self, rc, cust):
        try:
            added = self._add_to_list(rc, ALLOWED_LIST, cust)
            arp = self._arp_add(rc, cust)
            return {"ok": True, "added": added, "arp": arp}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def deprovision(self, rc, cust):
        try:
            rm = self._remove_from_list(rc, ALLOWED_LIST, cust)
            rm += self._remove_from_list(rc, WALLED_GARDEN_LIST, cust)
            self._arp_remove(rc, cust)
            self._flush_connections(rc, cust)
            return {"ok": True, "removed": rm}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def disable(self, rc, cust):
        """Move to walled-garden pool by flipping address-list membership."""
        try:
            self._remove_from_list(rc, ALLOWED_LIST, cust)
            added = self._add_to_list(rc, WALLED_GARDEN_LIST, cust)
            return {"ok": True, "walled": added}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def enable(self, rc, cust):
        try:
            self._remove_from_list(rc, WALLED_GARDEN_LIST, cust)
            added = self._add_to_list(rc, ALLOWED_LIST, cust)
            self._arp_add(rc, cust)
            return {"ok": True, "allowed": added}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kick(self, rc, cust):
        """Static has no PPPoE 'session' to kick — instead we flush the
        connection-tracking rows so existing TCP flows die. The firewall
        address-list already reflects the new state, so new flows obey it."""
        try:
            k = self._flush_connections(rc, cust)
            return {"ok": True, "flushed_connections": k}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Hotspot
# ----------------------------------------------------------------------
class HotspotBackend(AuthBackend):
    key = "hotspot"

    def _iter_users(self, rc):
        return list(rc._api.path("ip/hotspot/user"))

    def _find_user(self, rc, username):
        for row in self._iter_users(rc):
            if (row.get("name") or "").strip() == username:
                return row
        return None

    def provision(self, rc, cust):
        """Provisioning lives in routeros_provision.provision_customer_hotspot."""
        return {"ok": True, "noop": "provision handled by routeros_provision"}

    def deprovision(self, rc, cust):
        try:
            users = rc._api.path("ip/hotspot/user")
            row = self._find_user(rc, cust.username)
            if row:
                try:
                    users.remove(row[".id"])
                except TypeError:
                    users("remove", **{".id": row[".id"]})
                return {"ok": True, "removed": cust.username}
            return {"ok": True, "noop": "hotspot-user not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _toggle(self, rc, cust, disabled: bool):
        users = rc._api.path("ip/hotspot/user")
        row = self._find_user(rc, cust.username)
        if not row:
            return False
        users.update(**{".id": row[".id"],
                        "disabled": "yes" if disabled else "no"})
        return True

    def disable(self, rc, cust):
        try:
            t = self._toggle(rc, cust, True)
            return {"ok": True, "touched": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def enable(self, rc, cust):
        try:
            t = self._toggle(rc, cust, False)
            return {"ok": True, "touched": t}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kick(self, rc, cust):
        try:
            actives = rc._api.path("ip/hotspot/active")
            kicked = 0
            for a in actives:
                if (a.get("user") or "").strip() == cust.username:
                    try:
                        actives.remove(a[".id"])
                    except TypeError:
                        actives("remove", **{".id": a[".id"]})
                    kicked += 1
            return {"ok": True, "kicked": kicked}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------
_BACKENDS: Dict[str, AuthBackend] = {
    "pppoe": PPPoEBackend(),
    "static_ip": StaticIPBackend(),
    "hotspot": HotspotBackend(),
}


def get_backend(auth_type: str | None) -> AuthBackend:
    key = (auth_type or "pppoe").strip().lower()
    return _BACKENDS.get(key, _BACKENDS["pppoe"])


def backend_for_customer(cust) -> AuthBackend:
    return get_backend(getattr(cust, "auth_type", None))
