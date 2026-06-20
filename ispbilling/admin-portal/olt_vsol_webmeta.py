"""
_S53D_  VSOL Web UI scraper — pulls CPU% / Memory% / Uptime from the
        OLT's HTTP admin panel (`/action/main.html`).

The Netlink/VSOL/Syrotech EPON V1600D family exposes "System Basic
Information" via an HTTP form GET. CLI does not expose these values
so this scraper fills the gap.

Public API:
    fetch_vsol_web_meta(host, user, pwd, *, port=80, https=False,
                          timeout=4) -> dict
returns: { "ok": bool, "cpu_pct": float, "mem_pct": float,
           "uptime_sec": int, "raw": "...", "error": "..." }

The function is best-effort: any network failure / auth failure / parse
failure returns `{"ok": False, "error": "..."}` without raising.
"""
from __future__ import annotations

import re
import socket
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any


_UPTIME_RE = re.compile(
    r"(\d+)\s*(?:Days?|days?|d)\s*(\d+)\s*(?:Hours?|hours?|h)\s*"
    r"(\d+)\s*(?:Minutes?|minutes?|m(?:in)?)\s*(\d+)\s*"
    r"(?:Seconds?|seconds?|s)?", re.I)
_PCT_RE     = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _parse_uptime(text: str) -> int:
    m = _UPTIME_RE.search(text or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_vsol_web_meta(host: str, user: str, pwd: str, *,
                         port: int = 80, https: bool = False,
                         timeout: float = 4.0) -> Dict[str, Any]:
    if not host:
        return {"ok": False, "error": "no host"}
    scheme = "https" if https else "http"
    base = f"{scheme}://{host}:{port}"
    # VSOL session cookie endpoint: GET / first (form auth posts back).
    pw_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pw_mgr.add_password(None, base, user or "admin", pwd or "admin")
    handler = urllib.request.HTTPBasicAuthHandler(pw_mgr)
    opener = urllib.request.build_opener(handler)
    candidates = [
        # Most VSOL OEMs:
        "/action/main.html",
        "/action/sysinfo.html",
        "/cgi-bin/baseinfo.cgi",
        "/cgi-bin/system_status.cgi",
        # Generic fallback
        "/",
    ]
    last_err = None
    for path in candidates:
        url = base + path
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ISPBilling/1.0",
                              "Accept": "text/html,*/*"})
            with opener.open(req, timeout=timeout) as r:
                body = r.read(64 * 1024).decode("utf-8", "ignore")
        except (urllib.error.HTTPError, urllib.error.URLError,
                socket.timeout, ConnectionError, OSError) as e:
            last_err = f"{path}: {e}"
            continue
        # Locate the CPU / Memory percentages and uptime in the page.
        # The page uses a labeled table; we don't bind to specific
        # CSS — we search for the labels and grab the nearest "<n>%".
        out: Dict[str, Any] = {"raw": body[:512]}
        # CPU
        m = re.search(r"CPU\s*(?:Usage|Util\w*)\s*[:<>\s/]+(\d+(?:\.\d+)?)\s*%",
                       body, re.I)
        if m: out["cpu_pct"] = float(m.group(1))
        # Memory / RAM
        m = re.search(r"(?:Memory|RAM|Mem)\s*(?:Usage|Util\w*)?\s*[:<>\s/]+"
                       r"(\d+(?:\.\d+)?)\s*%", body, re.I)
        if m: out["mem_pct"] = float(m.group(1))
        # Uptime
        m = re.search(r"(?:Running|Up)\s*Time\s*[:<>\s/]+"
                       r"(\d+\s*Days?[^<]+?Seconds?)", body, re.I)
        if m: out["uptime_sec"] = _parse_uptime(m.group(1))
        if any(k in out for k in ("cpu_pct", "mem_pct", "uptime_sec")):
            out["ok"] = True
            return out
    return {"ok": False, "error": last_err or "no usable endpoint"}
