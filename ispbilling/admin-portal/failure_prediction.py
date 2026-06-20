"""_S57C_FAILPRED_  Phase 3 — Failure-Prediction Worker.

Every 5 minutes scan onu_signal_samples for the last 24h per ONU and
compute the simple linear-regression slope (dB/day) of rx_power.

If |slope| exceeds the tenant-configured signal_drop_threshold_db / day
(default 3.0 dB), open a signal_degradation_events_v2 row with
predicted_fail_in_days = max(0, abs(margin_dB / abs(slope))).

Idempotent: deduplicates open events per (company_id, onu_id) — only the
latest open row is updated."""
from __future__ import annotations
import logging, math, threading, time
from typing import Dict, List

from sqlalchemy import text as _t

log = logging.getLogger("failpred")


def _compute_slope(samples: List[tuple]) -> float | None:
    """Returns slope in dBm per day. samples = list of (ts_seconds, rx_dbm)."""
    n = len(samples)
    if n < 4:
        return None
    sx = sy = sxx = sxy = 0.0
    for t, y in samples:
        x = float(t) / 86400.0   # convert seconds -> days
        sx += x; sy += y; sxx += x*x; sxy += x*y
    denom = (n * sxx - sx * sx)
    if abs(denom) < 1e-9:
        return None
    return (n * sxy - sx * sy) / denom


def _run_once() -> Dict[str, int]:
    """Single pass — returns counts for logging/visibility."""
    out = {"scanned": 0, "opened": 0, "updated": 0, "closed": 0}
    try:
        from database import engine
    except Exception as e:
        log.warning(f"[failpred] no engine: {e}")
        return out
    with engine.begin() as conn:
        # Pull tenant thresholds once.
        try:
            rows = conn.execute(_t(
                "SELECT company_id, signal_drop_threshold_db, "
                "signal_critical_threshold_dbm FROM olt_settings"
            )).fetchall()
        except Exception:
            rows = []
        thresholds = {r[0]: (float(r[1]) if r[1] is not None else 3.0,
                              float(r[2]) if r[2] is not None else -27.0)
                      for r in rows}

        # Pull recent (24h) rx_dbm samples grouped by ONU.
        # ts is TEXT (legacy SQLite-migration) — cast before EXTRACT.
        samples_rows = conn.execute(_t(
            "SELECT company_id, onu_id, "
            "EXTRACT(EPOCH FROM ts::timestamptz)::bigint AS sec, rx_dbm "
            "FROM onu_signal_samples "
            "WHERE ts::timestamptz >= NOW() - INTERVAL '24 hours' "
            "  AND rx_dbm IS NOT NULL "
            "ORDER BY company_id, onu_id, ts"
        )).fetchall()
        bucket: Dict[tuple, List[tuple]] = {}
        for cid, onu_id, sec, rx in samples_rows:
            bucket.setdefault((cid, onu_id), []).append((int(sec), float(rx)))

        for (cid, onu_id), samples in bucket.items():
            out["scanned"] += 1
            drop_thr, crit_dbm = thresholds.get(cid, (3.0, -27.0))
            slope = _compute_slope(samples)
            if slope is None:
                continue
            # Negative slope == signal getting worse (rx_power decreasing).
            #   if abs(slope) >= threshold/day -> degradation event
            if abs(slope) < drop_thr:
                # Close any open event for this ONU if signal is steady.
                conn.execute(_t(
                    "UPDATE signal_degradation_events_v2 "
                    "SET closed_at = NOW() "
                    "WHERE company_id=:c AND onu_id=:o AND closed_at IS NULL"
                ), {"c": cid, "o": onu_id})
                out["closed"] += int(conn.execute(_t(
                    "SELECT 0"
                )).rowcount or 0)
                continue
            # Predict days-until-fail vs critical level.
            last_rx = samples[-1][1]
            margin = last_rx - crit_dbm   # how many dB above critical
            if slope < 0:
                days_left = max(0.0, margin / abs(slope))
            else:
                days_left = 99.0   # power increasing — unusual, no impending fail
            # Idempotent insert / update.
            existing = conn.execute(_t(
                "SELECT id FROM signal_degradation_events_v2 "
                "WHERE company_id=:c AND onu_id=:o AND closed_at IS NULL "
                "ORDER BY id DESC LIMIT 1"
            ), {"c": cid, "o": onu_id}).fetchone()
            if existing:
                conn.execute(_t(
                    "UPDATE signal_degradation_events_v2 SET "
                    " slope_db_per_day=:s, predicted_fail_in_days=:d "
                    "WHERE id=:id"
                ), {"s": float(slope), "d": float(days_left), "id": existing[0]})
                out["updated"] += 1
            else:
                conn.execute(_t(
                    "INSERT INTO signal_degradation_events_v2 "
                    "(company_id, onu_id, slope_db_per_day, "
                    "predicted_fail_in_days, opened_at) "
                    "VALUES (:c, :o, :s, :d, NOW())"
                ), {"c": cid, "o": onu_id, "s": float(slope),
                    "d": float(days_left)})
                out["opened"] += 1
    return out


_THREAD = None
_LOCK = threading.Lock()

def start_background(interval_seconds: int = 300):
    """Idempotent — only spawns one daemon."""
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        def _loop():
            # Acquire a PG-level advisory lock so only one worker process
            # (across all uvicorn workers) actually runs the loop body.
            # Lock key 0x5F571CFA (arbitrary u32).
            try:
                from database import engine
                holder = engine.connect()
                got = holder.execute(_t(
                    "SELECT pg_try_advisory_lock(:k)"
                ), {"k": 0x5F571CFA}).scalar()
                if not got:
                    log.info("[failpred] another worker holds the lock — exit")
                    try: holder.close()
                    except Exception: pass
                    return
                log.info("[failpred] advisory lock acquired")
            except Exception as _le:
                log.warning(f"[failpred] lock-acquire failed, running anyway: {_le}")
                holder = None
            while True:
                try:
                    res = _run_once()
                    log.info(f"[failpred] {res}")
                except Exception as e:
                    log.exception(f"[failpred] pass error: {e}")
                time.sleep(interval_seconds)
        _THREAD = threading.Thread(target=_loop, daemon=True,
                                    name="failpred-worker")
        _THREAD.start()
        log.info(f"[failpred] worker started, interval={interval_seconds}s")
        return True
