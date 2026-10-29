import sqlite3
import hashlib
import secrets
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any
from config import (
    LOCAL_TZ,
    OPEN_TIME,
    CLOSE_TIME,
    BREAK_START,
    BREAK_END,
    WEEKLY_OFF_DAY_INT,
    WEEKLY_OFF_DAY_STR,
    MAX_DAYS_AHEAD
)
from availability import get_now_ist, check_overlap

class BookingConflictError(Exception):
    """Raised when slot is already booked or blocked (HTTP 409)."""
    pass

class ValidationError(Exception):
    """Raised when validation fails (HTTP 400)."""
    pass

class NotFoundError(Exception):
    """Raised when entity not found (HTTP 404)."""
    pass

def hash_token(raw_token: str) -> str:
    """Computes SHA-256 hex digest of the raw cancellation token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

def create_booking(
    conn: sqlite3.Connection,
    service_id: int,
    start_at_iso: str,
    customer_name: str,
    phone: str,
    now_ist: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Creates a single-chair booking using BEGIN IMMEDIATE for atomic concurrency control.
    
    Why BEGIN IMMEDIATE?
    In SQLite, standard transactions start as DEFERRED (read-only) and upgrade to a
    write lock only on INSERT. If two customers request the same slot at the exact
    same instant, both would read that the slot is available, leading to double-booking
    or locking exceptions. BEGIN IMMEDIATE acquires an immediate reserved write lock,
    strictly serializing slot verification and insertion across concurrent requests.
    """
    if now_ist is None:
        now_ist = get_now_ist()

    # 1. Parse and ensure timezone awareness in Asia/Kolkata
    try:
        start_dt = datetime.fromisoformat(start_at_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
        else:
            start_dt = start_dt.astimezone(LOCAL_TZ)
    except Exception as e:
        raise ValidationError(f"Invalid datetime format: {e}")

    booking_date = start_dt.date()
    today_ist = now_ist.date()

    # 2. Date boundary checks
    if start_dt <= now_ist:
        raise ValidationError("Cannot book slots in the past.")

    if booking_date > today_ist + timedelta(days=MAX_DAYS_AHEAD):
        raise ValidationError(f"Cannot book more than {MAX_DAYS_AHEAD} days in advance.")

    if booking_date.weekday() == WEEKLY_OFF_DAY_INT:
        raise ValidationError(f"Shop is closed on {WEEKLY_OFF_DAY_STR}s (Weekly Off).")

    # 3. Retrieve service
    service = conn.execute(
        "SELECT id, name, duration_minutes, price_inr, is_active FROM services WHERE id = ?",
        (service_id,)
    ).fetchone()
    if not service or not service["is_active"]:
        raise NotFoundError("Selected service was not found or is currently inactive.")

    end_dt = start_dt + timedelta(minutes=service["duration_minutes"])

    # 4. Operating hours boundary check
    open_dt = datetime.combine(booking_date, OPEN_TIME, tzinfo=LOCAL_TZ)
    close_dt = datetime.combine(booking_date, CLOSE_TIME, tzinfo=LOCAL_TZ)

    if start_dt < open_dt or end_dt > close_dt:
        raise ValidationError("Booking falls outside shop operating hours.")

    # 5. Daily break check
    break_start_dt = datetime.combine(booking_date, BREAK_START, tzinfo=LOCAL_TZ)
    break_end_dt = datetime.combine(booking_date, BREAK_END, tzinfo=LOCAL_TZ)

    if check_overlap(start_dt, end_dt, break_start_dt, break_end_dt):
        raise ValidationError("Selected time overlaps with the daily afternoon break.")

    # 6. Spam protection: max 2 active upcoming bookings per phone number
    active_count = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM bookings
        WHERE phone = ?
          AND status = 'confirmed'
          AND end_at > ?
        """,
        (phone, now_ist.isoformat())
    ).fetchone()

    if active_count and active_count["cnt"] >= 2:
        raise ValidationError("You already have 2 active bookings. Please complete or cancel existing appointments first.")

    # 7. Atomic Write Lock & Overlap Check
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Check conflict with existing non-cancelled bookings
        conflict = conn.execute(
            """
            SELECT 1 FROM bookings
            WHERE status != 'cancelled'
              AND start_at < ?
              AND end_at > ?
            LIMIT 1
            """,
            (end_dt.isoformat(), start_dt.isoformat())
        ).fetchone()

        if conflict:
            raise BookingConflictError("This slot has already been booked. Please choose another time.")

        # Check conflict with owner emergency/blocked times
        blocked = conn.execute(
            """
            SELECT 1 FROM blocked_times
            WHERE start_at < ?
              AND end_at > ?
            LIMIT 1
            """,
            (end_dt.isoformat(), start_dt.isoformat())
        ).fetchone()

        if blocked:
            raise BookingConflictError("This time slot is blocked and unavailable.")

        # 8. Cryptographically secure cancellation token
        # Raw token is given once to the user in the URL; only its SHA-256 hash is saved
        raw_token = secrets.token_urlsafe(32)
        token_digest = hash_token(raw_token)
        created_at_iso = now_ist.isoformat()

        cursor = conn.execute(
            """
            INSERT INTO bookings (
                service_id, customer_name, phone,
                start_at, end_at, status, cancel_token_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?)
            """,
            (
                service["id"],
                customer_name,
                phone,
                start_dt.isoformat(),
                end_dt.isoformat(),
                token_digest,
                created_at_iso
            )
        )
        booking_id = cursor.lastrowid
        conn.execute("COMMIT")

        return {
            "booking_id": booking_id,
            "service_name": service["name"],
            "duration_minutes": service["duration_minutes"],
            "price_inr": service["price_inr"],
            "customer_name": customer_name,
            "phone": phone,
            "start_at": start_dt.isoformat(),
            "end_at": end_dt.isoformat(),
            "status": "confirmed",
            "cancel_token": raw_token
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise

def get_booking_by_token(conn: sqlite3.Connection, raw_token: str) -> Dict[str, Any]:
    """Retrieves booking details using the SHA-256 hash of the cancel token."""
    token_digest = hash_token(raw_token)
    query = """
    SELECT b.id, b.customer_name, b.phone, b.start_at, b.end_at, b.status, b.created_at,
           s.name as service_name, s.duration_minutes, s.price_inr
    FROM bookings b
    JOIN services s ON b.service_id = s.id
    WHERE b.cancel_token_hash = ?
    """
    row = conn.execute(query, (token_digest,)).fetchone()
    if not row:
        raise NotFoundError("Booking not found or invalid cancellation link.")
    return dict(row)

def cancel_booking(conn: sqlite3.Connection, raw_token: str) -> Dict[str, Any]:
    """Cancels a booking using the raw token in an idempotent transaction."""
    token_digest = hash_token(raw_token)
    conn.execute("BEGIN IMMEDIATE")
    try:
        booking = conn.execute(
            "SELECT id, status, customer_name, start_at FROM bookings WHERE cancel_token_hash = ?",
            (token_digest,)
        ).fetchone()

        if not booking:
            raise NotFoundError("Booking not found or invalid cancellation link.")

        if booking["status"] == "cancelled":
            conn.execute("COMMIT")
            return {
                "booking_id": booking["id"],
                "status": "cancelled",
                "message": "Appointment is already cancelled."
            }

        conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ?",
            (booking["id"],)
        )
        conn.execute("COMMIT")
        return {
            "booking_id": booking["id"],
            "status": "cancelled",
            "message": "Appointment cancelled successfully."
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise
