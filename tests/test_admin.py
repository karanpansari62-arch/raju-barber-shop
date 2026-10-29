from datetime import datetime, date, timedelta
from database import get_connection
from config import LOCAL_TZ

def test_admin_login_failure(client):
    res = client.post("/admin/login", data={"password": "wrong_password"})
    assert res.status_code == 400
    assert "Galat password" in res.text

def test_admin_login_success_and_logout(client):
    res = client.post("/admin/login", data={"password": "raju123"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/admin"
    assert "admin_session" in res.cookies

    # Access /admin with cookie
    dash_res = client.get("/admin")
    assert dash_res.status_code == 200
    assert "Total Bookings" in dash_res.text

    # Logout
    logout_res = client.get("/admin/logout", follow_redirects=False)
    assert logout_res.status_code == 303
    assert logout_res.headers["location"] == "/admin/login"

    # Subsequent request without cookie should redirect
    client.cookies.clear()
    unauth = client.get("/admin", follow_redirects=False)
    assert unauth.status_code == 303
    assert unauth.headers["location"] == "/admin/login"

def test_admin_update_booking_status(client, test_db, get_valid_future_date):
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id, price_inr FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=2)
    start_iso = f"{target_date.isoformat()}T11:00:00+05:30"

    # 1. Customer books
    book_res = client.post("/api/book", json={
        "service_id": haircut["id"],
        "start_at": start_iso,
        "name": "Raju Customer",
        "phone": "9876543210"
    })
    assert book_res.status_code == 200
    b_id = book_res.json()["booking_id"]

    # 2. Admin logs in
    client.post("/admin/login", data={"password": "raju123"})

    # 3. View dashboard
    dash = client.get(f"/admin?booking_date={target_date.isoformat()}")
    assert dash.status_code == 200
    assert "Raju Customer" in dash.text

    # 4. Mark completed
    update_res = client.post(
        f"/admin/bookings/{b_id}/status",
        data={"status": "completed", "redirect_date": target_date.isoformat()},
        follow_redirects=False
    )
    assert update_res.status_code == 303

    # Verify status in database
    row = conn.execute("SELECT status FROM bookings WHERE id = ?", (b_id,)).fetchone()
    assert row["status"] == "completed"

    # Verify dashboard revenue reflects completion
    dash_updated = client.get(f"/admin?booking_date={target_date.isoformat()}")
    assert f"₹{haircut['price_inr']}" in dash_updated.text
    conn.close()

def test_admin_emergency_block_flow(client, test_db, get_valid_future_date):
    conn = get_connection(test_db)
    haircut = conn.execute("SELECT id FROM services WHERE name = 'Haircut'").fetchone()
    target_date = get_valid_future_date(days_ahead=3)

    # Admin logs in
    client.post("/admin/login", data={"password": "raju123"})

    # Add emergency block 16:00 to 18:00
    block_res = client.post(
        "/admin/blocks/add",
        data={
            "block_date": target_date.isoformat(),
            "start_time": "16:00",
            "end_time": "18:00",
            "reason": "Light cut / Doctor visit"
        },
        follow_redirects=False
    )
    assert block_res.status_code == 303

    # Check that block is in DB
    block_row = conn.execute("SELECT id FROM blocked_times WHERE reason LIKE '%Doctor%'").fetchone()
    assert block_row is not None
    block_id = block_row["id"]

    # Customer checks availability: 16:00 to 17:30 must NOT be in slots
    avail = client.get(f"/api/availability?service_id={haircut['id']}&date={target_date.isoformat()}").json()
    slot_times = [s["time"] for s in avail["slots"]]
    assert "16:00" not in slot_times
    assert "16:30" not in slot_times
    assert "17:00" not in slot_times
    assert "17:30" not in slot_times
    assert "18:00" in slot_times

    # Admin unblocks / deletes the block
    del_res = client.post(f"/admin/blocks/{block_id}/delete", follow_redirects=False)
    assert del_res.status_code == 303

    # Verify slots are available again
    avail_after = client.get(f"/api/availability?service_id={haircut['id']}&date={target_date.isoformat()}").json()
    slot_times_after = [s["time"] for s in avail_after["slots"]]
    assert "16:00" in slot_times_after
    conn.close()

def test_admin_services_crud(client, test_db):
    conn = get_connection(test_db)
    client.post("/admin/login", data={"password": "raju123"})

    # Add new service
    add_res = client.post(
        "/admin/services/add",
        data={"name": "D-Tan Facial", "duration_minutes": 35, "price_inr": 250},
        follow_redirects=False
    )
    assert add_res.status_code == 303
    row = conn.execute("SELECT id, is_active FROM services WHERE name = 'D-Tan Facial'").fetchone()
    assert row is not None
    assert row["is_active"] == 1

    # Toggle inactive
    client.post(f"/admin/services/{row['id']}/toggle", follow_redirects=False)
    row_toggled = conn.execute("SELECT is_active FROM services WHERE id = ?", (row["id"],)).fetchone()
    assert row_toggled["is_active"] == 0
    conn.close()
