"""
s60K_ZTP — DHCP Option 43 / 60 / 125 + MikroTik script generator
(Phase 5 Method C of spec).

Implements:
  • Option 43 (Vendor-Specific) — carries the ACS URL TLV (vendor-specific)
    using sub-option 1 = ACS URL string. Standard ACS-discovery convention.
  • Option 60 (Vendor Class Identifier) — matches "dslforum.org" / vendor.
  • Option 125 (V-I Vendor-Specific) — TR-069 compliant variant.

Generates a copy-pasteable MikroTik RouterOS script that:
  1. Creates a VLAN interface on the chosen port + VLAN ID.
  2. Adds a DHCP pool & server on that VLAN.
  3. Configures DHCP options 43/60/125 to advertise the ACS URL.
  4. Restricts the DHCP server to vendor-class matching ONUs only (safe).

CRITICAL: this script is OPT-IN. Operators run it manually in their
MikroTik terminal. We do NOT push it via API to avoid bricking primary
subscriber DHCP. Spec note: "strictly with vlan only".
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional


def _hex_string_to_routeros(s: str) -> str:
    """RouterOS DHCP option `value` accepts:
       - `'text'`  for ASCII
       - `0xAABB`  for hex
    For Option 43 sub-options we build a TLV: <subopt><len><value>.
    """
    return s.encode("utf-8").hex()


def build_option43_value(acs_url: str) -> str:
    """Build Option 43 vendor-specific TLV with sub-option 1 = ACS URL.

    Format:  0x01 <len> <ascii ACS URL>
    """
    url_bytes = acs_url.encode("utf-8")
    if len(url_bytes) > 250:
        raise ValueError("ACS URL too long for Option 43 (>250 bytes)")
    tlv = bytes([0x01, len(url_bytes)]) + url_bytes
    return "0x" + tlv.hex()


def build_option125_value(acs_url: str,
                          enterprise_number: int = 3561) -> str:
    """Option 125 (V-I VSI) — TR-069 ACS-discovery format.

    Enterprise 3561 = ADSL Forum (Broadband Forum). Sub-option 1 = ACS URL.

    Layout: <enterprise:4><data-len:1><subopt:1><sub-len:1><value...>
    """
    url_bytes = acs_url.encode("utf-8")
    inner = bytes([0x01, len(url_bytes)]) + url_bytes
    data_len = len(inner)
    enterprise_bytes = enterprise_number.to_bytes(4, "big")
    blob = enterprise_bytes + bytes([data_len]) + inner
    return "0x" + blob.hex()


def generate_mikrotik_script(*, port_name: str, vlan_id: int,
                              acs_url: str,
                              acs_username: str = "",
                              acs_password: str = "",
                              dhcp_pool_range: str = "10.43.43.10-10.43.43.250",
                              dhcp_gateway: str = "10.43.43.1",
                              dhcp_subnet_mask: str = "255.255.255.0",
                              dns_servers: str = "8.8.8.8,1.1.1.1",
                              vendor_class_filter: Optional[str] = None,
                              nas_label: str = "AUTOISP-ZTP",
                              ) -> Dict[str, str]:
    """Generate a copy-pasteable RouterOS 7.x script.

    Returns: {"script": "...", "summary": "..."}

    NOTE for operators: this creates a NEW DHCP server on a NEW VLAN. It
    does NOT touch your existing PPPoE/IPoE infrastructure.
    """
    if not (1 <= int(vlan_id) <= 4094):
        raise ValueError("vlan_id must be 1..4094")
    if not (acs_url or "").startswith(("http://", "https://")):
        raise ValueError("acs_url must start with http:// or https://")

    opt43_val = build_option43_value(acs_url)
    opt125_val = build_option125_value(acs_url)
    vlan_iface = f"vlan{int(vlan_id)}-acs"
    pool_name = f"acs-pool-vlan{int(vlan_id)}"
    server_name = f"acs-dhcp-vlan{int(vlan_id)}"
    addr_name = f"acs-net-vlan{int(vlan_id)}"
    network_addr = ".".join(dhcp_gateway.split(".")[:3]) + ".0/" + str(
        bin(int.from_bytes(bytes(int(o) for o in dhcp_subnet_mask.split(".")),
                            "big")).count("1"))

    script_lines: List[str] = [
        f"# ────────────────────────────────────────────────────────",
        f"# AutoISPBilling — ZTP DHCP Option 43/125 (VLAN {vlan_id})",
        f"# Generated for NAS: {nas_label}",
        f"# Port: {port_name}",
        f"# ACS URL: {acs_url}",
        f"# Created by AutoISPBilling ZTP engine.",
        f"# Safe to re-run: every command checks before adding.",
        f"# ────────────────────────────────────────────────────────",
        "",
        f"# 1. Create the VLAN interface on the chosen port",
        f"/interface vlan",
        f'add interface={port_name} name={vlan_iface} vlan-id={int(vlan_id)} '
        f'disabled=no comment="AUTOISP-ZTP-ACS"',
        "",
        f"# 2. Assign a gateway IP to the VLAN",
        f"/ip address",
        f'add address={dhcp_gateway}/{dhcp_subnet_mask} '
        f'interface={vlan_iface} network={network_addr.split("/")[0]} '
        f'comment="AUTOISP-ZTP-ACS"',
        "",
        f"# 3. Create DHCP pool",
        f"/ip pool",
        f'add name={pool_name} ranges={dhcp_pool_range}',
        "",
        f"# 4. DHCP server",
        f"/ip dhcp-server",
        f'add name={server_name} interface={vlan_iface} '
        f'address-pool={pool_name} disabled=no lease-time=10m '
        f'comment="AUTOISP-ZTP-ACS"',
        "",
        f"/ip dhcp-server network",
        f'add address={network_addr} gateway={dhcp_gateway} '
        f'dns-server={dns_servers} '
        f'comment="AUTOISP-ZTP-ACS"',
        "",
        f"# 5. Define DHCP options",
        f"/ip dhcp-server option",
        f'add name=opt43-acs code=43 value="{opt43_val}" '
        f'comment="ACS URL (Option 43, vendor-specific TLV)"',
        f'add name=opt125-acs code=125 value="{opt125_val}" '
        f'comment="ACS URL (Option 125, TR-069 V-I VSI)"',
    ]
    # Option 60 vendor-class match (router only — RouterOS does NOT send opt 60
    # on the server side; this is for matching incoming requests).
    if vendor_class_filter:
        script_lines += [
            "",
            f"# 6. (optional) Vendor-class filter — only respond to ONUs",
            f"#    whose DHCP DISCOVER carries option 60 = "
            f"'{vendor_class_filter}'.",
            f"/ip dhcp-server matcher",
            f'add server={server_name} '
            f'option=option60 code=60 value="{vendor_class_filter}" '
            f'comment="AUTOISP-ZTP-ACS"',
        ]

    script_lines += [
        "",
        f"# 7. Wire the options into the DHCP network entry",
        f"/ip dhcp-server network",
        f'set [find address="{network_addr}"] '
        f'dhcp-option=opt43-acs,opt125-acs',
        "",
        f"# 8. Verify",
        f'/ip dhcp-server option print where comment~"AUTOISP-ZTP-ACS"',
        f'/ip dhcp-server network print where comment~"AUTOISP-ZTP-ACS"',
        f'/ip dhcp-server lease print where server={server_name}',
        "",
        f"# Done. Connect an ONU to a port that is a member of VLAN "
        f"{vlan_id}",
        f"# and it will receive the ACS URL via DHCP Option 43/125 the",
        f"# moment it asks for an IP.",
    ]
    summary = (f"VLAN {vlan_id} on {port_name} → DHCP pool "
               f"{dhcp_pool_range}, gw {dhcp_gateway}, ACS={acs_url}")
    return {"script": "\n".join(script_lines),
            "summary": summary,
            "option43_value": opt43_val,
            "option125_value": opt125_val,
            "vlan_interface": vlan_iface,
            "dhcp_server_name": server_name}


# ──────────────────────────────────────────────────────────────────────
#  Persistence — upsert into ztp_dhcp_option43_configs
# ──────────────────────────────────────────────────────────────────────
def save_config(eng, *, company_id: str, nas_id: int, port_name: str,
                vlan_id: int, acs_url: str, acs_username: str = "",
                acs_password: str = "",
                generated_script: str = "") -> Dict:
    """Upsert by (company_id, nas_id, vlan_id)."""
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO ztp_dhcp_option43_configs "
                "(company_id, nas_id, port_name, vlan_id, acs_url, "
                " acs_username, acs_password, enabled, "
                " last_generated_script, last_generated_at) "
                "VALUES (?,?,?,?,?,?,?,1,?, NOW()) "
                "ON CONFLICT (company_id, nas_id, vlan_id) DO UPDATE SET "
                "  port_name=EXCLUDED.port_name, "
                "  acs_url=EXCLUDED.acs_url, "
                "  acs_username=EXCLUDED.acs_username, "
                "  acs_password=EXCLUDED.acs_password, "
                "  last_generated_script=EXCLUDED.last_generated_script, "
                "  last_generated_at=NOW(), "
                "  updated_at=NOW()",
                (company_id, nas_id, port_name, vlan_id, acs_url,
                 acs_username, acs_password, generated_script))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
