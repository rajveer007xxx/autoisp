"""Mikrotik NAT provisioner.

Pushes idempotent /ip/address + /ip/firewall/nat rules for outbound NAT
configs. Each config gets a unique tag prefix `nat/autoispbilling-{id}`
so disable can purge cleanly. 1:1 pair rules use a child tag of the form
`nat/autoispbilling-{cfg_id}-p{pair_id}` so the parent disable wipes them
in a single prefix scan.

Supported actions:
  - masquerade : simple, uses WAN's primary IP. Best for single public IP.
  - netmap     : 1:1 hash mapping across a public range. Best for /29+ blocks.
  - src-nat    : round-robin source-NAT to a public range.
  - 1to1       : per-customer dedicated public IP (src-nat + dst-nat pair
                 per customer).
"""
from __future__ import annotations
import logging
from typing import Dict, List

log = logging.getLogger(__name__)

NAT_TAG_PREFIX = "nat/autoispbilling-"


def network_to_range(cidr: str) -> str:
    """36.60.119.112/29 -> 36.60.119.113-36.60.119.118 (skip net + bcast)."""
    if not cidr or "/" not in cidr:
        return ""
    try:
        a, plen = cidr.split("/", 1)
        plen_n = int(plen)
        a_int = int.from_bytes(bytes(int(x) for x in a.split(".")), "big")
        size = 1 << (32 - plen_n)
        first = a_int + 1
        last = a_int + size - 2
        if first > last:
            return ""

        def to_ip(n):
            return ".".join(str((n >> s) & 0xFF) for s in (24, 16, 8, 0))

        return f"{to_ip(first)}-{to_ip(last)}"
    except Exception:
        return ""


def network_to_ip_list(cidr: str) -> List[str]:
    """36.60.119.112/29 -> [36.60.119.113, .114, .115, .116, .117, .118]."""
    rng = network_to_range(cidr)
    if "-" not in rng:
        return []
    a, _, b = rng.partition("-")
    try:
        a_int = int.from_bytes(bytes(int(x) for x in a.split(".")), "big")
        b_int = int.from_bytes(bytes(int(x) for x in b.split(".")), "big")
    except Exception:
        return []

    def to_ip(n):
        return ".".join(str((n >> s) & 0xFF) for s in (24, 16, 8, 0))

    return [to_ip(n) for n in range(a_int, b_int + 1)]


# Single point of truth for the parent tag.
def parent_tag(cfg_id: int) -> str:
    return f"{NAT_TAG_PREFIX}{cfg_id}"


def pair_tag(cfg_id: int, pair_id: int) -> str:
    return f"{NAT_TAG_PREFIX}{cfg_id}-p{pair_id}"


class NATProvisioner:
    """Wraps a live RouterOSClient and applies outbound NAT configs."""

    def __init__(self, ros_client):
        self._rc = ros_client
        self._api = ros_client._api

    # ------------------------------------------------------------------
    def list_interfaces(self):
        out = []
        try:
            for i in self._api.path("interface"):
                if i.get("name"):
                    out.append({
                        "name": i["name"],
                        "type": i.get("type", ""),
                        "running": (i.get("running") or "false") in ("true", True),
                    })
        except Exception as e:
            log.warning(f"list_interfaces: {e}")
        return out

    # ------------------------------------------------------------------
    def apply(self, cfg_id: int, cfg: Dict, pairs: List[Dict] = None) -> Dict:
        """cfg keys:
            nat_network: e.g. 36.60.119.111/29 (the public block, required)
            nat_range:   e.g. 36.60.119.112-36.60.119.118 (auto-derived if blank)
            interface:   ether1 / WAN iface (required)
            action:      masquerade | netmap | src-nat | 1to1
            source_address: e.g. 10.0.0.0/24 or blank for all (ignored for 1to1)
            bind_address: True/False — also add /ip/address on the WAN iface

        For action='1to1', `pairs` is a list of dicts:
            [{pair_id, private_ip, public_ip, customer_username}, ...]

        Returns: {ok, pushed, error?, pair_results?}
        """
        ptag = parent_tag(cfg_id)
        result: Dict = {"ok": False, "pushed": [], "tag": ptag,
                        "error": "", "pair_results": []}

        # Always wipe any prior tagged rules first (full-replace per save)
        try:
            self._purge_by_prefix(ptag)
        except Exception as e:
            log.warning(f"purge {ptag}: {e}")

        nat_network = (cfg.get("nat_network") or "").strip()
        iface = (cfg.get("interface") or "").strip()
        action = (cfg.get("action") or "masquerade").strip()
        src_addr = (cfg.get("source_address") or "").strip()
        nat_range = (cfg.get("nat_range") or "").strip() or network_to_range(nat_network)
        bind = bool(cfg.get("bind_address", True))

        if not nat_network or "/" not in nat_network:
            result["error"] = "nat_network must be a CIDR like 36.60.119.111/29"
            return result
        if not iface:
            result["error"] = "interface is required"
            return result
        if action not in ("masquerade", "netmap", "src-nat", "1to1"):
            result["error"] = f"invalid action: {action}"
            return result
        if action in ("netmap", "src-nat") and not nat_range:
            result["error"] = "nat_range could not be derived (network too small?)"
            return result
        if action == "1to1" and not pairs:
            result["error"] = "1:1 mapping requires at least one customer pair"
            return result

        # 1) /ip/address — bind the public block to the chosen WAN iface
        if bind:
            try:
                ipa = self._api.path("ip/address")
                exists = False
                for r in ipa:
                    if (r.get("address") or "").split("/")[0] == nat_network.split("/")[0]:
                        exists = True
                        break
                if not exists:
                    ipa.add(address=nat_network, interface=iface, comment=ptag)
                    result["pushed"].append(f"/ip/address {nat_network} on {iface}")
                else:
                    result["pushed"].append(f"/ip/address {nat_network} already present")
            except Exception as e:
                emsg = str(e).lower()
                if "already have such address" not in emsg:
                    result["error"] = f"ip/address: {e}"
                    return result

        # 2) /ip/firewall/nat — main rule (skipped for 1to1; pair rules do it)
        # _S39R5V_MULTIIP_NAT_ multi-IP NAT improvements:
        #   * place_at_top    — put rule at index 0 (place-before=0)
        #   * auto_disable_masq — disable conflicting catch-all masquerade
        #   * pcc_enabled (src-nat only) — sticky per-customer distribution
        if action != "1to1":
            try:
                nat = self._api.path("ip/firewall/nat")
                place_at_top = bool(cfg.get("place_at_top", True))
                auto_disable_masq = bool(cfg.get("auto_disable_masq", False))
                pcc_enabled = bool(cfg.get("pcc_enabled", False))

                # (b) Auto-disable conflicting masquerade rules on same iface
                if auto_disable_masq and action in ("netmap", "src-nat"):
                    disabled_n = 0
                    try:
                        for r in list(nat):
                            r_act = (r.get("action") or "").lower()
                            r_iface = (r.get("out-interface") or "")
                            r_cmt = (r.get("comment") or "")
                            r_dis = str(r.get("disabled","")).lower() in ("true","yes")
                            if (r_act == "masquerade" and r_iface == iface
                                    and not r_cmt.startswith(NAT_TAG_PREFIX)
                                    and not r_dis):
                                try:
                                    nat.update(numbers=r["id"], disabled="yes")
                                    disabled_n += 1
                                except Exception as _de:
                                    log.warning(f"disable masq {r.get('id')}: {_de}")
                        if disabled_n:
                            result["pushed"].append(
                                f"disabled {disabled_n} conflicting masquerade rule(s) on {iface}"
                            )
                    except Exception as _le:
                        log.warning(f"list nat: {_le}")

                # (c) PCC sticky distribution — push N parallel rules
                pcc_emitted = False
                if pcc_enabled and action == "src-nat":
                    ips = network_to_ip_list(nat_network)
                    n = len(ips)
                    if n >= 2:
                        for i, ip in enumerate(ips):
                            params = {
                                "chain": "srcnat",
                                "out-interface": iface,
                                "action": "src-nat",
                                "to-addresses": ip,
                                "per-connection-classifier":
                                    f"both-addresses-and-ports:{n}/{i}",
                                "comment": f"{ptag} pcc {i+1}/{n}",
                            }
                            if src_addr:
                                params["src-address"] = src_addr
                            if place_at_top:
                                params["place-before"] = "0"
                            nat.add(**params)
                        result["pushed"].append(
                            f"/ip/firewall/nat src-nat PCC sticky x{n} via {iface}"
                            f" pool={ips[0]}…{ips[-1]}"
                        )
                        pcc_emitted = True

                # Default single-rule path (covers masquerade, netmap, src-nat
                # without PCC, AND src-nat with PCC fallback for n<2).
                if not pcc_emitted:
                    params = {
                        "chain": "srcnat",
                        "out-interface": iface,
                        "action": action,
                        "comment": ptag,
                    }
                    if src_addr:
                        params["src-address"] = src_addr
                    if action in ("netmap", "src-nat"):
                        params["to-addresses"] = nat_range
                    if place_at_top and action in ("netmap", "src-nat"):
                        params["place-before"] = "0"
                    nat.add(**params)
                    result["pushed"].append(
                        f"/ip/firewall/nat {action} via {iface}"
                        + (f" src={src_addr}" if src_addr else " src=any")
                        + (f" to={nat_range}" if action != "masquerade" else "")
                        + (" (placed at top)" if place_at_top
                           and action in ("netmap","src-nat") else "")
                    )
            except Exception as e:
                result["error"] = f"firewall/nat: {e}"
                return result

        # 3) For 1:1, push per-pair srcnat + dstnat
        if action == "1to1":
            nat = self._api.path("ip/firewall/nat")
            for pair in pairs:
                pid = int(pair.get("pair_id") or 0)
                priv = (pair.get("private_ip") or "").strip()
                pub = (pair.get("public_ip") or "").strip()
                uname = (pair.get("customer_username") or "").strip()
                ptag_pair = pair_tag(cfg_id, pid)
                pair_res = {"pair_id": pid, "private_ip": priv,
                            "public_ip": pub, "username": uname,
                            "ok": False, "error": ""}
                if not priv or not pub:
                    pair_res["error"] = "private_ip and public_ip are required"
                    result["pair_results"].append(pair_res)
                    continue
                try:
                    # outbound: priv leaves as pub
                    nat.add(**{
                        "chain": "srcnat",
                        "src-address": priv,
                        "out-interface": iface,
                        "action": "src-nat",
                        "to-addresses": pub,
                        "comment": ptag_pair,
                    })
                    # inbound: pub redirects to priv
                    nat.add(**{
                        "chain": "dstnat",
                        "dst-address": pub,
                        "in-interface": iface,
                        "action": "dst-nat",
                        "to-addresses": priv,
                        "comment": ptag_pair,
                    })
                    pair_res["ok"] = True
                    result["pushed"].append(
                        f"1:1 {uname or priv} <-> {pub} via {iface}"
                    )
                except Exception as e:
                    pair_res["error"] = str(e)
                result["pair_results"].append(pair_res)

            ok_count = sum(1 for p in result["pair_results"] if p["ok"])
            if ok_count == 0:
                result["error"] = "all pairs failed to push"
                return result

        result["ok"] = True
        return result

    # ------------------------------------------------------------------
    def disable(self, cfg_id: int) -> Dict:
        """Remove every rule whose comment is `parent_tag` OR begins with
        `parent_tag + "-"` (catches all pair children)."""
        ptag = parent_tag(cfg_id)
        purged = self._purge_by_prefix(ptag)
        return {"ok": True, "purged": purged, "tag": ptag}

    def _purge_by_prefix(self, base_tag: str) -> int:
        """Match exact `base_tag` OR `base_tag + "-..."` to catch pair children
        without mis-matching e.g. cfg=1 vs cfg=11 (the trailing '-' guards it)."""
        prefix_with_dash = base_tag + "-"
        removed = 0
        for path in ("ip/firewall/nat", "ip/address"):
            try:
                p = self._api.path(path)
                ids = []
                for r in p:
                    cmt = r.get("comment") or ""
                    if cmt == base_tag or cmt.startswith(prefix_with_dash):
                        if ".id" in r:
                            ids.append(r[".id"])
                for rid in ids:
                    try:
                        p.remove(rid)
                        removed += 1
                    except Exception as e:
                        log.warning(f"purge {path} {rid}: {e}")
            except Exception as e:
                log.warning(f"enumerate {path}: {e}")
        return removed
