"""
OLT vendor adapters — Session OLT-2 (rev. net-snmp).

Strategy: shell out to system `snmpget` / `snmpbulkwalk` from the
`snmp` package (net-snmp 5.9+).  This is more reliable than the
fragmented pysnmp ecosystem and is what production NMS tools use.

Each adapter exposes:
    poll_real(olt) -> (ok: bool, error: str|None, olt_meta: dict, onus: list)
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from typing import Tuple, List, Dict, Any, Optional

_SNMPGET = shutil.which("snmpget")
_SNMPWALK = shutil.which("snmpbulkwalk") or shutil.which("snmpwalk")
_SNMP_AVAILABLE = bool(_SNMPGET and _SNMPWALK)


VENDOR_PROFILES: Dict[str, Dict[str, Any]] = {
    "huawei": {
        "label":     "Huawei MA56xx / MA58xx",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "cpu":       "1.3.6.1.4.1.2011.6.3.4.1.3.0",
        "mem":       "1.3.6.1.4.1.2011.6.3.5.1.1.2.1.5.0",
        "temp":      "1.3.6.1.4.1.2011.6.3.6.1.1.10.1.1",
        "onu_serial":  "1.3.6.1.4.1.2011.6.128.1.1.2.43.1.3",
        "onu_status":  "1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15",
        "onu_rx":      "1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4",
        "onu_tx":      "1.3.6.1.4.1.2011.6.128.1.1.2.51.1.6",
        "onu_distance": "1.3.6.1.4.1.2011.6.128.1.1.2.46.1.20",
    },
    "nokia": {
        "label":     "Nokia 7360 ISAM",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.637.61.1.35.4.1.4",
        "onu_status":  "1.3.6.1.4.1.637.61.1.35.4.1.10",
        "onu_rx":      "1.3.6.1.4.1.637.61.1.35.6.1.10",
        "onu_tx":      "1.3.6.1.4.1.637.61.1.35.6.1.11",
        "onu_distance": "1.3.6.1.4.1.637.61.1.35.4.1.20",
    },
    "vsol": {
        "label":     "VSOL V1600D / V160x",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "syrotech": {
        "label":     "Syrotech GPON / EPON",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.21317.1.1.5.1.2.1.6",
        "onu_status":  "1.3.6.1.4.1.21317.1.1.5.1.2.1.5",
        "onu_rx":      "1.3.6.1.4.1.21317.1.1.5.1.2.1.10",
        "onu_tx":      "1.3.6.1.4.1.21317.1.1.5.1.2.1.11",
        "onu_distance": "1.3.6.1.4.1.21317.1.1.5.1.2.1.12",
    },
    "zte": {
        "label":     "ZTE C300 / C320",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.3902.1012.3.28.1.1.5",
        "onu_status":  "1.3.6.1.4.1.3902.1012.3.28.1.1.4",
        "onu_rx":      "1.3.6.1.4.1.3902.1012.3.50.12.1.1.10",
        "onu_tx":      "1.3.6.1.4.1.3902.1012.3.50.12.1.1.14",
        "onu_distance": "1.3.6.1.4.1.3902.1012.3.28.1.1.20",
    },
    "fiberhome": {
        "label":     "Fiberhome AN5516",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.5875.800.3.10.1.1.1.2",
        "onu_status":  "1.3.6.1.4.1.5875.800.3.10.1.1.1.5",
        "onu_rx":      "1.3.6.1.4.1.5875.800.3.10.6.1.1.10",
        "onu_tx":      "1.3.6.1.4.1.5875.800.3.10.6.1.1.11",
        "onu_distance": "1.3.6.1.4.1.5875.800.3.10.1.1.1.20",
    },
    "optilink": {
        "label":     "Optilink GPON",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.34592.1.1.5.1.2.1.6",
        "onu_status":  "1.3.6.1.4.1.34592.1.1.5.1.2.1.5",
        "onu_rx":      "1.3.6.1.4.1.34592.1.1.5.1.2.1.10",
        "onu_tx":      "1.3.6.1.4.1.34592.1.1.5.1.2.1.11",
        "onu_distance": "1.3.6.1.4.1.34592.1.1.5.1.2.1.12",
    },
    # _S45F_VSOL_V1600D — Netlink / Syrotech / CData / VSOL V1600D EPON
    # OLTs. The vsol EPON OID branch is NOT 13.2.1.* — it is 12.1.9.1.*
    # with:
    #   .1.X = PON port number (0..7)
    #   .2.X = vendor-internal status code (5/6/etc)
    #   .3.X = ONU index within PON
    #   .4.X = auth message ("auth success", "deregistered", "ranging")
    #   .5.X = ONU MAC address (canonical identifier)
    # Per-ONU TX/RX power and distance are NOT exposed on this branch
    # for the V1600D; we leave those OIDs empty (the poller will skip).
    # online_keywords list lets the generic parser map vendor-specific
    # status strings to canonical online/offline (case-insensitive
    # substring match).
    "netlink_epon": {
        "label":     "Netlink EPON (VSOL OEM)",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        # _S46A_ VSOL V1600D EPON branch — .5=MAC, .3=PON, .4=auth_msg
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "netlink": {
        "label":     "Netlink (VSOL OEM)",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "syrotech_epon": {
        "label":     "Syrotech EPON (VSOL OEM)",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "cdata_epon": {
        "label":     "CData EPON (VSOL OEM)",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "cdata": {
        "label":     "CData (VSOL OEM)",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
    "vsol_epon": {
        "label":     "VSOL EPON",
        "sys_descr": "1.3.6.1.2.1.1.1.0",
        "sys_uptime": "1.3.6.1.2.1.1.3.0",
        "onu_serial":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_mac":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.5",
        "onu_status":  "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.4",
        "onu_pon":     "1.3.6.1.4.1.37950.1.1.5.12.1.9.1.3",
        "onu_rx":      "",
        "onu_tx":      "",
        "onu_distance": "",
        "online_keywords": ["success", "active", "online", "auth ok", "los_ok"],
    },
}


def _reachable(host: str, timeout: float = 1.0) -> bool:
    if not host:
        return False
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(host)
        return True
    except Exception:
        return False


_VAL_RE = re.compile(r"^(?P<oid>\S+)\s*=\s*(?:\S+:\s*)?(?P<val>.*)$")


def _parse_line(line: str) -> Optional[Tuple[str, str]]:
    m = _VAL_RE.match(line)
    if not m:
        return None
    raw = m.group("val").strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return m.group("oid"), raw


def _snmp_get(host: str, community: str, port: int, oid: str,
              version: str = "v2c", timeout: float = 2.0) -> Optional[str]:
    if not _SNMPGET:
        return None
    cmd = [_SNMPGET, "-v", "2c" if version != "v3" else "3", "-c", community,
           "-On", "-Ovq", "-t", str(int(timeout)), "-r", "0",
           f"{host}:{int(port)}", oid]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 1.5)
        if out.returncode != 0:
            return None
        return out.stdout.strip().strip('"')
    except Exception:
        return None


def _snmp_walk(host: str, community: str, port: int, base_oid: str,
               version: str = "v2c", timeout: float = 3.0
               ) -> List[Tuple[str, str]]:
    if not _SNMPWALK:
        return []
    # _S45F_SNMPWALK_FIX — Remove invalid '-Cr20' flag (that's a snmpbulkwalk
    # option). Normalize OID prefix matching so both '.1.3.6...' (output of
    # snmpwalk -On) and '1.3.6...' (config-supplied base_oid) match. This was
    # silently returning 0 rows for every poll.
    cmd = [_SNMPWALK, "-v", "2c" if version != "v3" else "3", "-c", community,
           "-On", "-t", str(int(timeout)), "-r", "1",
           f"{host}:{int(port)}", base_oid]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 8.0)
        if out.returncode != 0 and not out.stdout.strip():
            return []
        rows: List[Tuple[str, str]] = []
        nbase = base_oid.lstrip(".")
        for line in out.stdout.splitlines():
            p = _parse_line(line)
            if not p:
                continue
            full, val = p
            nfull = full.lstrip(".")
            if not nfull.startswith(nbase):
                continue
            suffix = nfull[len(nbase):].lstrip(".")
            rows.append((suffix, val))
        return rows
    except Exception:
        return []


def poll_real(olt: dict) -> Tuple[bool, Optional[str], dict, list]:
    vendor = (olt.get("vendor") or "").lower()
    profile = VENDOR_PROFILES.get(vendor)
    if not profile:
        return False, f"No SNMP profile for vendor '{vendor}'", {}, []
    if not _SNMP_AVAILABLE:
        return False, ("net-snmp tools not installed (`apt install snmp`) "
                       "— required for live SNMP polling."), {}, []
    host = olt.get("host") or ""
    if not _reachable(host):
        return False, f"Host {host} not resolvable / unreachable", {}, []
    community = olt.get("snmp_community") or "public"
    port = int(olt.get("snmp_port") or 161)
    version = (olt.get("snmp_version") or "v2c").lower()

    sys_descr = _snmp_get(host, community, port, profile["sys_descr"], version)
    if sys_descr is None:
        return False, (f"SNMP timeout against {host}:{port} (community "
                       f"'{community}'). Verify the OLT allows SNMP from "
                       "this server's IP and the community matches."), {}, []

    def _f(oid_key: str, default: float = 0.0) -> float:
        oid = profile.get(oid_key)
        if not oid:
            return default
        v = _snmp_get(host, community, port, oid, version)
        try:
            f = float(v) if v is not None else default
            return f
        except Exception:
            return default

    olt_meta = {
        "status": "online",
        "uptime_sec": int(_f("sys_uptime") / 100),
        "cpu_pct": _f("cpu"),
        "mem_pct": _f("mem"),
        "temp_c": _f("temp"),
        "sys_descr": sys_descr,
    }

    serials  = dict(_snmp_walk(host, community, port, profile["onu_serial"], version))
    statuses = dict(_snmp_walk(host, community, port, profile["onu_status"], version))
    rxs      = dict(_snmp_walk(host, community, port, profile["onu_rx"], version)) if profile.get("onu_rx") else {}
    txs      = dict(_snmp_walk(host, community, port, profile["onu_tx"], version)) if profile.get("onu_tx") else {}
    dists    = dict(_snmp_walk(host, community, port,
                               profile.get("onu_distance") or
                               profile["onu_status"], version)) if profile.get("onu_distance") else {}
    # _S46A_  Separate MAC walk when vendor exposes it (EPON V1600D uses
    # the same OID for canonical id AND mac — both ok).
    macs     = dict(_snmp_walk(host, community, port, profile["onu_mac"], version))                if profile.get("onu_mac") else {}

    # _S45F_PON_INDEX — when the profile defines a separate onu_pon OID
    # (PON-port lookup), use it instead of guessing from the OID suffix.
    pons_lookup = {}
    if profile.get("onu_pon"):
        pons_lookup = dict(_snmp_walk(host, community, port,
                                       profile["onu_pon"], version))
    onus: List[Dict[str, Any]] = []
    # _S46A_  When the vendor exposes a separate PON-port OID, treat that
    # value as the real PON and number ONUs 1..N within each PON. The
    # numeric SNMP suffix is just a table key, not an ONU index.
    _per_pon_counter: Dict[int, int] = {}
    for suffix, serial in serials.items():
        if pons_lookup:
            try:
                pon = int(pons_lookup.get(suffix, "1"))
            except Exception:
                pon = 1
            # 1-based PON labelling — convert any 0-based vendor index.
            pon_label = pon + 1 if pon == 0 or (pon < 8 and "0" in str(pons_lookup.get(suffix, "")).strip()[:1]) else pon
            pon = pon_label
            _per_pon_counter[pon] = _per_pon_counter.get(pon, 0) + 1
            idx = _per_pon_counter[pon]
        else:
            parts = suffix.split(".")
            try:
                pon = int(parts[0])
                idx = int(parts[1]) if len(parts) > 1 else int(parts[0])
            except Exception:
                pon, idx = 1, len(onus) + 1
        st_raw = (statuses.get(suffix) or "").strip()
        kws = profile.get("online_keywords") or []
        st_low = st_raw.lower()
        if any(kw.lower() in st_low for kw in kws):
            status = "online"
        elif st_raw and ("1" in st_raw[:2] or "online" in st_low or "active" in st_low):
            status = "online"
        else:
            status = "offline"
        try:
            rx = float(rxs.get(suffix) or 0)
            if rx and abs(rx) > 100:
                rx = rx / 100.0
        except Exception:
            rx = None
        try:
            tx = float(txs.get(suffix) or 0)
            if tx and abs(tx) > 100:
                tx = tx / 100.0
        except Exception:
            tx = None
        try:
            dist = int(dists.get(suffix) or 0)
        except Exception:
            dist = 0
        onus.append({
            "pon_port_index": pon, "onu_index": idx,
            "serial": str(serial)[:64], "mac": "",
            "vendor": vendor, "model": profile.get("label", ""),
            "rx_power": rx, "tx_power": tx, "distance_m": dist,
            "status": status, "uptime_sec": 0,
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime()),
        })
    return True, None, olt_meta, onus
