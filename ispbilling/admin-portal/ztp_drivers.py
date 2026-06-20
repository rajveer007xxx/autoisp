"""
s60K_ZTP — OLT vendor driver abstraction layer (Phase 4 of spec).

Goal: a normalized API every OLT driver implements. Frontend / orchestrator
only ever talks to this base class. Vendor quirks stay inside concrete
drivers.

Each driver implements as many of these methods as the underlying hardware
supports. Unimplemented methods return a structured "unsupported" response
so the orchestrator can fall back to alternate paths (OMCI → ACS-push →
DHCP43 etc.).

The base class also defines the **DriverResult** envelope every method
returns. Callers should NEVER raise — they should inspect `.ok`.

Conventions
-----------
- All methods accept `olt` (dict from `olts` table) as first arg.
- All methods return `DriverResult` instances. NEVER raise.
- Driver classes are stateless. Connection state lives in olt_telnet_pool.
- Use `from olt_telnet_pool import telnet_session` for CLI work.
- Use `from snmp_helpers import snmp_walk/get` for SNMP work.

Adding a new vendor
-------------------
1. Subclass BaseOLTDriver.
2. Override `vendor_id` and `display_name`.
3. Implement methods. Mark unimplemented ones as `_unsupported(...)`.
4. Register in `_DRIVER_REGISTRY` at module bottom.
"""
from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


_LOG = "/var/log/autoispbilling/ztp-driver.log"
try:
    os.makedirs(os.path.dirname(_LOG), exist_ok=True)
except Exception:
    pass


def _log(line: str) -> None:
    try:
        with open(_LOG, "a") as fh:
            fh.write(f"[{int(time.time())}] {line}\n")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
#  Result envelope
# ──────────────────────────────────────────────────────────────────────
@dataclass
class DriverResult:
    """Uniform return envelope. ALL driver methods return this. Never raise."""
    ok: bool
    method: str = ""            # "cli" | "snmp" | "omci" | "unsupported"
    vendor: str = ""
    output: str = ""            # truncated command output for audit
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
#  Base driver
# ──────────────────────────────────────────────────────────────────────
class BaseOLTDriver:
    """Abstract base. Subclasses override methods they support."""

    vendor_id: str = "generic"
    display_name: str = "Generic OLT"
    # CLI prompt hints used by the telnet pool to detect prompts.
    cli_prompt_hints: List[str] = ["#", ">"]

    # ---- ONU discovery ----------------------------------------------
    def discover_onus(self, olt: Dict) -> DriverResult:
        """Return list of unauthorized + authorized ONUs visible to the OLT.

        data = {"onus": [
            {pon_port, onu_index, serial, mac, vendor, model, firmware,
             status, rx_dbm, tx_dbm, distance_m, loid}, ...
        ]}
        """
        return self._unsupported("discover_onus")

    # ---- ONU lifecycle ----------------------------------------------
    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        """Bind a discovered ONU to a specific pon/index, name it, attach a
        line/service profile, and configure default TCONT/GEMPORT/serviceport."""
        return self._unsupported("authorize_onu")

    def delete_onu(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        return self._unsupported("delete_onu")

    def create_tcont(self, olt: Dict, *, pon_port: int, onu_index: int,
                     tcont_id: int, dba_profile: str) -> DriverResult:
        return self._unsupported("create_tcont")

    def create_gemport(self, olt: Dict, *, pon_port: int, onu_index: int,
                       gem_id: int, tcont_id: int) -> DriverResult:
        return self._unsupported("create_gemport")

    def create_service_port(self, olt: Dict, *, pon_port: int, onu_index: int,
                            vlan_id: int, gem_id: int,
                            user_vlan: Optional[int] = None) -> DriverResult:
        return self._unsupported("create_service_port")

    def set_vlan(self, olt: Dict, *, pon_port: int, onu_index: int,
                 vlan_id: int) -> DriverResult:
        return self._unsupported("set_vlan")

    def set_management_vlan(self, olt: Dict, *, pon_port: int,
                            onu_index: int, mgmt_vlan: int) -> DriverResult:
        return self._unsupported("set_management_vlan")

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        return self._unsupported("set_acs_url_if_supported")

    def set_wan_profile_if_supported(self, olt: Dict, *, pon_port: int,
                                     onu_index: int, mode: str = "pppoe",
                                     pppoe_user: str = "",
                                     pppoe_password: str = "",
                                     vlan: Optional[int] = None,
                                     bind_lan: str = "lan1 lan2 lan3 lan4"
                                     ) -> DriverResult:
        return self._unsupported("set_wan_profile_if_supported")

    def reboot_onu(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        return self._unsupported("reboot_onu")

    def factory_reset_onu(self, olt: Dict, *, pon_port: int,
                          onu_index: int) -> DriverResult:
        return self._unsupported("factory_reset_onu")

    def get_onu_status(self, olt: Dict, *, pon_port: int,
                       onu_index: int) -> DriverResult:
        return self._unsupported("get_onu_status")

    def get_signal(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        return self._unsupported("get_signal")

    def get_onu_info(self, olt: Dict, *, pon_port: int,
                     onu_index: int) -> DriverResult:
        return self._unsupported("get_onu_info")

    # ---- helpers (concrete) -----------------------------------------
    def _unsupported(self, op: str) -> DriverResult:
        return DriverResult(ok=False, method="unsupported",
                            vendor=self.vendor_id,
                            error=f"{op} not implemented for {self.vendor_id}")

    def _log(self, olt: Dict, op: str, msg: str) -> None:
        _log(f"{self.vendor_id} olt={olt.get('id')}@{olt.get('host')} "
             f"op={op} {msg}")


# ──────────────────────────────────────────────────────────────────────
#  Concrete: VSOL / NETLINK EPON (V1600D and clones)
# ──────────────────────────────────────────────────────────────────────
class VSOLNetlinkEPONDriver(BaseOLTDriver):
    """Production-ready driver for V1600D-style EPON OLTs.

    Tested on:
      - NETLINK V1600D / D2 / EPON
      - VSOL V1600D-MINI
      - their relabelled clones (BDCOM EPON OEMs)
    """

    vendor_id = "vsol_netlink_epon"
    display_name = "VSOL/NETLINK EPON (V1600D family)"

    def discover_onus(self, olt: Dict) -> DriverResult:
        """Walks all configured PON ports and returns ONU list."""
        from olt_telnet_pool import telnet_session
        onus: List[Dict] = []
        try:
            with telnet_session(olt) as ts:
                # Iterate 16 PON ports (V1600D series)
                for pon in range(1, 17):
                    try:
                        ts.enter_pon(pon)
                    except Exception:
                        continue
                    out = ts.send("show onu information", wait=2.0,
                                  iters=10) or ""
                    onus.extend(self._parse_onu_information(out, pon))
                    ts.exit_config()
            return DriverResult(ok=True, method="cli", vendor=self.vendor_id,
                                output=f"{len(onus)} ONUs across PONs",
                                data={"onus": onus})
        except Exception as e:
            return DriverResult(ok=False, method="cli", vendor=self.vendor_id,
                                error=str(e)[:300])

    @staticmethod
    def _parse_onu_information(text: str, pon_port: int) -> List[Dict]:
        """Parse VSOL `show onu information` output.

        Example line (varies by firmware):
          ONU 1   :  MAC 00:11:22:33:44:55  Type EPON  Status Auth_success
          Status: connected
        """
        out = []
        for ln in text.splitlines():
            up = ln.strip()
            if not up.lower().startswith("onu "):
                continue
            try:
                parts = up.split()
                onu_idx = int(parts[1].rstrip(":"))
                mac = ""
                status = ""
                for i, tok in enumerate(parts):
                    if tok.lower() == "mac" and i + 1 < len(parts):
                        mac = parts[i + 1]
                    if tok.lower() == "status" and i + 1 < len(parts):
                        status = parts[i + 1]
                out.append({
                    "pon_port": pon_port,
                    "onu_index": onu_idx,
                    "serial": mac,            # EPON uses MAC as serial
                    "mac": mac,
                    "vendor": "Realtek",
                    "model": "",
                    "firmware": "",
                    "status": status,
                    "rx_dbm": None,
                    "tx_dbm": None,
                    "distance_m": None,
                    "loid": "",
                })
            except (ValueError, IndexError):
                continue
        return out

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        """Bind an ONU to (pon, index), set description and the bare-min
        line config that lets it carry user traffic + TR-069."""
        from olt_telnet_pool import telnet_session
        desc = (profile.get("description") or
                f"AUTOISP-CUST-{profile.get('customer_id', '')}")[:31]
        try:
            with telnet_session(olt) as ts:
                ts.enter_pon(int(pon_port))
                # bind by MAC (EPON style: onu N type epon mac AA:BB:..)
                cmds = [
                    f"onu {int(onu_index)} type netlink mac {serial}",
                    f"onu {int(onu_index)} description {desc}",
                ]
                joined = []
                for c in cmds:
                    out = ts.send(c, wait=1.2, iters=6) or ""
                    joined.append(f"$ {c}\n{out}")
                ts.exit_config()
            return DriverResult(ok=True, method="cli", vendor=self.vendor_id,
                                output="\n".join(joined)[-2000:])
        except Exception as e:
            return DriverResult(ok=False, method="cli", vendor=self.vendor_id,
                                error=str(e)[:300])

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        # Delegate to the existing implementation (already battle-tested).
        from olt_telnet_actions import push_tr069_acs
        r = push_tr069_acs(olt, int(pon_port), int(onu_index),
                           acs_url=acs_url,
                           acs_username=acs_username,
                           acs_password=acs_password,
                           inform_interval=inform_interval)
        return DriverResult(ok=bool(r.get("ok")),
                            method=r.get("method", "cli"),
                            vendor=self.vendor_id,
                            output=r.get("output", ""),
                            error=r.get("error", ""),
                            data={"note": r.get("note", "")})

    def set_wan_profile_if_supported(self, olt: Dict, *, pon_port: int,
                                     onu_index: int, mode: str = "pppoe",
                                     pppoe_user: str = "",
                                     pppoe_password: str = "",
                                     vlan: Optional[int] = None,
                                     bind_lan: str = "lan1 lan2 lan3 lan4"
                                     ) -> DriverResult:
        from olt_telnet_actions import push_wan_pppoe_with_tr069
        r = push_wan_pppoe_with_tr069(olt, int(pon_port), int(onu_index),
                                      pppoe_user=pppoe_user,
                                      pppoe_password=pppoe_password,
                                      bind_lan_ports=bind_lan)
        return DriverResult(ok=bool(r.get("ok")),
                            method=r.get("method", "cli"),
                            vendor=self.vendor_id,
                            output=r.get("output", ""),
                            error=r.get("error", ""))

    def reboot_onu(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        # _S60K_REBOOT_VIA_FACTORY — V1600D firmware exposes no 
        # verb at pon-config scope.  is the only command
        # the firmware accepts that triggers an ONU restart. The ONU
        # comes back up with empty config; the OLT's OAM heartbeat
        # re-pushes our running-config within ~60s, which is exactly the
        # behavior ZTP wants for a clean kick of the CWMP daemon.
        from olt_telnet_pool import telnet_session
        try:
            with telnet_session(olt) as ts:
                ts.enter_pon(int(pon_port))
                out = ts.send(
                    f"onu {int(onu_index)} pri factory_reset",
                    wait=1.8, iters=8) or ""
                ts.exit_config()
            ok = ("doesn't exist" not in out.lower()
                  and "% unknown" not in out.lower()
                  and "% invalid" not in out.lower())
            return DriverResult(ok=ok, method="cli",
                                vendor=self.vendor_id,
                                output=out[-500:])
        except Exception as e:
            return DriverResult(ok=False, method="cli",
                                vendor=self.vendor_id,
                                error=str(e)[:300])

    def factory_reset_onu(self, olt: Dict, *, pon_port: int,
                          onu_index: int) -> DriverResult:
        from olt_telnet_pool import telnet_session
        try:
            with telnet_session(olt) as ts:
                ts.enter_pon(int(pon_port))
                out = ts.send(f"onu {int(onu_index)} restore_factory",
                              wait=1.5, iters=6) or ""
                ts.exit_config()
            return DriverResult(ok=True, method="cli", vendor=self.vendor_id,
                                output=out[-500:])
        except Exception as e:
            return DriverResult(ok=False, method="cli", vendor=self.vendor_id,
                                error=str(e)[:300])

    def get_onu_status(self, olt: Dict, *, pon_port: int,
                       onu_index: int) -> DriverResult:
        from olt_telnet_pool import telnet_session
        try:
            with telnet_session(olt) as ts:
                ts.enter_pon(int(pon_port))
                # `show running-config onu N` is the only universally
                # supported per-ONU query on V1600D-family firmware.
                out = ts.send(
                    f"show running-config onu {int(onu_index)}",
                    wait=2.0, iters=8) or ""
                ts.exit_config()
            ok = ("doesn't exist" not in out.lower()
                  and "% unknown" not in out.lower()
                  and "% invalid" not in out.lower()
                  and len(out.strip()) > 50)
            return DriverResult(ok=ok, method="cli", vendor=self.vendor_id,
                                output=out[-2000:],
                                data={"raw": out})
        except Exception as e:
            return DriverResult(ok=False, method="cli", vendor=self.vendor_id,
                                error=str(e)[:300])


# ──────────────────────────────────────────────────────────────────────
#  Vendor scaffolds — published CLI documented but not field-tested.
#  Each method is implemented best-effort from vendor docs (see notes).
#  Operators should review actual command output and fine-tune.
# ──────────────────────────────────────────────────────────────────────
class _GenericTelnetCliMixin:
    """Helpers shared by the CLI-based scaffolds below."""

    def _run_cmds(self, olt: Dict, cmds: List[str],
                  pon_port: Optional[int] = None,
                  enter_config: bool = True) -> DriverResult:
        from olt_telnet_pool import telnet_session
        joined: List[str] = []
        try:
            with telnet_session(olt) as ts:
                if pon_port is not None:
                    try:
                        ts.enter_pon(int(pon_port))
                    except Exception:
                        pass
                for c in cmds:
                    out = ts.send(c, wait=1.4, iters=6) or ""
                    joined.append(f"$ {c}\n{out}")
                try:
                    ts.exit_config()
                except Exception:
                    pass
            full = "\n".join(joined)
            return DriverResult(ok=True, method="cli",
                                vendor=getattr(self, "vendor_id", "?"),
                                output=full[-2000:])
        except Exception as e:
            return DriverResult(ok=False, method="cli",
                                vendor=getattr(self, "vendor_id", "?"),
                                error=str(e)[:300])


class HuaweiGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """Huawei MA5800-X / MA5608T / SmartAX GPON.
    CLI reference: Huawei OptiX OSN MA5800 Configuration Guide V100R022.
    """
    vendor_id = "huawei_gpon"
    display_name = "Huawei MA5800/MA5608T GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        # `display ont autofind all` returns unauth ONUs.
        # Caller parses the output downstream; for now we just stream it.
        return self._run_cmds(olt, ["enable", "config",
                                    "display ont autofind all"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        # interface gpon 0/<frame>/<slot> ; ont add <port> <onu_idx> sn-auth
        sn = serial.upper().replace(":", "")
        return self._run_cmds(olt, [
            "enable", "config",
            f"interface gpon 0/{pon_port}",
            f"ont add {pon_port} {onu_index} sn-auth {sn} omci ont-lineprofile-name "
            f"{profile.get('line_profile','LINE-default')} ont-srvprofile-name "
            f"{profile.get('srv_profile','SRV-default')}",
        ])

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        # tr069-server-profile <name> ; acs <url> ; provision-code <usr/pwd>
        return self._run_cmds(olt, [
            "enable", "config",
            "tr069-server-profile profile-name autoisp",
            f"tr069-server-profile add profile-name autoisp acs {acs_url}",
            f"tr069-server-profile mod profile-name autoisp acs-username "
            f"{acs_username} acs-password {acs_password}",
        ])

    def reboot_onu(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "config",
            f"interface gpon 0/{pon_port}",
            f"ont reset {pon_port} {onu_index}",
        ])

    def factory_reset_onu(self, olt: Dict, *, pon_port: int,
                          onu_index: int) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "config",
            f"interface gpon 0/{pon_port}",
            f"ont configuration-restore {pon_port} {onu_index}",
        ])


class ZTEGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """ZTE C300/C320/C600 GPON.
    CLI reference: ZTE ZXA10 C320 Optical Access Convergence Product
    Configuration Guide V2.1.
    """
    vendor_id = "zte_gpon"
    display_name = "ZTE C300/C320/C600 GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "configure terminal",
                                    "show gpon onu uncfg"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        sn = serial.upper()
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"interface gpon-olt_1/1/{pon_port}",
            f"onu {onu_index} type ZTEG-{profile.get('model','F660')} sn {sn}",
        ])

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"pon-onu-mng gpon-onu_1/1/{pon_port}:{onu_index}",
            f"tr069-management 1 acs {acs_url}",
            f"tr069-management 1 inform-interval {inform_interval}",
            f"tr069-management 1 acs-username {acs_username} "
            f"acs-password {acs_password}",
        ])

    def reboot_onu(self, olt: Dict, *, pon_port: int,
                   onu_index: int) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"pon-onu-mng gpon-onu_1/1/{pon_port}:{onu_index}",
            "reboot",
        ])


class BDCOMGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """BDCOM GP3600 / P3310 GPON.
    CLI reference: BDCOM GPON OLT CLI Manual V2.5.
    """
    vendor_id = "bdcom_gpon"
    display_name = "BDCOM GP3600/P3310 GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "show pon onu uncfg-list"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "config",
            f"interface epon0/{pon_port}",
            f"epon bind-onu sn {serial.upper()} {onu_index}",
        ])

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "config",
            f"interface epon0/{pon_port}",
            f"epon onu {onu_index} ctc tr069 acs-url {acs_url}",
            f"epon onu {onu_index} ctc tr069 acs-username {acs_username}",
            f"epon onu {onu_index} ctc tr069 acs-password {acs_password}",
            f"epon onu {onu_index} ctc tr069 inform {inform_interval}",
        ])


class CDataGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """C-Data FD16xx GPON.
    CLI reference: C-Data 16xx series OLT CLI manual V3.0.
    """
    vendor_id = "cdata_gpon"
    display_name = "C-Data FD16xx GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "configure terminal",
                                    "show gpon onu-information uncfg"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"interface gpon 0/{pon_port}",
            f"onu add sn {serial.upper()} onu-id {onu_index}",
        ])


class FiberHomeGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """FiberHome AN5516 / AN6000 GPON.
    CLI reference: FiberHome AN5516 OLT Configuration Manual V8.0.
    """
    vendor_id = "fiberhome_gpon"
    display_name = "FiberHome AN5516/AN6000 GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "config",
                                    "show discovery slot 1 link " +
                                    "all"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "config",
            "cd onu",
            f"add onu slot 1 pon {pon_port} ont {onu_index} authtype "
            f"phyid auth-value {serial.upper()}",
        ])


class OptilinkGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """Optilink GPON (rebrand of various Realtek/CIG references)."""
    vendor_id = "optilink_gpon"
    display_name = "Optilink GPON"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "show gpon discover-onu"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"interface gpon 0/{pon_port}",
            f"ont add ont-id {onu_index} sn {serial.upper()}",
        ])


class SyrotechGPONDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """Syrotech SY-GPON-1408/1416 (Realtek-based)."""
    vendor_id = "syrotech_gpon"
    display_name = "Syrotech SY-GPON 1408/1416"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "show onu auth-pending"])

    def authorize_onu(self, olt: Dict, *, pon_port: int, onu_index: int,
                      serial: str, profile: Dict) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"interface gpon-olt 0/{pon_port}",
            f"onu add sn {serial.upper()} onu-id {onu_index}",
        ])

    def set_acs_url_if_supported(self, olt: Dict, *, pon_port: int,
                                 onu_index: int, acs_url: str,
                                 acs_username: str = "",
                                 acs_password: str = "",
                                 inform_interval: int = 300) -> DriverResult:
        return self._run_cmds(olt, [
            "enable", "configure terminal",
            f"onu {onu_index} tr069 enable acs-url {acs_url}",
            f"onu {onu_index} tr069 acs-username {acs_username} "
            f"acs-password {acs_password}",
            f"onu {onu_index} tr069 inform {inform_interval}",
        ])


class SyrotechEPONDriver(VSOLNetlinkEPONDriver):
    """Syrotech SY-OLT-1408 (Realtek EPON, shares the V1600D CLI family).

    The Syrotech EPON OLT is a Realtek-chipset clone of the V1600D EPON
    firmware. CLI commands, OAM behavior, and provisioning sequences are
    identical to NETLINK/VSOL, so we subclass the production-tested
    VSOLNetlinkEPONDriver and just override the vendor identity.
    """
    vendor_id = "syrotech_epon"
    display_name = "Syrotech SY-OLT 1408/1416 (EPON, V1600D-family)"


class GenericCliDriver(BaseOLTDriver, _GenericTelnetCliMixin):
    """Fallback driver — operator manually composes commands per OLT.
    discover_onus returns the raw `show running-config` output for parsing.
    """
    vendor_id = "generic_cli"
    display_name = "Generic CLI (manual)"

    def discover_onus(self, olt: Dict) -> DriverResult:
        return self._run_cmds(olt, ["enable", "show running-config"])


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────
# Singleton instances so aliases share the same `id()` (cleaner list_drivers).
_vsol = VSOLNetlinkEPONDriver()
_huawei = HuaweiGPONDriver()
_zte = ZTEGPONDriver()
_bdcom = BDCOMGPONDriver()
_cdata = CDataGPONDriver()
_fiberhome = FiberHomeGPONDriver()
_optilink = OptilinkGPONDriver()
_syrotech_gpon = SyrotechGPONDriver()
_syrotech_epon = SyrotechEPONDriver()
_generic = GenericCliDriver()

_DRIVER_REGISTRY: Dict[str, BaseOLTDriver] = {
    # primary keys
    "vsol_netlink_epon": _vsol, "vsol": _vsol,
    "netlink_epon": _vsol, "netlink": _vsol,
    "huawei_gpon": _huawei, "huawei": _huawei,
    "zte_gpon": _zte, "zte": _zte,
    "bdcom_gpon": _bdcom, "bdcom": _bdcom,
    "cdata_gpon": _cdata, "cdata": _cdata, "c-data": _cdata,
    "fiberhome_gpon": _fiberhome, "fiberhome": _fiberhome,
    "optilink_gpon": _optilink, "optilink": _optilink,
    "syrotech_gpon": _syrotech_gpon,
    "syrotech_epon": _syrotech_epon,
    "syrotech": _syrotech_gpon,  # default Syrotech → GPON
    "generic": _generic, "generic_cli": _generic,
}


def get_driver(vendor: str) -> BaseOLTDriver:
    """Resolve a driver by vendor key (case-insensitive). Falls back to
    GenericCliDriver if no match — never raises."""
    k = (vendor or "").strip().lower()
    if k in _DRIVER_REGISTRY:
        return _DRIVER_REGISTRY[k]
    # Loose-match (e.g. "vsol_epon", "Netlink EPON", "ZTE-C320")
    for key, drv in _DRIVER_REGISTRY.items():
        if key in k or k in key:
            return drv
    _log(f"get_driver: no exact match for vendor='{vendor}' -> generic_cli")
    return _DRIVER_REGISTRY["generic_cli"]


def list_drivers() -> List[Dict[str, str]]:
    """For UI / API use. Deduplicates by driver instance."""
    seen = set()
    out = []
    for key, drv in _DRIVER_REGISTRY.items():
        if id(drv) in seen:
            continue
        seen.add(id(drv))
        out.append({"vendor_id": drv.vendor_id,
                    "display_name": drv.display_name})
    return sorted(out, key=lambda x: x["display_name"])
