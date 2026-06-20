"""Mikrotik website-blocker provisioner.

For each block config we maintain three things on the router, all tagged
`block/autoispbilling-{cfg_id}` so disable can purge cleanly:

  1. Address-list `block-dst-{cfg_id}` populated with each blocked domain
     (FQDN entries — RouterOS auto-resolves them every few minutes).
  2. Address-list `block-src-{cfg_id}` populated with each affected
     customer IP.
  3. ONE `/ip/firewall/filter` rule:
        chain=forward src-address-list=block-src-{id}
                       dst-address-list=block-dst-{id} action=drop

When a customer's IP changes (PPPoE re-dial) the admin clicks "Refresh
IPs" — we re-snapshot live IPs from the router and rewrite the
`block-src-{id}` list (filter rule itself is idempotent).
"""
from __future__ import annotations
import logging
from typing import Dict, List

log = logging.getLogger(__name__)

BLOCK_TAG_PREFIX = "block/autoispbilling-"


def parent_tag(cfg_id: int) -> str:
    return f"{BLOCK_TAG_PREFIX}{cfg_id}"


def src_list_name(cfg_id: int) -> str:
    return f"block-src-{cfg_id}"


def dst_list_name(cfg_id: int) -> str:
    return f"block-dst-{cfg_id}"


class WebsiteBlocker:
    """Wraps a live RouterOSClient and applies website-block configs."""

    def __init__(self, ros_client):
        self._rc = ros_client
        self._api = ros_client._api

    # ------------------------------------------------------------------
    def resolve_active_ip(self, username: str) -> str:
        """Best-effort live-IP lookup for a customer username.
        Checks /ppp/active and /ip/hotspot/active. Returns empty string
        if not online."""
        if not username:
            return ""
        try:
            for r in self._api.path("ppp/active"):
                if (r.get("name") or "") == username:
                    return r.get("address") or ""
        except Exception:
            pass
        try:
            for r in self._api.path("ip/hotspot/active"):
                if (r.get("user") or "") == username:
                    return r.get("address") or ""
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    def apply(self, cfg_id: int, domains: List[str],
              src_ips: List[Dict]) -> Dict:
        """domains: list of FQDN/IP strings to block.
        src_ips: list of {ip, label} dicts (label kept as comment).
        """
        ptag = parent_tag(cfg_id)
        src_list = src_list_name(cfg_id)
        dst_list = dst_list_name(cfg_id)
        result: Dict = {"ok": False, "pushed": [], "tag": ptag,
                        "error": ""}

        # Wipe prior tagged rules first (full-replace each save)
        try:
            self._purge_by_prefix(ptag)
        except Exception as e:
            log.warning(f"purge {ptag}: {e}")

        clean_domains = [d.strip() for d in (domains or []) if d and d.strip()]
        clean_ips = [(s.get("ip") or "").strip() for s in (src_ips or [])
                     if s and (s.get("ip") or "").strip()]
        clean_ips_with_labels = [
            (s.get("ip") or "").strip()
            for s in (src_ips or [])
            if s and (s.get("ip") or "").strip()
        ]

        if not clean_domains:
            result["error"] = "at least one domain is required"
            return result
        if not clean_ips:
            result["error"] = "no online/static IP found for selected customers"
            return result

        # 1) Populate destination address-list with FQDN/IP entries
        try:
            al = self._api.path("ip/firewall/address-list")
            for d in clean_domains:
                al.add(**{"list": dst_list, "address": d, "comment": ptag})
            result["pushed"].append(
                f"address-list {dst_list}: {len(clean_domains)} domain(s)"
            )
        except Exception as e:
            result["error"] = f"address-list dst: {e}"
            return result

        # 2) Populate source address-list with customer IPs
        try:
            al = self._api.path("ip/firewall/address-list")
            for s in (src_ips or []):
                ip = (s.get("ip") or "").strip()
                if not ip:
                    continue
                comment = ptag
                lbl = (s.get("label") or "").strip()
                if lbl:
                    comment = f"{ptag} {lbl}"
                al.add(**{"list": src_list, "address": ip,
                          "comment": comment})
            result["pushed"].append(
                f"address-list {src_list}: {len(clean_ips)} customer IP(s)"
            )
        except Exception as e:
            result["error"] = f"address-list src: {e}"
            return result

        # 3) The drop rule connecting both lists
        try:
            f = self._api.path("ip/firewall/filter")
            f.add(**{
                "chain": "forward",
                "src-address-list": src_list,
                "dst-address-list": dst_list,
                "action": "drop",
                "comment": ptag,
            })
            result["pushed"].append(
                f"filter forward drop {src_list} -> {dst_list}"
            )
        except Exception as e:
            result["error"] = f"firewall/filter: {e}"
            return result

        result["ok"] = True
        result["src_count"] = len(clean_ips)
        result["dst_count"] = len(clean_domains)
        return result

    # ------------------------------------------------------------------
    def disable(self, cfg_id: int) -> Dict:
        ptag = parent_tag(cfg_id)
        purged = self._purge_by_prefix(ptag)
        return {"ok": True, "purged": purged, "tag": ptag}

    def _purge_by_prefix(self, base_tag: str) -> int:
        """Match exact `base_tag` OR `base_tag + " ..."` (note: src-list
        rows tag as `parent comment-suffix`) OR `base_tag + "-..."`."""
        prefix_dash = base_tag + "-"
        prefix_space = base_tag + " "
        removed = 0
        for path in (
            "ip/firewall/filter",
            "ip/firewall/address-list",
        ):
            try:
                p = self._api.path(path)
                ids = []
                for r in p:
                    cmt = r.get("comment") or ""
                    if (cmt == base_tag
                            or cmt.startswith(prefix_dash)
                            or cmt.startswith(prefix_space)):
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
