"""
smart_provision.py — Phase 19.2 — Smart OLT Provision helpers

Single source of truth for:
  * Computing smart defaults for Wi-Fi (2.4G + 5G), LAN, DHCP from
    a customer's name + mobile number
  * Loading + applying the tenant's default Provision Profile
  * Persisting the resolved values onto the `onus` row so the existing
    `_genieacs_auto_push()` picks them up.

This module is pure-function + DB; it has no FastAPI dependencies so
it can be reused from the API layer, watchers, and CLI tools.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from database import engine


# ---------------------------------------------------------------------------
#  Defaults — fallback when no profile / no template substitution
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, object] = {
    "wifi_ssid_tpl":   "FIBERNET-{mobile_last4}-2G",
    "wifi_pw_tpl":     "{name_first4}{mobile_last4}",
    "wifi_ssid_5g_tpl":"FIBERNET-{mobile_last4}-5G",
    "wifi_pw_5g_tpl":  "{name_first4}{mobile_last4}",
    "wifi_band_split": 1,
    "wifi_bw_24":      "Auto",
    "wifi_bw_5":       "Auto",
    "wifi_auto_24":    1,
    "wifi_auto_5":     1,
    "wifi_radio_24":   1,
    "wifi_radio_5":    1,
    "lan_ip_tpl":      "192.168.1.1",
    "lan_netmask_tpl": "255.255.255.0",
    "dhcp_enabled":    1,
    "dhcp_start_tpl":  "192.168.1.2",
    "dhcp_end_tpl":    "192.168.1.254",
    "acs_inform_int":  60,
    "factory_reset_on_push": 0,
}


def _safe(s: Optional[str]) -> str:
    """Strip non-alphanumerics and lowercase. Empty-safe."""
    return re.sub(r"[^A-Za-z0-9]+", "", (s or "")).lower()


def _last4_digits(phone: Optional[str]) -> str:
    digits = re.sub(r"\D+", "", (phone or ""))
    if len(digits) >= 4:
        return digits[-4:]
    if digits:
        # pad with leading zeros to keep a stable 4-char SSID suffix.
        return digits.zfill(4)
    return "0000"


def _first4_name(name: Optional[str]) -> str:
    cleaned = _safe(name) or "user"
    return (cleaned[:4] or "user").ljust(4, "x")


def _first_token(name: Optional[str]) -> str:
    """__PHASE19_3__  Return the customer's first name token, sanitized.
    Splits on whitespace + dots + dashes + underscores so usernames like
    ``mp.sehbaz.fibernet`` and real names like ``Rajveer Singh`` both
    resolve correctly. Falls back to ``User``."""
    if not name:
        return "User"
    parts = re.split(r"[\s._\-]+", str(name).strip())
    for token in parts:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", token)
        if len(cleaned) >= 1:
            return cleaned
    return "User"


def _first_name_4(name: Optional[str]) -> str:
    """First 4 letters of the first name token (no padding)."""
    first = _first_token(name)
    return first[:4]


def _substitute(template: Optional[str], cust: Dict) -> Optional[str]:
    """Replace template tokens against the customer record."""
    if template is None:
        return None
    name = cust.get("name")
    return (template
            .replace("{first_name4}",  _first_name_4(name))
            .replace("{first_name}",   _first_token(name))
            .replace("{mobile_last4}", _last4_digits(cust.get("phone")))
            .replace("{name_first4}",  _first4_name(name))  # legacy alias
            .replace("{customer_id}",  str(cust.get("customer_id") or "")))


# ---------------------------------------------------------------------------
#  Profile load
# ---------------------------------------------------------------------------

def _load_profile(cid: str, profile_id: Optional[int] = None) -> Dict:
    """Return the chosen profile (or tenant default, or hardcoded fallback)."""
    out = dict(_DEFAULTS)
    cols = [
        "wifi_ssid_tpl", "wifi_pw_tpl", "wifi_ssid_5g_tpl", "wifi_pw_5g_tpl",
        "wifi_band_split", "wifi_channel_24", "wifi_channel_5",
        "wifi_bw_24", "wifi_bw_5",
        "wifi_auto_24", "wifi_auto_5", "wifi_radio_24", "wifi_radio_5",
        "lan_ip_tpl", "lan_netmask_tpl",
        "dhcp_enabled", "dhcp_start_tpl", "dhcp_end_tpl",
        "acs_inform_int", "factory_reset_on_push", "vlan", "connection_type",
    ]
    sel = ", ".join(cols)
    row = None
    try:
        with engine.begin() as conn:
            if profile_id:
                row = conn.exec_driver_sql(
                    f"SELECT id, {sel} FROM onu_service_profiles "
                    "WHERE id=%s AND company_id=%s LIMIT 1",
                    (int(profile_id), cid)).fetchone()
            if not row:
                row = conn.exec_driver_sql(
                    f"SELECT id, {sel} FROM onu_service_profiles "
                    "WHERE company_id=%s AND is_default=1 "
                    "ORDER BY id LIMIT 1",
                    (cid,)).fetchone()
    except Exception:
        row = None
    if row:
        keymap = ["id"] + cols
        for k, v in zip(keymap, row):
            if v is not None and k != "id":
                out[k] = v
        out["_profile_id"] = row[0]
        out["_profile_name"] = "(loaded)"
    return out


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def build_smart_defaults(cid: str, customer: Dict,
                         profile_id: Optional[int] = None) -> Dict:
    """Resolve all Wi-Fi/LAN/DHCP/Adv defaults for a given customer.

    Args
    ----
    cid: tenant company_id
    customer: {customer_id, name, phone}
    profile_id: optional override; otherwise tenant default

    Returns
    -------
    Concrete values ready to be written to `onus` row:
      {
        wifi_ssid, wifi_password,
        wifi_band_split, wifi_ssid_5g, wifi_password_5g,
        wifi_channel_24, wifi_channel_5,
        wifi_bw_24, wifi_bw_5,
        wifi_auto_24, wifi_auto_5,
        wifi_radio_24_enabled, wifi_radio_5_enabled,
        lan_ip, lan_netmask,
        dhcp_enabled, dhcp_start, dhcp_end,
        inform_interval, factory_reset_on_push,
        _profile_id (informational)
      }
    """
    p = _load_profile(cid, profile_id)
    out = {
        "wifi_ssid":           _substitute(p["wifi_ssid_tpl"], customer),
        "wifi_password":       _substitute(p["wifi_pw_tpl"], customer),
        "wifi_band_split":     int(p.get("wifi_band_split") or 0),
        "wifi_ssid_5g":        _substitute(p.get("wifi_ssid_5g_tpl"), customer),
        "wifi_password_5g":    _substitute(p.get("wifi_pw_5g_tpl"), customer),
        "wifi_channel_24":     p.get("wifi_channel_24"),
        "wifi_channel_5":      p.get("wifi_channel_5"),
        "wifi_bw_24":          p.get("wifi_bw_24") or "Auto",
        "wifi_bw_5":           p.get("wifi_bw_5") or "Auto",
        "wifi_auto_24":        int(p.get("wifi_auto_24") or 1),
        "wifi_auto_5":         int(p.get("wifi_auto_5") or 1),
        "wifi_radio_24_enabled": int(p.get("wifi_radio_24") or 1),
        "wifi_radio_5_enabled":  int(p.get("wifi_radio_5") or 1),
        "lan_ip":              _substitute(p.get("lan_ip_tpl"), customer),
        "lan_netmask":         p.get("lan_netmask_tpl") or "255.255.255.0",
        "dhcp_enabled":        int(p.get("dhcp_enabled") or 1),
        "dhcp_start":          _substitute(p.get("dhcp_start_tpl"), customer),
        "dhcp_end":            _substitute(p.get("dhcp_end_tpl"), customer),
        "inform_interval":     min(60, int(p.get("acs_inform_int") or 60)),
        "factory_reset_on_push": int(p.get("factory_reset_on_push") or 0),
        "_profile_id":         p.get("_profile_id"),
    }
    # __PHASE19_3__ — splice in WAN config derived from the customer.
    out.update(build_wan_for_onu(customer))
    return out


def fetch_customer_for_onu(cid: str, customer_id: Optional[str],
                           onu_id: Optional[int] = None) -> Dict:
    """Pull (name, phone) for the customer linked to the ONU or by id."""
    if not customer_id and onu_id is not None:
        with engine.begin() as conn:
            r = conn.exec_driver_sql(
                "SELECT customer_id FROM onus WHERE id=%s AND company_id=%s",
                (int(onu_id), cid)).fetchone()
            if r:
                customer_id = r[0]
    if not customer_id:
        return {"customer_id": None, "name": None, "phone": None}
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT customer_id, customer_name AS name, customer_phone AS phone, "
            "       username, pppoe_password, auth_type, "
            "       static_ip_address, static_netmask, fix_ip_address, "
            "       vlan_enabled, vlan_id "
            "FROM customers WHERE company_id=%s AND "
            "      (customer_id=%s OR username=%s) LIMIT 1",
            (cid, customer_id, customer_id)).fetchone()
    if not r:
        return {"customer_id": customer_id, "name": None, "phone": None,
                "wan": {"mode": None}}
    return {
        "customer_id": r[0], "name": r[1], "phone": r[2],
        "wan": {
            "mode": (r[5] or "pppoe").lower(),       # pppoe / static_ip / dhcp / bridge
            "username": r[3],
            "password": r[4],
            "static_ip": (r[6] or None),
            "static_netmask": (r[7] or "255.255.255.0"),
            "fix_ip": (r[8] or "No"),
            "vlan_enabled": int(r[9] or 0),
            "vlan_id": r[10],
        },
    }


def build_wan_for_onu(customer: Dict) -> Dict:
    """__PHASE19_3__  Map customer.wan{} to ONU.wan_* columns.

    Supports four authoritative modes:
      * ``pppoe``     — Username + Password + optional Service-Name + VLAN
      * ``static_ip`` — IP + Netmask + Gateway + DNS + VLAN
      * ``dhcp``      — DHCP client + VLAN
      * ``bridge``    — Bridge mode, only VLAN matters
    """
    wan = (customer or {}).get("wan") or {}
    raw_mode = (wan.get("mode") or "pppoe").lower().replace("-", "_")
    # Normalize legacy values: 'static_ip' vs 'static', 'PPPoE' vs 'pppoe'
    mode = {
        "static": "static_ip", "static_ip": "static_ip", "staticip": "static_ip",
        "ppp": "pppoe", "pppoe": "pppoe",
        "dhcp": "dhcp", "dynamic": "dhcp",
        "bridge": "bridge", "bridged": "bridge",
    }.get(raw_mode, raw_mode)
    out = {
        "wan_mode": mode,
        "wan_username": None, "wan_password": None,
        "wan_static_ip": None, "wan_netmask": None,
        "wan_gateway": None, "wan_dns": None,
        "wan_vlan": None, "wan_service_name": None,
    }
    if mode == "pppoe":
        out["wan_username"] = wan.get("username")
        out["wan_password"] = wan.get("password")
    elif mode == "static_ip":
        out["wan_static_ip"] = wan.get("static_ip")
        out["wan_netmask"] = wan.get("static_netmask") or "255.255.255.0"
    # VLAN applies in every mode if enabled on the customer
    if int(wan.get("vlan_enabled") or 0) == 1 and wan.get("vlan_id"):
        try:
            out["wan_vlan"] = int(wan["vlan_id"])
        except Exception:
            out["wan_vlan"] = None
    return out


def merge_with_overrides(defaults: Dict, body: Dict) -> Dict:
    """Operator-supplied form values win over defaults; None/empty falls back."""
    merged = dict(defaults)
    if not body:
        return merged
    field_map = {
        "wifi_ssid": "wifi_ssid", "wifi_password": "wifi_password",
        "wifi_band_split": "wifi_band_split",
        "wifi_ssid_5g": "wifi_ssid_5g", "wifi_password_5g": "wifi_password_5g",
        "wifi_channel_24": "wifi_channel_24",
        "wifi_channel_5":  "wifi_channel_5",
        "wifi_bw_24": "wifi_bw_24", "wifi_bw_5": "wifi_bw_5",
        "wifi_auto_24": "wifi_auto_24", "wifi_auto_5": "wifi_auto_5",
        "wifi_radio_24_enabled": "wifi_radio_24_enabled",
        "wifi_radio_5_enabled":  "wifi_radio_5_enabled",
        "lan_ip": "lan_ip", "lan_netmask": "lan_netmask",
        "dhcp_enabled": "dhcp_enabled",
        "dhcp_start": "dhcp_start", "dhcp_end": "dhcp_end",
        "inform_interval": "inform_interval",
        "factory_reset_on_push": "factory_reset_on_push",
        # __PHASE19_3__ — WAN overrides
        "wan_mode": "wan_mode",
        "wan_username": "wan_username", "wan_password": "wan_password",
        "wan_static_ip": "wan_static_ip", "wan_netmask": "wan_netmask",
        "wan_gateway": "wan_gateway", "wan_dns": "wan_dns",
        "wan_vlan": "wan_vlan", "wan_service_name": "wan_service_name",
    }
    for src, dst in field_map.items():
        if src in body and body[src] not in (None, ""):
            merged[dst] = body[src]
    # Clamp inform_interval to [5, 60]
    try:
        ii = int(merged.get("inform_interval") or 60)
        merged["inform_interval"] = max(5, min(60, ii))
    except Exception:
        merged["inform_interval"] = 60
    return merged


def persist_to_onu(cid: str, onu_id: int, resolved: Dict) -> None:
    """Write the resolved values onto the ONU row so subsequent
    `_genieacs_auto_push()` invocations have a consistent source.
    Only writes non-empty values."""
    fields, args = [], []
    db_map = {
        "wifi_ssid": "wifi_ssid", "wifi_password": "wifi_password",
        "wifi_band_split": "wifi_band_split",
        "wifi_ssid_5g": "wifi_ssid_5g", "wifi_password_5g": "wifi_password_5g",
        "wifi_channel_24": "wifi_channel_24", "wifi_channel_5": "wifi_channel_5",
        "wifi_bw_24": "wifi_bw_24", "wifi_bw_5": "wifi_bw_5",
        "wifi_auto_24": "wifi_auto_24", "wifi_auto_5": "wifi_auto_5",
        "wifi_radio_24_enabled": "wifi_radio_24_enabled",
        "wifi_radio_5_enabled":  "wifi_radio_5_enabled",
        "lan_ip": "lan_ip", "lan_netmask": "lan_netmask",
        "dhcp_enabled": "dhcp_enabled",
        "dhcp_start": "dhcp_start", "dhcp_end": "dhcp_end",
        "factory_reset_on_push": "factory_reset_on_push",
        # __PHASE19_3__ — WAN columns
        "wan_mode": "wan_mode",
        "wan_username": "wan_username", "wan_password": "wan_password",
        "wan_static_ip": "wan_static_ip", "wan_netmask": "wan_netmask",
        "wan_gateway": "wan_gateway", "wan_dns": "wan_dns",
        "wan_vlan": "wan_vlan", "wan_service_name": "wan_service_name",
    }
    for k, col in db_map.items():
        v = resolved.get(k)
        if v in (None, ""):
            continue
        fields.append(f"{col}=%s")
        args.append(v)
    if not fields:
        return
    args.extend([int(onu_id), cid])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE onus SET {', '.join(fields)} "
            "WHERE id=%s AND company_id=%s",
            tuple(args))
