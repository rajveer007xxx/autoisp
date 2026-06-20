"""Mikrotik dual-WAN Load Balancing provisioner.

Pushes idempotent rules tagged with comment 'lb/autoispbilling' so they
can be enumerated and removed cleanly on disable. Supports:
  - pcc_balanced  (50/50 PCC)
  - pcc_weighted  (admin-set weights, e.g. 70/30)
  - failover      (WAN1 primary, WAN2 standby with check-gateway)

Used from lb_routes.py via:
    with rp.RouterOSClient(nas, dry_run=False) as rc:
        lb = LoadBalancer(rc)
        lb.apply(cfg)
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict, List

log = logging.getLogger(__name__)

LB_TAG = "lb/autoispbilling"


class LoadBalancer:
    """Wraps a live RouterOSClient and exposes high-level dual-WAN ops."""

    def __init__(self, ros_client):
        self._rc = ros_client
        self._api = ros_client._api  # underlying librouteros connection
        self._is_v7 = self._detect_v7()

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------
    def _detect_v7(self) -> bool:
        try:
            res = next(iter(self._api.path("system/resource")), {}) or {}
            ver = (res.get("version") or "").strip()
            return ver.startswith("7")
        except Exception:
            return True  # default to v7 path

    def list_interfaces(self) -> List[Dict]:
        """Physical + virtual interfaces — admins pick from this list."""
        out = []
        try:
            for i in self._api.path("interface"):
                name = i.get("name", "")
                if not name:
                    continue
                out.append({
                    "name": name,
                    "type": i.get("type", ""),
                    "running": (i.get("running") or "false") in ("true", True),
                    "disabled": (i.get("disabled") or "false") in ("true", True),
                })
        except Exception as e:
            log.warning(f"list_interfaces: {e}")
        return out

    def detect_wan1(self) -> Dict:
        """Find existing default route + its gateway interface + IP."""
        try:
            routes = list(self._api.path("ip/route"))
            for r in routes:
                if (r.get("dst-address") or "") == "0.0.0.0/0" and \
                        (r.get("active") in ("true", True)) and \
                        (r.get("comment") or "") != LB_TAG:
                    gw = (r.get("gateway") or "").split("%")[0]
                    iface = r.get("gateway-interface") or ""
                    address = ""
                    if iface:
                        for a in self._api.path("ip/address"):
                            if a.get("interface") == iface and (a.get("comment") or "") != LB_TAG:
                                address = a.get("address", "")
                                break
                    return {"interface": iface, "address": address, "gateway": gw}
        except Exception as e:
            log.warning(f"detect_wan1: {e}")
        return {}

    def list_iface_users(self, iface: str) -> Dict:
        """Every service currently bound to the given interface — used for
        the migration preview dialog."""
        users = {
            "pppoe_servers": [],
            "hotspot": [],
            "dhcp_servers": [],
            "ip_addresses": [],
        }
        try:
            for r in self._api.path("interface/pppoe-server/server"):
                if r.get("interface") == iface:
                    users["pppoe_servers"].append({
                        "id": r.get(".id"),
                        "service-name": r.get("service-name", ""),
                    })
        except Exception:
            pass
        try:
            for r in self._api.path("ip/hotspot"):
                if r.get("interface") == iface:
                    users["hotspot"].append({
                        "id": r.get(".id"),
                        "name": r.get("name", ""),
                    })
        except Exception:
            pass
        try:
            for r in self._api.path("ip/dhcp-server"):
                if r.get("interface") == iface:
                    users["dhcp_servers"].append({
                        "id": r.get(".id"),
                        "name": r.get("name", ""),
                    })
        except Exception:
            pass
        try:
            for r in self._api.path("ip/address"):
                if r.get("interface") == iface and (r.get("comment") or "") != LB_TAG:
                    users["ip_addresses"].append({
                        "id": r.get(".id"),
                        "address": r.get("address", ""),
                    })
        except Exception:
            pass
        return users

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def migrate_iface(self, old_iface: str, new_iface: str) -> Dict:
        """Re-bind PPPoE / Hotspot / DHCP / non-LB IP-addresses from old
        to new interface. Idempotent."""
        moved = {"pppoe_servers": 0, "hotspot": 0, "dhcp_servers": 0,
                 "ip_addresses": 0}
        for path, key in (
            ("interface/pppoe-server/server", "pppoe_servers"),
            ("ip/hotspot", "hotspot"),
            ("ip/dhcp-server", "dhcp_servers"),
        ):
            try:
                p = self._api.path(path)
                for r in list(p):
                    if r.get("interface") == old_iface:
                        try:
                            p.update(**{".id": r[".id"], "interface": new_iface})
                            moved[key] += 1
                        except Exception as e:
                            log.warning(f"migrate {path} {r.get('.id')} fail: {e}")
            except Exception:
                pass
        try:
            p = self._api.path("ip/address")
            for r in list(p):
                if r.get("interface") == old_iface and (r.get("comment") or "") != LB_TAG:
                    try:
                        p.update(**{".id": r[".id"], "interface": new_iface})
                        moved["ip_addresses"] += 1
                    except Exception as e:
                        log.warning(f"migrate ip/address {r.get('.id')} fail: {e}")
        except Exception:
            pass
        return moved

    def _purge_tagged(self) -> int:
        """Remove every rule tagged with LB_TAG across all relevant paths.
        Returns total count purged."""
        paths = [
            "ip/firewall/mangle",
            "ip/firewall/nat",
            "ip/firewall/filter",
            "ip/route",
            "ip/address",
        ]
        if self._is_v7:
            paths.append("routing/table")
        removed = 0
        for path in paths:
            try:
                p = self._api.path(path)
                ids = [r[".id"] for r in p
                       if (r.get("comment") or "") == LB_TAG and ".id" in r]
                for rid in ids:
                    try:
                        p.remove(rid)
                        removed += 1
                    except Exception as e:
                        log.warning(f"purge {path} {rid} fail: {e}")
            except Exception as e:
                log.warning(f"purge enumerate {path} fail: {e}")
        return removed

    def apply(self, cfg: Dict) -> Dict:
        """Apply the load-balancing configuration. cfg keys:
            wan1_iface, wan1_gw,
            wan2_iface, wan2_ip, wan2_gw,
            lan_iface,
            strategy in (pcc_balanced, pcc_weighted, failover),
            weight1, weight2,
            dns (optional, comma-separated)
        Returns dict with ok flag, backup name, push counters.
        """
        result: Dict[str, Any] = {"ok": False, "stage": "", "error": "",
                                   "backup": "", "is_v7": self._is_v7,
                                   "pushed": {}}

        # 1) Take a config backup so the admin can roll back externally
        backup_name = f"lb-backup-{int(time.time())}"
        try:
            self._api(("/system/backup/save",), name=backup_name)
            result["backup"] = backup_name
        except Exception as e:
            log.warning(f"backup save failed (non-fatal): {e}")
            result["backup_warning"] = str(e)

        # 2) Wipe any existing lb-tagged rules — full replace each save
        try:
            result["purged"] = self._purge_tagged()
        except Exception as e:
            result["stage"] = "purge"; result["error"] = str(e); return result

        # 3) Push WAN2 /ip/address
        try:
            ipa = self._api.path("ip/address")
            ipa.add(address=cfg["wan2_ip"],
                    interface=cfg["wan2_iface"],
                    comment=LB_TAG)
        except Exception as e:
            # If exact same address already exists we tolerate it
            if "already have such address" not in str(e).lower():
                result["stage"] = "wan2_address"; result["error"] = str(e)
                return result

        # 4) Routing tables (RouterOS 7 only — v6 uses routing-mark on routes)
        if self._is_v7:
            try:
                rt = self._api.path("routing/table")
                existing = {r.get("name") for r in rt}
                for nm in ("to_wan1", "to_wan2"):
                    if nm not in existing:
                        rt.add(name=nm, fib="", comment=LB_TAG)
            except Exception as e:
                log.warning(f"routing/table add: {e}")

        # 5) Routes
        try:
            ipr = self._api.path("ip/route")
            rkey = "routing-table" if self._is_v7 else "routing-mark"
            ipr.add(**{
                "dst-address": "0.0.0.0/0",
                "gateway": cfg["wan1_gw"],
                rkey: "to_wan1",
                "check-gateway": "ping",
                "comment": LB_TAG,
            })
            ipr.add(**{
                "dst-address": "0.0.0.0/0",
                "gateway": cfg["wan2_gw"],
                rkey: "to_wan2",
                "check-gateway": "ping",
                "comment": LB_TAG,
            })
            # Default route fallbacks. For failover: WAN2 only on dist=2 (standby).
            ipr.add(**{
                "dst-address": "0.0.0.0/0",
                "gateway": cfg["wan1_gw"],
                "distance": "1",
                "check-gateway": "ping",
                "comment": LB_TAG,
            })
            ipr.add(**{
                "dst-address": "0.0.0.0/0",
                "gateway": cfg["wan2_gw"],
                "distance": "2" if cfg["strategy"] == "failover" else "1",
                "check-gateway": "ping",
                "comment": LB_TAG,
            })
        except Exception as e:
            result["stage"] = "routes"; result["error"] = str(e); return result

        # 6) NAT masquerade per WAN
        try:
            nat = self._api.path("ip/firewall/nat")
            nat.add(chain="srcnat",
                    **{"out-interface": cfg["wan1_iface"]},
                    action="masquerade", comment=LB_TAG)
            nat.add(chain="srcnat",
                    **{"out-interface": cfg["wan2_iface"]},
                    action="masquerade", comment=LB_TAG)
        except Exception as e:
            result["stage"] = "nat"; result["error"] = str(e); return result

        # 7) Mangle (skip entirely for failover — distance routing handles it)
        if cfg["strategy"] != "failover":
            try:
                mng = self._api.path("ip/firewall/mangle")
                # 7a) Mark connections coming from each WAN (so replies go back)
                mng.add(chain="input",
                        **{"in-interface": cfg["wan1_iface"]},
                        action="mark-connection",
                        **{"new-connection-mark": "wan1_conn"},
                        passthrough="yes", comment=LB_TAG)
                mng.add(chain="input",
                        **{"in-interface": cfg["wan2_iface"]},
                        action="mark-connection",
                        **{"new-connection-mark": "wan2_conn"},
                        passthrough="yes", comment=LB_TAG)
                # 7b) Outbound (router-originated) responses follow the conn mark
                mng.add(chain="output",
                        **{"connection-mark": "wan1_conn"},
                        action="mark-routing",
                        **{"new-routing-mark": "to_wan1"},
                        passthrough="no", comment=LB_TAG)
                mng.add(chain="output",
                        **{"connection-mark": "wan2_conn"},
                        action="mark-routing",
                        **{"new-routing-mark": "to_wan2"},
                        passthrough="no", comment=LB_TAG)
                # 7c) PCC bucket assignment for new LAN-originated connections
                if cfg["strategy"] == "pcc_balanced":
                    pcc_pairs = [("wan1_conn", "both-addresses-and-ports:2/0"),
                                 ("wan2_conn", "both-addresses-and-ports:2/1")]
                else:  # pcc_weighted
                    w1 = max(1, int(cfg.get("weight1") or 50))
                    w2 = max(1, int(cfg.get("weight2") or 50))
                    total = w1 + w2
                    pcc_pairs = []
                    for i in range(w1):
                        pcc_pairs.append(("wan1_conn",
                            f"both-addresses-and-ports:{total}/{i}"))
                    for i in range(w1, total):
                        pcc_pairs.append(("wan2_conn",
                            f"both-addresses-and-ports:{total}/{i}"))
                for cmark, classifier in pcc_pairs:
                    mng.add(chain="prerouting",
                            **{"in-interface": cfg["lan_iface"]},
                            **{"connection-mark": "no-mark"},
                            **{"dst-address-type": "!local"},
                            action="mark-connection",
                            **{"new-connection-mark": cmark},
                            **{"per-connection-classifier": classifier},
                            passthrough="yes", comment=LB_TAG)
                # 7d) After conn-mark, apply routing-mark per conn-mark
                mng.add(chain="prerouting",
                        **{"connection-mark": "wan1_conn"},
                        **{"in-interface": cfg["lan_iface"]},
                        action="mark-routing",
                        **{"new-routing-mark": "to_wan1"},
                        passthrough="no", comment=LB_TAG)
                mng.add(chain="prerouting",
                        **{"connection-mark": "wan2_conn"},
                        **{"in-interface": cfg["lan_iface"]},
                        action="mark-routing",
                        **{"new-routing-mark": "to_wan2"},
                        passthrough="no", comment=LB_TAG)
            except Exception as e:
                result["stage"] = "mangle"; result["error"] = str(e); return result

        # 8) Filter — only add safety rules if they don't already exist.
        #    We never delete the admin's existing filter chain.
        try:
            flt = self._api.path("ip/firewall/filter")
            existing = list(flt)

            def has(chain, conn_state, action):
                for r in existing:
                    if (r.get("chain") == chain and
                            r.get("connection-state") == conn_state and
                            r.get("action") == action):
                        return True
                return False

            for chain in ("input", "forward"):
                if not has(chain, "established,related", "accept"):
                    flt.add(chain=chain,
                            **{"connection-state": "established,related"},
                            action="accept", comment=LB_TAG)
                if not has(chain, "invalid", "drop"):
                    flt.add(chain=chain,
                            **{"connection-state": "invalid"},
                            action="drop", comment=LB_TAG)
        except Exception as e:
            log.warning(f"filter safety rules: {e}")

        # 9) DNS (optional — set on /ip/dns)
        if cfg.get("dns"):
            try:
                self._api(("/ip/dns/set",), servers=cfg["dns"],
                          **{"allow-remote-requests": "yes"})
            except Exception as e:
                log.warning(f"dns set: {e}")

        result["ok"] = True
        return result

    def disable(self) -> Dict:
        """Remove every lb-tagged rule. Returns count purged."""
        purged = self._purge_tagged()
        return {"ok": True, "purged": purged}
