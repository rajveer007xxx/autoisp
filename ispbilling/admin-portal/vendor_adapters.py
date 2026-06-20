"""
_S40l_ — Vendor adapter for PON port enable / disable

Supports:
 • mock / unknown  → DB-only (for test OLTs)
 • huawei          → SSH CLI:  interface gpon 0/N/M ; shutdown / undo shutdown
 • zte             → SSH CLI:  interface gpon-olt_0/N/M ; shutdown / no shutdown
 • bdcom           → SSH CLI:  interface EPON0/N ; shutdown / no shutdown
 • optilink / syrotech / netlink / generic-snmp
                    → SNMPv2c SET ifAdminStatus = 1 (up) / 2 (down)

Reads credentials from the `olts` table:
  mgmt_host, mgmt_port, mgmt_user, mgmt_pass,
  snmp_community, snmp_version, snmp_port.

Returns: { ok: bool, details: str, cmd_trace: [..] }
"""
from __future__ import annotations
import socket
import time
from typing import Optional, Dict, Any, Tuple, List


# ─── SSH CLI adapter ────────────────────────────────────────────────────
def _ssh_run(host: str, port: int, user: str, passwd: str,
             commands: List[str], timeout: float = 12.0) -> Tuple[bool, str]:
    """Open an interactive SSH channel, send each command, return buffer."""
    try:
        import paramiko
    except Exception as e:                                     # pragma: no cover
        return False, f"paramiko missing: {e}"
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    buf = ""
    try:
        cli.connect(hostname=host, port=port or 22,
                    username=user, password=passwd,
                    timeout=timeout, banner_timeout=timeout,
                    auth_timeout=timeout, look_for_keys=False,
                    allow_agent=False)
        chan = cli.invoke_shell(width=180, height=40)
        chan.settimeout(timeout)
        time.sleep(0.6)
        while chan.recv_ready(): buf += chan.recv(65535).decode(errors="replace")
        for cmd in commands:
            chan.send(cmd + "\n")
            time.sleep(0.7)
            t_end = time.time() + timeout
            while time.time() < t_end:
                if chan.recv_ready():
                    buf += chan.recv(65535).decode(errors="replace")
                    if buf.rstrip().endswith(("#", ">", "$")): break
                else:
                    time.sleep(0.15)
        cli.close()
        return True, buf
    except (socket.timeout, OSError) as e:
        return False, f"connect error: {e}"
    except Exception as e:                                    # pragma: no cover
        return False, f"ssh error: {e}"


# ─── SNMP adapter (ifAdminStatus set) ──────────────────────────────────
def _snmp_set_admin_status(host: str, community: str, if_index: int,
                            enable: bool, port: int = 161) -> Tuple[bool, str]:
    """SNMPv2c SET on ifAdminStatus (.1.3.6.1.2.1.2.2.1.7.<idx>).
       Value 1 = up, 2 = down."""
    try:
        from pysnmp.hlapi import (SnmpEngine, CommunityData, UdpTransportTarget,
                                    ContextData, ObjectType, ObjectIdentity,
                                    Integer32, setCmd)
    except Exception as e:                                    # pragma: no cover
        return False, f"pysnmp missing: {e}"
    oid = f"1.3.6.1.2.1.2.2.1.7.{if_index}"
    value = Integer32(1 if enable else 2)
    try:
        iterator = setCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, port or 161), timeout=5, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid), value))
        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            return False, f"snmp indication: {errorIndication}"
        if errorStatus:
            return False, f"snmp error: {errorStatus.prettyPrint()}"
        return True, f"ifAdminStatus.{if_index}={value}"
    except Exception as e:                                    # pragma: no cover
        return False, f"snmp exception: {e}"


# ─── Public dispatch ────────────────────────────────────────────────────
def dispatch_pon_toggle(olt_row: Dict[str, Any], port_index: int,
                         enable: bool) -> Dict[str, Any]:
    """Entry point.  `olt_row` is the full dict from the olts table."""
    vendor = (olt_row.get("vendor") or "mock").lower()
    host = olt_row.get("host") or olt_row.get("mgmt_host")
    user = olt_row.get("mgmt_user") or olt_row.get("username")
    passwd = olt_row.get("mgmt_pass") or olt_row.get("password")
    mgmt_port = int(olt_row.get("mgmt_port") or 22)
    comm = olt_row.get("snmp_community") or "public"
    snmp_port = int(olt_row.get("snmp_port") or 161)
    trace = []

    # Mock / no host configured → DB only
    if vendor in ("mock", "test", "demo") or not host:
        return {"ok": True, "mode": "mock",
                 "details": "DB-only flip (mock OLT or no host configured)",
                 "cmd_trace": trace}

    # Huawei (MA5600T / MA5800)
    if vendor in ("huawei",):
        if not (user and passwd):
            return {"ok": False, "mode": "huawei",
                     "details": "mgmt_user / mgmt_pass missing on OLT"}
        # Port mapping: assume gpon 0/1/{port_index} for a single-slot OLT
        iface = f"0/1/{port_index}"
        cmds = ["enable", "config", f"interface gpon {iface}",
                ("undo shutdown" if enable else "shutdown"),
                "quit", "quit", "save"]
        trace.extend(cmds)
        ok, buf = _ssh_run(host, mgmt_port, user, passwd, cmds)
        return {"ok": ok, "mode": "huawei-ssh", "details": buf[-1200:],
                 "cmd_trace": trace}

    # ZTE (C320 / C300 / C600)
    if vendor in ("zte",):
        if not (user and passwd):
            return {"ok": False, "mode": "zte",
                     "details": "mgmt_user / mgmt_pass missing on OLT"}
        iface = f"1/1/{port_index}"
        cmds = ["enable", "configure terminal",
                f"interface gpon-olt_{iface}",
                ("no shutdown" if enable else "shutdown"),
                "exit", "exit", "write"]
        trace.extend(cmds)
        ok, buf = _ssh_run(host, mgmt_port, user, passwd, cmds)
        return {"ok": ok, "mode": "zte-ssh", "details": buf[-1200:],
                 "cmd_trace": trace}

    # BDCOM / VSOL / generic EPON
    if vendor in ("bdcom", "vsol", "epon-generic"):
        if not (user and passwd):
            return {"ok": False, "mode": vendor,
                     "details": "mgmt_user / mgmt_pass missing on OLT"}
        cmds = ["enable", "config", f"interface EPON0/{port_index}",
                ("no shutdown" if enable else "shutdown"),
                "exit", "exit", "write"]
        trace.extend(cmds)
        ok, buf = _ssh_run(host, mgmt_port, user, passwd, cmds)
        return {"ok": ok, "mode": f"{vendor}-ssh", "details": buf[-1200:],
                 "cmd_trace": trace}

    # Fallback: SNMP on ifAdminStatus (if_index assumed = port_index; tune
    # with the `snmp_ifindex_base` prop if needed on the OLT row)
    base = int(olt_row.get("snmp_ifindex_base") or 0)
    if_index = base + port_index if base else port_index
    ok, details = _snmp_set_admin_status(host, comm, if_index, enable,
                                          port=snmp_port)
    return {"ok": ok, "mode": f"snmp-v2c ({vendor})",
             "details": details, "cmd_trace":
             [f"SET ifAdminStatus.{if_index} = {'up' if enable else 'down'}"]}
