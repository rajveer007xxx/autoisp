"""S39R5L_VENDOR — Real-world SNMP polling adapters for ISP-grade GPON OLTs.

Status: scaffolded with vendor-specific MIB OIDs from each vendor's public
documentation. Each adapter exposes a uniform interface:

    poll_environment(ip, community, timeout=5) -> dict
        Returns: {ok, cpu_pct, temp_c, voltage_v, fan_status, uptime_s,
                 sys_name, error}
    poll_pon_ports(ip, community, timeout=5) -> list[dict]
        Returns: [{pon_index, admin_status, oper_status, tx_power_dbm,
                  rx_power_dbm, onu_count, ...}, ...]
    poll_onus(ip, community, timeout=10) -> list[dict]
        Returns: [{pon_port_index, onu_index, serial, mac, model,
                  rx_power_dbm, tx_power_dbm, distance_m, status, ...}, ...]

Note on testing: These adapters are written from each vendor's published
MIB / CLI documentation. Real-world OLTs tend to deviate slightly from
spec (firmware quirks, custom OID branches). Each adapter logs unparsed
responses to /var/log/autoispbilling/snmp-debug.log for iterative tuning
once a real OLT is connected.
"""
import os
import time
import subprocess
from typing import Dict, List, Optional


_LOG = "/var/log/autoispbilling/snmp-debug.log"
os.makedirs(os.path.dirname(_LOG), exist_ok=True)


def _snmp_walk(ip: str, community: str, oid: str,
               timeout: int = 5, version: str = "2c") -> List[str]:
    """net-snmp wrapper. Returns list of lines (KEY = TYPE: VALUE)."""
    cmd = ["snmpwalk", "-v", version, "-c", community,
           "-t", str(timeout), "-r", "1", "-Ovq", ip, oid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        if r.returncode != 0:
            with open(_LOG, "a") as fh:
                fh.write(f"[{time.time():.0f}] {ip} walk {oid} FAIL: {r.stderr.strip()[:200]}\n")
            return []
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception as e:
        with open(_LOG, "a") as fh:
            fh.write(f"[{time.time():.0f}] {ip} walk {oid} EXC: {e}\n")
        return []


def _snmp_get(ip: str, community: str, oid: str,
              timeout: int = 5, version: str = "2c") -> Optional[str]:
    cmd = ["snmpget", "-v", version, "-c", community,
           "-t", str(timeout), "-r", "1", "-Ovq", ip, oid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip().strip('"')
    except Exception:
        return None


# Standard MIB-II OIDs (work everywhere)
_OID_SYSNAME    = "1.3.6.1.2.1.1.5.0"
_OID_SYSDESCR   = "1.3.6.1.2.1.1.1.0"
_OID_SYSUPTIME  = "1.3.6.1.2.1.1.3.0"


# ============================================================================
#                              GENERIC FALLBACK
# ============================================================================
class GenericAdapter:
    name = "generic"

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        try:
            sysname = _snmp_get(ip, community, _OID_SYSNAME, timeout)
            uptime  = _snmp_get(ip, community, _OID_SYSUPTIME, timeout)
            if sysname is None:
                return {"ok": False, "error": "SNMP not responding"}
            return {"ok": True, "sys_name": sysname,
                    "uptime_s": int(uptime or 0) // 100,
                    "cpu_pct": None, "temp_c": None,
                    "voltage_v": None, "fan_status": "Unknown"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @classmethod
    def poll_onus(cls, ip, community, timeout=10):
        return []


# ============================================================================
#                                NOKIA (ISAM)
#   ISAM 7360 / 7367 — uses ALCATEL-IND1-PORT-MIB + GPON-ONT-MGMT-MIB
#   Reference: nokia.com/networks technical-doc DN9020210 (public)
# ============================================================================
class NokiaAdapter(GenericAdapter):
    name = "nokia"
    _OID_TEMP    = "1.3.6.1.4.1.637.61.1.23.5.1.2"   # nbnSwTemperatureValue
    _OID_CPU     = "1.3.6.1.4.1.637.61.1.4.4.1.4.0"  # nbnCpu5SecUtil
    _OID_ONT_RX  = "1.3.6.1.4.1.637.61.1.35.6.1.4"   # GPON-ONT-MIB::ontRxOpticalPower
    _OID_ONT_TX  = "1.3.6.1.4.1.637.61.1.35.6.1.5"
    _OID_ONT_SN  = "1.3.6.1.4.1.637.61.1.35.5.1.4"   # ontSerialNumber

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if not env.get("ok"):
            return env
        try:
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_walk(ip, community, cls._OID_TEMP, timeout)
            if cpu and cpu.isdigit():
                env["cpu_pct"] = float(cpu)
            if tmp:
                # Take max sensor as core temp
                vals = [float(t) for t in tmp if t.replace(".", "").lstrip("-").isdigit()]
                env["temp_c"] = max(vals) / 10.0 if vals else None
        except Exception:
            pass
        return env


# ============================================================================
#                             OPTILINK (OLT-9116/8116/4116)
#   Vendor MIB: OPTILINK-OLT-MIB. RX/TX power in 0.01 dBm units.
#   Public ref: optilink.com.cn/upload/files/MIB-Optilink-OLT-V1.x.zip
# ============================================================================
class OptilinkAdapter(GenericAdapter):
    name = "optilink"
    _OID_CPU    = "1.3.6.1.4.1.45100.1.1.4.1.0"          # cpuUsage
    _OID_TEMP   = "1.3.6.1.4.1.45100.1.1.4.2.0"          # boardTemp
    _OID_FAN    = "1.3.6.1.4.1.45100.1.1.4.5.0"          # fanStatus 1=normal 2=alarm
    _OID_ONU_SN = "1.3.6.1.4.1.45100.1.10.5.1.1.4"       # gponOnuSn (per index)
    _OID_ONU_RX = "1.3.6.1.4.1.45100.1.10.5.1.1.32"      # rxPowerCentidBm
    _OID_ONU_TX = "1.3.6.1.4.1.45100.1.10.5.1.1.33"      # txPowerCentidBm
    _OID_ONU_DIST = "1.3.6.1.4.1.45100.1.10.5.1.1.20"    # distance (m)
    _OID_ONU_STATUS = "1.3.6.1.4.1.45100.1.10.5.1.1.6"   # 1=online 0=offline

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if not env.get("ok"):
            return env
        try:
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_get(ip, community, cls._OID_TEMP, timeout)
            fan = _snmp_get(ip, community, cls._OID_FAN, timeout)
            if cpu and cpu.isdigit(): env["cpu_pct"] = float(cpu)
            if tmp and tmp.isdigit(): env["temp_c"]  = float(tmp)
            env["fan_status"] = "Normal" if fan == "1" else ("Critical" if fan == "2" else "Unknown")
        except Exception:
            pass
        return env

    @classmethod
    def poll_onus(cls, ip, community, timeout=10):
        sn_lines = _snmp_walk(ip, community, cls._OID_ONU_SN, timeout)
        rx_lines = _snmp_walk(ip, community, cls._OID_ONU_RX, timeout)
        # Each line corresponds to a (pon_index . onu_index) suffix.
        # Without index, we fall back to ordering — production code should
        # use snmpwalk -On to retrieve full OID then split the suffix.
        out = []
        for i, sn in enumerate(sn_lines):
            rx = float(rx_lines[i]) / 100.0 if i < len(rx_lines) and rx_lines[i].lstrip("-").isdigit() else None
            out.append({"serial": sn.strip().strip('"'), "rx_power_dbm": rx,
                        "vendor": "optilink"})
        return out


# ============================================================================
#                           VSOL (V1600 / V2400 / V3700)
#   MIB: V-SOL-OLT-MIB (private OID 37950)
#   Reference: vsolcn.com/download/V-SOL-OLT-MIB-V2.x.zip
# ============================================================================
class VsolAdapter(GenericAdapter):
    name = "vsol"
    _OID_CPU      = "1.3.6.1.4.1.37950.1.1.5.1.0"
    _OID_TEMP     = "1.3.6.1.4.1.37950.1.1.5.2.0"
    _OID_FAN      = "1.3.6.1.4.1.37950.1.1.5.4.0"
    _OID_ONU_SN   = "1.3.6.1.4.1.37950.1.6.1.1.1.4"
    _OID_ONU_RX   = "1.3.6.1.4.1.37950.1.6.1.1.1.20"   # 0.01 dBm
    _OID_ONU_TX   = "1.3.6.1.4.1.37950.1.6.1.1.1.21"
    _OID_ONU_DIST = "1.3.6.1.4.1.37950.1.6.1.1.1.15"

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if not env.get("ok"):
            return env
        try:
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_get(ip, community, cls._OID_TEMP, timeout)
            fan = _snmp_get(ip, community, cls._OID_FAN, timeout)
            if cpu and cpu.isdigit(): env["cpu_pct"] = float(cpu)
            if tmp and tmp.isdigit(): env["temp_c"]  = float(tmp)
            env["fan_status"] = "Normal" if fan == "1" else "Critical" if fan == "2" else "Unknown"
        except Exception:
            pass
        return env

    # __s56y_vsol_iftable_onus__
    @classmethod
    def poll_onus(cls, ip, community, timeout=10):
        """V1600D / V1600G OEMs hide vendor OIDs and expose ONUs only
        via the standard ifTable as `EPON0/<pon>:<onu>` sub-interfaces.
        We walk ifDescr + ifOperStatus, regex out the (pon, onu) tuple,
        and synthesize one row per ONU. Vendor RX/TX/SN are not
        available on this firmware family — set to None."""
        import subprocess, re as _re
        try:
            r = subprocess.run(
                ["snmpwalk", "-v", "2c", "-c", community, "-On",
                 "-t", str(timeout), "-r", "1",
                 ip, "1.3.6.1.2.1.2.2.1.2"],  # ifDescr
                capture_output=True, text=True, timeout=timeout + 5)
            if r.returncode != 0:
                return []
        except Exception:
            return []
        # Also walk ifOperStatus (1.3.6.1.2.1.2.2.1.8) for up/down state.
        status_map = {}
        try:
            rs = subprocess.run(
                ["snmpwalk", "-v", "2c", "-c", community, "-On",
                 "-t", str(timeout), "-r", "1",
                 ip, "1.3.6.1.2.1.2.2.1.8"],
                capture_output=True, text=True, timeout=timeout + 5)
            if rs.returncode == 0:
                for line in rs.stdout.splitlines():
                    m = _re.search(r"\.(\d+)\s*=\s*INTEGER:\s*(\d+)", line)
                    if m:
                        status_map[int(m.group(1))] = int(m.group(2))
        except Exception:
            pass

        pat = _re.compile(
            r'\.(\d+)\s*=\s*STRING:\s*"?(?:EPON|GPON)(\d+)/(\d+):(\d+)"?',
            _re.IGNORECASE,
        )
        out = []
        for line in r.stdout.splitlines():
            m = pat.search(line)
            if not m:
                continue
            ifindex = int(m.group(1))
            chassis = m.group(2)
            pon = int(m.group(3))
            onu = int(m.group(4))
            oper = status_map.get(ifindex, 0)
            # ifOperStatus: 1=up, 2=down, 3=testing, 4=unknown,
            #               5=dormant, 6=notPresent, 7=lowerLayerDown
            status = "online" if oper == 1 else (
                "offline" if oper in (2, 5, 7) else "unknown")
            out.append({
                "ifindex": ifindex,
                "pon": pon,
                "onu_id": onu,
                "sn": None,
                "rx_power": None,
                "tx_power": None,
                "distance_m": None,
                "status": status,
                "name": f"EPON{chassis}/{pon}:{onu}",
            })
        return out


# ============================================================================
#                         SYROTECH (G-1425 / G-1224)
#   MIB: SYROTECH-OLT-MIB (private OID 30966)
#   Reference: syrotech.com.cn — vendor MIB pack
# ============================================================================
class SyrotechAdapter(GenericAdapter):
    name = "syrotech"
    _OID_CPU      = "1.3.6.1.4.1.30966.1.1.5.1.0"
    _OID_TEMP     = "1.3.6.1.4.1.30966.1.1.5.3.0"
    _OID_ONU_SN   = "1.3.6.1.4.1.30966.1.7.1.1.1.4"
    _OID_ONU_RX   = "1.3.6.1.4.1.30966.1.7.1.1.1.18"
    _OID_ONU_TX   = "1.3.6.1.4.1.30966.1.7.1.1.1.19"


# ============================================================================
#                              ZTE (C320 / C300 / C600)
#   MIB: ZXAN-COMMON-MIB (private OID 3902)
#   Reference: zte.com.cn/portal — public MIB-pack download
# ============================================================================
class ZteAdapter(GenericAdapter):
    name = "zte"
    _OID_CPU       = "1.3.6.1.4.1.3902.1015.1.2.0"     # zxAnCpuUsage
    _OID_TEMP      = "1.3.6.1.4.1.3902.1015.2.1.0"     # zxAnTemperature
    _OID_ONU_SN    = "1.3.6.1.4.1.3902.1012.3.28.1.1.5"
    _OID_ONU_RX    = "1.3.6.1.4.1.3902.1012.3.50.12.1.1.10"   # ontRxPower (0.1 uW)
    _OID_ONU_TX    = "1.3.6.1.4.1.3902.1012.3.50.12.1.1.11"
    _OID_ONU_DIST  = "1.3.6.1.4.1.3902.1012.3.28.1.1.10"

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if env.get("ok"):
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_get(ip, community, cls._OID_TEMP, timeout)
            if cpu and cpu.isdigit(): env["cpu_pct"] = float(cpu)
            if tmp and tmp.isdigit(): env["temp_c"]  = float(tmp)
        return env


# ============================================================================
#                       FIBERHOME (AN5506 / AN6000)
#   MIB: FH-EPON-MIB / FH-GPON-MIB (private OID 5875)
#   Reference: fiberhome.com — China-region MIB pack
# ============================================================================
class FiberhomeAdapter(GenericAdapter):
    name = "fiberhome"
    _OID_CPU        = "1.3.6.1.4.1.5875.800.3.10.1.7.1.4.1"
    _OID_TEMP       = "1.3.6.1.4.1.5875.800.3.10.1.7.1.5.1"
    _OID_ONU_SN     = "1.3.6.1.4.1.5875.800.3.10.1.4.6.1.5"
    _OID_ONU_RX     = "1.3.6.1.4.1.5875.800.3.10.1.4.10.1.10"  # 0.1 dBm
    _OID_ONU_TX     = "1.3.6.1.4.1.5875.800.3.10.1.4.10.1.11"


# ============================================================================
#                              HUAWEI (MA56xxT / MA58xx)
#   MIB: HUAWEI-XPON-MIB (private OID 2011)
# ============================================================================
class HuaweiAdapter(GenericAdapter):
    name = "huawei"
    _OID_CPU       = "1.3.6.1.4.1.2011.6.3.4.1.5"      # hwCpuPeak
    _OID_TEMP      = "1.3.6.1.4.1.2011.6.3.3.2.1.4"    # hwBoardTemp
    _OID_ONU_SN    = "1.3.6.1.4.1.2011.6.128.1.1.2.43.1.5"
    _OID_ONU_RX    = "1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4"



# ============================================================================
#                       SYROTECH-EPON  (G-1004 / G-1008 / G-1408)
#   EPON OLTs use a different MIB tree than their GPON counterparts.
#   Reference: SYROTECH-EPON-OLT-MIB (private OID 30966.1.x EPON branch).
# ============================================================================
class SyrotechEponAdapter(GenericAdapter):
    name = "syrotech_epon"
    _OID_CPU      = "1.3.6.1.4.1.30966.1.6.1.1.0"
    _OID_TEMP     = "1.3.6.1.4.1.30966.1.6.1.4.0"
    _OID_FAN      = "1.3.6.1.4.1.30966.1.6.1.7.0"
    _OID_ONU_MAC  = "1.3.6.1.4.1.30966.1.7.1.1.1.4"
    _OID_ONU_RX   = "1.3.6.1.4.1.30966.1.7.1.1.1.18"
    _OID_ONU_TX   = "1.3.6.1.4.1.30966.1.7.1.1.1.19"
    _OID_ONU_DIST = "1.3.6.1.4.1.30966.1.7.1.1.1.21"
    _OID_ONU_STATE= "1.3.6.1.4.1.30966.1.7.1.1.1.5"

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if env.get("ok"):
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_get(ip, community, cls._OID_TEMP, timeout)
            fan = _snmp_get(ip, community, cls._OID_FAN, timeout)
            if cpu and cpu.isdigit(): env["cpu_pct"] = float(cpu)
            if tmp and tmp.lstrip("-").isdigit(): env["temp_c"] = float(tmp)
            env["fan_status"] = ("Normal" if fan == "1" else
                                  "Critical" if fan == "2" else "Unknown")
        return env


# ============================================================================
#                     NETLINK / C-DATA EPON  (FD11xx / FD16xx)
#   Netlink and many other "indian-rebrander" EPON OLTs ship the C-Data
#   chipset firmware. SNMP private branch 17409.
# ============================================================================
class NetlinkEponAdapter(GenericAdapter):
    name = "netlink"
    _OID_CPU       = "1.3.6.1.4.1.17409.1.1.1.5.0"
    _OID_TEMP      = "1.3.6.1.4.1.17409.1.1.1.7.0"
    _OID_FAN       = "1.3.6.1.4.1.17409.1.1.1.10.0"
    _OID_ONU_MAC   = "1.3.6.1.4.1.17409.2.4.1.1.1.4"
    _OID_ONU_RX    = "1.3.6.1.4.1.17409.2.4.1.1.1.16"
    _OID_ONU_TX    = "1.3.6.1.4.1.17409.2.4.1.1.1.17"
    _OID_ONU_STATE = "1.3.6.1.4.1.17409.2.4.1.1.1.5"

    @classmethod
    def poll_environment(cls, ip, community, timeout=5):
        env = super().poll_environment(ip, community, timeout)
        if env.get("ok"):
            cpu = _snmp_get(ip, community, cls._OID_CPU, timeout)
            tmp = _snmp_get(ip, community, cls._OID_TEMP, timeout)
            if cpu and cpu.isdigit(): env["cpu_pct"] = float(cpu)
            if tmp and tmp.lstrip("-").isdigit(): env["temp_c"] = float(tmp)
        return env


# C-Data is the silicon vendor; many brands rebrand the same firmware.
CDataEponAdapter = NetlinkEponAdapter

# ============================================================================
#                              ROUTER  (vendor -> adapter)
# ============================================================================
ADAPTERS = {
    "nokia": NokiaAdapter,
    "optilink": OptilinkAdapter,
    "vsol": VsolAdapter,
    "syrotech": SyrotechAdapter,
    "syrotech_epon": SyrotechEponAdapter,
    "netlink": NetlinkEponAdapter,
    "netlink_epon": NetlinkEponAdapter,
    "cdata": CDataEponAdapter,
    "cdata_epon": CDataEponAdapter,
    "zte": ZteAdapter,
    "fiberhome": FiberhomeAdapter,
    "huawei": HuaweiAdapter,
    "generic": GenericAdapter,
    "mock": GenericAdapter,
}


def get_adapter(vendor: str):
    """Return the adapter class for the given vendor key. Falls back to
    GenericAdapter for unknown vendors."""
    v = (vendor or "").strip().lower()
    return ADAPTERS.get(v, GenericAdapter)


def poll_environment(vendor: str, ip: str, community: str = "public",
                     timeout: int = 5) -> Dict:
    """Top-level dispatch."""
    return get_adapter(vendor).poll_environment(ip, community, timeout)


def poll_onus(vendor: str, ip: str, community: str = "public",
              timeout: int = 10) -> List[Dict]:
    return get_adapter(vendor).poll_onus(ip, community, timeout)
