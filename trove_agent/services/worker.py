"""Background worker: timers, reminders, delivery retries, cleanup.

Run as its own process in production (``python -m trove_agent worker``) or as a
thread inside the web process in development (TROVE_RUN_WORKER=true).

Each tick:
1. delivers pending notifications (retries failed ones)
2. re-evaluates open release requests whose cooling-off period has ended,
   so release no longer depends on someone clicking "re-check"
3. sends owner proof-of-life reminders when the check-in interval passes,
   and warns nominees when the owner has missed two intervals (this never
   releases anything by itself; a verified death claim is still required)
4. deletes expired sessions and one-time codes

With PostgreSQL an advisory lock guarantees only one worker runs a tick.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from trove_agent.services.legacy import LegacyService
from trove_agent.services.notify import deliver_pending
from trove_agent.core.store import TroveService, now

log = logging.getLogger("trove.worker")
LOCK_ID = 7_041_966


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def tick(service: TroveService, legacy: LegacyService, channels: Any = None) -> dict[str, int]:
    stats = {"released": 0, "reminders": 0, "escalations": 0, "sent": 0, "failed": 0}
    lock = None
    if service.db.dialect == "postgres":
        lock = service._connect()
        if not lock.execute("SELECT pg_try_advisory_lock(?)", (LOCK_ID,)).fetchone()[0]:
            lock.close()
            return stats
    try:
        current = datetime.now(timezone.utc)
        with service._connect() as db:
            due = db.execute(
                "SELECT case_id, id FROM release_requests WHERE status = 'open' AND waiting_until <= ?",
                (now(),)).fetchall()
        for row in due:
            if legacy.evaluate(row["case_id"], row["id"])["status"] == "released":
                stats["released"] += 1

        with service._connect() as db:
            policies = db.execute(
                "SELECT * FROM legacy_policy WHERE owner_email IS NOT NULL OR owner_phone IS NOT NULL"
            ).fetchall()
            for policy in policies:
                interval = timedelta(days=policy["checkin_interval_days"])
                last = _parse(policy["last_owner_checkin"]) or _parse(policy["updated_at"])
                if last is None or current - last < interval:
                    continue
                reminded = _parse(policy["last_reminder_at"])
                if reminded is None or current - reminded >= timedelta(days=7):
                    legacy._notify_owner(
                        db, policy["case_id"],
                        "Trove routine check: please sign in and press 'I am alive' to confirm "
                        "you are well. Your nominees are not notified unless you miss two check-ins.")
                    db.execute("UPDATE legacy_policy SET last_reminder_at = ? WHERE case_id = ?",
                               (now(), policy["case_id"]))
                    stats["reminders"] += 1
                if current - last >= 2 * interval and not policy["escalated_at"]:
                    legacy._notify_nominees(
                        db, policy["case_id"],
                        "The owner of a Trove legacy vault you are a nominee for has not confirmed "
                        "they are well for an extended period. Please check on them. If they have "
                        "passed away, sign in to report it; release still needs verification.")
                    db.execute("UPDATE legacy_policy SET escalated_at = ? WHERE case_id = ?",
                               (now(), policy["case_id"]))
                    legacy._audit(db, policy["case_id"], "system", "owner.checkin_escalated", {})
                    stats["escalations"] += 1
            db.execute("DELETE FROM nominee_sessions WHERE expires_at <= ?", (now(),))
            db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now(),))
            db.execute("DELETE FROM otp_challenges WHERE expires_at <= ? OR consumed = 1", (now(),))
            if legacy.service.db.dialect and db.columns("staff_sessions"):
                db.execute("DELETE FROM staff_sessions WHERE expires_at <= ?", (now(),))
        stats.update(deliver_pending(service, channels))
    finally:
        if lock is not None:
            lock.execute("SELECT pg_advisory_unlock(?)", (LOCK_ID,))
            lock.close()
    if any(stats.values()):
        log.info("worker tick", extra={"stats": stats})
    return stats


def run_forever(service: TroveService, legacy: LegacyService, interval: int = 60,
                stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    log.info("worker started", extra={"interval": interval})
    while not stop.is_set():
        try:
            tick(service, legacy)
        except Exception:  # keep the worker alive; errors are logged
            log.exception("worker tick failed")
        stop.wait(interval)


def start_thread(service: TroveService, legacy: LegacyService, interval: int) -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(service, legacy, interval, stop),
                     daemon=True, name="trove-worker").start()
    return stop
