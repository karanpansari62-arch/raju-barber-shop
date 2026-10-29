import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
from config import (
    LOCAL_TZ,
    OPEN_TIME,
    CLOSE_TIME,
    BREAK_START,
    BREAK_END,
    WEEKLY_OFF_DAY_INT,
    WEEKLY_OFF_DAY_STR,
    MAX_DAYS_AHEAD,
    SLOT_GRANULARITY_MINUTES
)

def get_now_ist() -> datetime:
    """Returns current timestamp in Asia/Kolkata timezone."""
    return datetime.now(LOCAL_TZ)

def check_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    """
    Standard half-open interval overlap check [start1, end1) and [start2, end2).
    True if intervals intersect; False if contiguous (e.g. end1 == start2).
    """
    return start1 < end2 and end1 > start2

def get_available_slots(
    conn: sqlite3.Connection,
    service_id: int,
    target_date: date,
    now_ist: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Computes available 15-minute start times for single barber/chair.
    
    Rules:
    - 15-minute start time grid.
    - Fits completely within OPEN_TIME and CLOSE_TIME.
    - Excludes weekly off day.
    - Excludes dates in the past or beyond MAX_DAYS_AHEAD.
    - Excludes past times for today.
    - Excludes any slot overlapping daily break [BREAK_START, BREAK_END).
    - Excludes any slot overlapping records in blocked_times.
    - Excludes any slot overlapping existing non-cancelled bookings.
    """
    if now_ist is None:
        now_ist = get_now_ist()

    today_ist = now_ist.date()
    max_date = today_ist + timedelta(days=MAX_DAYS_AHEAD)

    # 1. Fetch active service details
    service = conn.execute(
        "SELECT id, name, duration_minutes, price_inr, is_active FROM services WHERE id = ?",
        (service_id,)
    ).fetchone()
    if not service or not service["is_active"]:
        raise ValueError("Service not found or is currently inactive.")

    # 2. Date boundary checks
    if target_date < today_ist:
        return {
            "date": target_date.isoformat(),
            "service_id": service["id"],
            "service_name": service["name"],
            "duration_minutes": service["duration_minutes"],
            "price_inr": service["price_inr"],
            "slots": [],
            "is_off_day": False,
            "message": "Past dates cannot be booked."
        }

    if target_date > max_date:
        return {
            "date": target_date.isoformat(),
            "service_id": service["id"],
            "service_name": service["name"],
            "duration_minutes": service["duration_minutes"],
            "price_inr": service["price_inr"],
            "slots": [],
            "is_off_day": False,
            "message": f"Bookings can only be made up to {MAX_DAYS_AHEAD} days in advance."
        }

    # 3. Weekly off day check
    if target_date.weekday() == WEEKLY_OFF_DAY_INT:
        return {
            "date": target_date.isoformat(),
            "service_id": service["id"],
            "service_name": service["name"],
            "duration_minutes": service["duration_minutes"],
            "price_inr": service["price_inr"],
            "slots": [],
            "is_off_day": True,
            "message": f"Shop is closed on {WEEKLY_OFF_DAY_STR}s (Weekly Off)."
        }

    # 4. Day boundaries
    open_dt = datetime.combine(target_date, OPEN_TIME, tzinfo=LOCAL_TZ)
    close_dt = datetime.combine(target_date, CLOSE_TIME, tzinfo=LOCAL_TZ)
    break_start_dt = datetime.combine(target_date, BREAK_START, tzinfo=LOCAL_TZ)
    break_end_dt = datetime.combine(target_date, BREAK_END, tzinfo=LOCAL_TZ)

    # 5. Fetch existing non-cancelled bookings for target_date
    bookings_rows = conn.execute(
        """
        SELECT start_at, end_at FROM bookings
        WHERE status != 'cancelled'
          AND start_at < ?
          AND end_at > ?
        """,
        (close_dt.isoformat(), open_dt.isoformat())
    ).fetchall()

    booked_intervals = [
        (datetime.fromisoformat(r["start_at"]), datetime.fromisoformat(r["end_at"]))
        for r in bookings_rows
    ]

    # 6. Fetch active blocked_times for target_date
    blocked_rows = conn.execute(
        """
        SELECT start_at, end_at FROM blocked_times
        WHERE start_at < ?
          AND end_at > ?
        """,
        (close_dt.isoformat(), open_dt.isoformat())
    ).fetchall()

    blocked_intervals = [
        (datetime.fromisoformat(r["start_at"]), datetime.fromisoformat(r["end_at"]))
        for r in blocked_rows
    ]

    # 7. Generate candidate slots on a 15-minute grid
    duration = timedelta(minutes=service["duration_minutes"])
    step = timedelta(minutes=SLOT_GRANULARITY_MINUTES)

    slots: List[Dict[str, str]] = []
    current_start = open_dt

    while current_start + duration <= close_dt:
        current_end = current_start + duration

        # Skip past times for today
        if target_date == today_ist and current_start <= now_ist:
            current_start += step
            continue

        # Check overlap with daily break
        if check_overlap(current_start, current_end, break_start_dt, break_end_dt):
            current_start += step
            continue

        # Check overlap with emergency/owner blocked times
        is_blocked = any(check_overlap(current_start, current_end, b_s, b_e) for b_s, b_e in blocked_intervals)
        if is_blocked:
            current_start += step
            continue

        # Check overlap with existing active bookings
        is_booked = any(check_overlap(current_start, current_end, b_s, b_e) for b_s, b_e in booked_intervals)
        if is_booked:
            current_start += step
            continue

        # Slot is available
        slots.append({
            "time": current_start.strftime("%H:%M"),
            "iso_start": current_start.isoformat()
        })
        current_start += step

    return {
        "date": target_date.isoformat(),
        "service_id": service["id"],
        "service_name": service["name"],
        "duration_minutes": service["duration_minutes"],
        "price_inr": service["price_inr"],
        "slots": slots,
        "is_off_day": False,
        "message": None if slots else "No slots available for this date."
    }
