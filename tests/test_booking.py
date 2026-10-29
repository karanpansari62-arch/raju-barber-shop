import concurrent.futures
import hashlib
from datetime import datetime, date, timedelta
from config import LOCAL_TZ, WEEKLY_OFF_DAY_INT, BREAK_START_STR
from database import get_connection

def test_booking_overlap_logic(client, test_db, get_valid_future_date):
    """
    Requirement 9:
    10:00-10:45 booked -> 10:30 rejected (409), 10:45 allowed.
    """
    conn = get_connection(test_db)
    combo = conn.execute("SELECT id FROM services WHERE name = 'Haircut + Beard Combo'").fetchone() # 45 min
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone() # 30 min

    target_date = get_valid_future_date(days_ahead=2)
    start_1000 = f"{target_date.isoformat()}T10:00:00+05:30"
    start_1030 = f"{target_date.isoformat()}T10:30:00+05:30"
    start_1045 = f"{target_date.isoformat()}T10:45:00+05:30"

    # 1. Book 10:00 - 10:45
    r1 = client.post("/api/book", json={
        "service_id": combo["id"],
        "start_at": start_1000,
        "name": "Customer One",
        "phone": "9876543210"
    })
    assert r1.status_code == 200, r1.text
    assert r1.json()["end_at"] == f"{target_date.isoformat()}T10:45:00+05:30"

    # 2. Try to book 10:30 (overlaps with 10:00-10:45) -> MUST FAIL 409
    r2 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": start_1030,
        "name": "Customer Overlap",
        "phone": "9876543211"
    })
    assert r2.status_code == 409
    assert "already been booked" in r2.json()["detail"].lower()

    # 3. Try to book 10:45 (immediately after) -> MUST SUCCEED 200
    r3 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": start_1045,
        "name": "Customer After",
        "phone": "9876543212"
    })
    assert r3.status_code == 200
    assert r3.json()["start_at"] == start_1045
    conn.close()

def test_daily_break_overlapping_slots_rejected(client, test_db, get_valid_future_date):
    """
    Requirement 9:
    Slot overlapping daily break (14:00-15:00) is rejected.
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone() # 30 min
    target_date = get_valid_future_date(days_ahead=3)

    # 1. Slot starting inside break (14:15)
    inside_break_iso = f"{target_date.isoformat()}T14:15:00+05:30"
    r1 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": inside_break_iso,
        "name": "Break Tester",
        "phone": "9876543210"
    })
    assert r1.status_code == 400
    assert "break" in r1.json()["detail"].lower()

    # 2. Slot starting before break but ending inside break (13:45 for 30-min haircut ends at 14:15)
    overlap_break_iso = f"{target_date.isoformat()}T13:45:00+05:30"
    r2 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": overlap_break_iso,
        "name": "Break Overlap Tester",
        "phone": "9876543210"
    })
    assert r2.status_code == 400
    assert "break" in r2.json()["detail"].lower()

    # 3. Verify availability endpoint also filters out break times
    avail = client.get(f"/api/availability?service_id={haircut['id']}&date={target_date.isoformat()}").json()
    times = [s["time"] for s in avail["slots"]]
    assert "13:45" not in times
    assert "14:00" not in times
    assert "14:15" not in times
    assert "14:30" not in times
    assert "14:45" not in times
    # Slot right at break end (15:00) should be available
    assert "15:00" in times
    conn.close()

def test_concurrent_booking_attempts(client, test_db, get_valid_future_date):
    """
    Requirement 9:
    Two concurrent booking attempts for the same slot -> exactly one succeeds.
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=4)
    slot_iso = f"{target_date.isoformat()}T11:00:00+05:30"

    p1 = {
        "service_id": haircut["id"],
        "start_at": slot_iso,
        "name": "User Alpha",
        "phone": "9876543201"
    }
    p2 = {
        "service_id": haircut["id"],
        "start_at": slot_iso,
        "name": "User Beta",
        "phone": "9876543202"
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(lambda: client.post("/api/book", json=p1))
        f2 = executor.submit(lambda: client.post("/api/book", json=p2))
        res1 = f1.result()
        res2 = f2.result()

    statuses = [res1.status_code, res2.status_code]
    assert statuses.count(200) == 1, f"Expected exactly one 200, got: {statuses}"
    assert statuses.count(409) == 1, f"Expected exactly one 409, got: {statuses}"

    count = conn.execute("SELECT COUNT(*) as cnt FROM bookings WHERE start_at = ?", (slot_iso,)).fetchone()["cnt"]
    assert count == 1
    conn.close()

def test_past_slot_closed_hours_and_weekly_off_rejected(client, test_db):
    """
    Requirement 9:
    Past slot, closed hours, and weekly off day must be rejected.
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()

    # 1. Past slot (yesterday)
    past_date = date.today() - timedelta(days=1)
    past_iso = f"{past_date.isoformat()}T12:00:00+05:30"
    r_past = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": past_iso,
        "name": "Past Tester",
        "phone": "9876543210"
    })
    assert r_past.status_code == 400
    assert "past" in r_past.json()["detail"].lower()

    # 2. Closed hours (e.g. 21:00 when shop closes at 21:00)
    future_date = date.today() + timedelta(days=2)
    if future_date.weekday() == WEEKLY_OFF_DAY_INT:
        future_date += timedelta(days=1)
    closed_iso = f"{future_date.isoformat()}T21:00:00+05:30"
    r_closed = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": closed_iso,
        "name": "Closed Tester",
        "phone": "9876543210"
    })
    assert r_closed.status_code == 400
    assert "operating hours" in r_closed.json()["detail"].lower()

    # 3. Weekly off day (Tuesday)
    target = date.today() + timedelta(days=1)
    while target.weekday() != WEEKLY_OFF_DAY_INT:
        target += timedelta(days=1)
    off_iso = f"{target.isoformat()}T11:00:00+05:30"
    r_off = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": off_iso,
        "name": "OffDay Tester",
        "phone": "9876543210"
    })
    assert r_off.status_code == 400
    assert "weekly off" in r_off.json()["detail"].lower()
    conn.close()

def test_cancelled_booking_frees_the_slot(client, test_db, get_valid_future_date):
    """
    Requirement 9:
    Cancelled booking frees the slot for other customers.
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=2)
    start_iso = f"{target_date.isoformat()}T16:00:00+05:30"

    # 1. Book the slot
    book_resp = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": start_iso,
        "name": "First Customer",
        "phone": "9876543210"
    })
    assert book_resp.status_code == 200
    token = book_resp.json()["cancel_token"]

    # 2. Verify slot is NOT available in /api/availability
    avail1 = client.get(f"/api/availability?service_id={haircut['id']}&date={target_date.isoformat()}").json()
    assert "16:00" not in [s["time"] for s in avail1["slots"]]

    # 3. Cancel the booking
    cancel_resp = client.post(f"/api/cancel/{token}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    # 4. Verify slot is now available again
    avail2 = client.get(f"/api/availability?service_id={haircut['id']}&date={target_date.isoformat()}").json()
    assert "16:00" in [s["time"] for s in avail2["slots"]]

    # 5. Another customer can now book this exact slot
    book_resp2 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": start_iso,
        "name": "Second Customer",
        "phone": "9876543299"
    })
    assert book_resp2.status_code == 200
    assert book_resp2.json()["start_at"] == start_iso
    conn.close()

def test_spam_protection_max_two_active_bookings(client, test_db, get_valid_future_date):
    """
    Requirement 5:
    Max 2 active upcoming bookings per phone number (spam protection).
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=2)
    phone = "9876543210"

    # Booking 1
    r1 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": f"{target_date.isoformat()}T10:00:00+05:30",
        "name": "Spam User",
        "phone": phone
    })
    assert r1.status_code == 200

    # Booking 2
    r2 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": f"{target_date.isoformat()}T10:30:00+05:30",
        "name": "Spam User",
        "phone": phone
    })
    assert r2.status_code == 200

    # Booking 3 -> MUST BE REJECTED (400)
    r3 = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": f"{target_date.isoformat()}T11:00:00+05:30",
        "name": "Spam User",
        "phone": phone
    })
    assert r3.status_code == 400
    assert "2 active bookings" in r3.json()["detail"].lower()
    conn.close()

def test_token_hash_stored_in_db_not_raw(client, test_db, get_valid_future_date):
    """
    Requirement 5 & 7:
    Store ONLY SHA-256 hash in DB, show token once in cancel link.
    """
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=2)

    res = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": f"{target_date.isoformat()}T17:00:00+05:30",
        "name": "Token User",
        "phone": "9876543210"
    })
    assert res.status_code == 200
    raw_token = res.json()["cancel_token"]
    expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    row = conn.execute("SELECT cancel_token_hash FROM bookings WHERE id = ?", (res.json()["booking_id"],)).fetchone()
    assert row["cancel_token_hash"] == expected_hash
    assert row["cancel_token_hash"] != raw_token
    conn.close()

def test_security_headers_present(client):
    """
    Requirement 7:
    Security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
    """
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

def test_rate_limiting_enforced(test_db, get_valid_future_date):
    """
    Requirement 7:
    slowapi rate limit: booking 5/min/IP, cancel 10/min/IP.
    """
    from config import limiter
    from fastapi.testclient import TestClient
    from main import app
    from database import get_connection, get_db

    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=2)
    start_iso = f"{target_date.isoformat()}T18:00:00+05:30"

    def override_get_db():
        c = get_connection(test_db)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.enabled = True
    limiter.reset()

    with TestClient(app) as test_client:
        statuses = []
        for i in range(6):
            r = test_client.post("/api/book", json={
                "service_id": haircut["id"],
                "start_at": start_iso,
                "name": f"Rate Limit {i}",
                "phone": f"98765432{i:02d}"
            })
            statuses.append(r.status_code)

    limiter.enabled = False
    app.dependency_overrides.clear()
    conn.close()

    # 6th request from same IP must hit 429
    assert 429 in statuses, f"Expected 429 rate limit error, got: {statuses}"
