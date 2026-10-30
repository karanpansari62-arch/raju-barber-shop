from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional
from fastapi import FastAPI, Request, Depends, HTTPException, Query, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import sqlite3

from config import (
    SHOP_NAME,
    SHOP_PHONE,
    OPEN_TIME_STR,
    CLOSE_TIME_STR,
    BREAK_START_STR,
    BREAK_END_STR,
    WEEKLY_OFF_DAY_STR,
    WEEKLY_OFF_DAY_INT,
    MAX_DAYS_AHEAD,
    limiter
)
from database import init_db, get_db, get_connection
from models import BookingCreateRequest, BookingResponse, AvailabilityResponse
from availability import get_available_slots, get_now_ist
from booking_service import (
    create_booking,
    cancel_booking,
    get_booking_by_token,
    BookingConflictError,
    ValidationError,
    NotFoundError
)
from admin_auth import get_admin_token, is_admin_authenticated, require_admin
from config import ADMIN_PASSWORD, LOCAL_TZ

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database tables exist on startup
    init_db()
    # Auto-seed standard services if empty
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        if count == 0:
            from seed import seed_services
            seed_services()
    except Exception as e:
        print(f"Warning during auto-seed: {e}")
    finally:
        conn.close()
    yield

app = FastAPI(
    title="Bhusawal Barber Shop Booking Engine",
    description="Single-chair online appointment booking system",
    version="2.0.0",
    lifespan=lifespan
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security hardening middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Static CSS
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==================== Customer UI Views ====================

@app.get("/", response_class=HTMLResponse)
def index_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """
    Main customer booking page.
    Renders active services, shop hours, and allowed 14-day date range.
    """
    services = conn.execute(
        "SELECT id, name, duration_minutes, price_inr FROM services WHERE is_active = 1 ORDER BY price_inr ASC"
    ).fetchall()

    now_ist = get_now_ist()
    min_date = now_ist.date().isoformat()
    max_date = (now_ist.date() + timedelta(days=MAX_DAYS_AHEAD)).isoformat()

    return templates.TemplateResponse(
        request=request,
        name="book.html",
        context={
            "shop_name": SHOP_NAME,
            "shop_phone": SHOP_PHONE,
            "open_time": OPEN_TIME_STR,
            "close_time": CLOSE_TIME_STR,
            "break_start": BREAK_START_STR,
            "break_end": BREAK_END_STR,
            "weekly_off_day": WEEKLY_OFF_DAY_STR,
            "weekly_off_day_int": WEEKLY_OFF_DAY_INT,
            "services": [dict(s) for s in services],
            "min_date": min_date,
            "max_date": max_date
        }
    )

@app.get("/cancel/{token}", response_class=HTMLResponse)
@limiter.limit("10/minute")
def cancel_view(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    """
    Appointment cancellation review screen.
    Phone number is partially masked to preserve customer privacy.
    Rate limited to 10/min/IP.
    """
    try:
        booking = get_booking_by_token(conn, token)
        phone = booking.get("phone", "")
        masked_phone = phone[:2] + "******" + phone[-2:] if len(phone) >= 4 else phone

        return templates.TemplateResponse(
            request=request,
            name="cancel.html",
            context={
                "shop_name": SHOP_NAME,
                "booking": booking,
                "masked_phone": masked_phone,
                "token": token,
                "cancelled": booking["status"] == "cancelled"
            }
        )
    except NotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"message": "Appointment not found ya cancel link invalid hai."},
            status_code=404
        )

@app.post("/cancel/{token}", response_class=HTMLResponse)
@limiter.limit("10/minute")
def cancel_confirm(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    """
    Executes appointment cancellation and shows confirmation.
    Rate limited to 10/min/IP.
    """
    try:
        booking = get_booking_by_token(conn, token)
        cancel_result = cancel_booking(conn, token)
        phone = booking.get("phone", "")
        masked_phone = phone[:2] + "******" + phone[-2:] if len(phone) >= 4 else phone
        booking["status"] = "cancelled"

        return templates.TemplateResponse(
            request=request,
            name="cancel.html",
            context={
                "shop_name": SHOP_NAME,
                "booking": booking,
                "masked_phone": masked_phone,
                "token": token,
                "cancelled": True,
                "message": cancel_result.get("message", "Appointment cancelled successfully.")
            }
        )
    except NotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"message": "Appointment not found ya cancel link invalid hai."},
            status_code=404
        )

# ==================== REST API Endpoints ====================

@app.get("/api/availability", response_model=AvailabilityResponse)
def check_availability(
    service_id: int = Query(..., gt=0),
    booking_date: str = Query(..., alias="date", description="YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_db)
):
    """
    Returns available 15-minute start times for the single chair.
    Excludes past times, weekly off day, daily break, blocked times, and existing bookings.
    """
    try:
        parsed_date = date.fromisoformat(booking_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    try:
        slots_data = get_available_slots(conn, service_id, parsed_date)
        return slots_data
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/book", response_model=BookingResponse)
@limiter.limit("5/minute")
def book_appointment(
    request: Request,
    payload: BookingCreateRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    """
    Creates an appointment atomically using BEGIN IMMEDIATE.
    Returns 409 Conflict if the slot was already taken.
    Enforces maximum 2 upcoming bookings per mobile number.
    Rate limited to 5 requests per minute per IP.
    """
    try:
        booking = create_booking(
            conn=conn,
            service_id=payload.service_id,
            start_at_iso=payload.start_at,
            customer_name=payload.name,
            phone=payload.phone
        )
        return BookingResponse(
            booking_id=booking["booking_id"],
            service_name=booking["service_name"],
            price_inr=booking["price_inr"],
            duration_minutes=booking["duration_minutes"],
            customer_name=booking["customer_name"],
            phone=booking["phone"],
            start_at=booking["start_at"],
            end_at=booking["end_at"],
            status=booking["status"],
            cancel_token=booking["cancel_token"],
            cancel_url=f"/cancel/{booking['cancel_token']}"
        )
    except BookingConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/cancel/{token}")
@limiter.limit("10/minute")
def cancel_api_endpoint(
    request: Request,
    token: str,
    conn: sqlite3.Connection = Depends(get_db)
):
    """API endpoint to cancel a booking via raw secret token."""
    try:
        result = cancel_booking(conn, token)
        return result
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

# ==================== Admin & Owner Panel Routes ====================

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse(request=request, name="admin_login.html", context={"error": None, "shop_name": SHOP_NAME})

@app.post("/admin/login", response_class=HTMLResponse)
def admin_login_submit(request: Request, password: str = Form(...)):
    if password.strip() == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=get_admin_token(),
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30  # 30 days
        )
        return response
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": "Galat password! Please dobara try karein.", "shop_name": SHOP_NAME},
        status_code=400
    )

@app.get("/admin/logout")
@app.post("/admin/logout")
def admin_logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    booking_date: Optional[str] = None,
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    now_ist = get_now_ist()
    target_date_str = booking_date if booking_date else now_ist.date().isoformat()

    bookings = conn.execute(
        """
        SELECT b.id, b.customer_name, b.phone, b.start_at, b.end_at, b.status, b.created_at,
               s.name as service_name, s.price_inr, s.duration_minutes
        FROM bookings b
        JOIN services s ON b.service_id = s.id
        WHERE date(b.start_at) = ?
        ORDER BY b.start_at ASC
        """,
        (target_date_str,)
    ).fetchall()

    stats = {
        "total": len(bookings),
        "confirmed": sum(1 for b in bookings if b["status"] == "confirmed"),
        "completed": sum(1 for b in bookings if b["status"] == "completed"),
        "no_show": sum(1 for b in bookings if b["status"] == "no_show"),
        "cancelled": sum(1 for b in bookings if b["status"] == "cancelled"),
        "revenue": sum(b["price_inr"] for b in bookings if b["status"] == "completed")
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "shop_name": SHOP_NAME,
            "target_date": target_date_str,
            "bookings": [dict(b) for b in bookings],
            "stats": stats
        }
    )

@app.post("/admin/bookings/{booking_id}/status")
def update_booking_status(
    request: Request,
    booking_id: int,
    status_val: str = Form(..., alias="status"),
    redirect_date: Optional[str] = Form(None),
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    if status_val not in ("confirmed", "completed", "no_show", "cancelled"):
        raise HTTPException(status_code=400, detail="Invalid status")

    conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status_val, booking_id))
    param = f"?booking_date={redirect_date}" if redirect_date else ""
    return RedirectResponse(url=f"/admin{param}", status_code=303)

@app.get("/admin/blocks", response_class=HTMLResponse)
def admin_blocks_view(
    request: Request,
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    now_ist = get_now_ist()
    blocks = conn.execute(
        "SELECT id, start_at, end_at, reason FROM blocked_times WHERE end_at >= ? ORDER BY start_at ASC",
        (now_ist.isoformat(),)
    ).fetchall()
    return templates.TemplateResponse(
        request=request,
        name="admin_blocks.html",
        context={
            "shop_name": SHOP_NAME,
            "blocks": [dict(b) for b in blocks],
            "today_date": now_ist.date().isoformat()
        }
    )

@app.post("/admin/blocks/add")
def add_blocked_time(
    request: Request,
    block_date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    reason: str = Form(...),
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    try:
        b_date = date.fromisoformat(block_date)
        from config import parse_time
        s_t = parse_time(start_time)
        e_t = parse_time(end_time)
        from datetime import datetime
        start_dt = datetime.combine(b_date, s_t, tzinfo=LOCAL_TZ)
        end_dt = datetime.combine(b_date, e_t, tzinfo=LOCAL_TZ)
        if start_dt >= end_dt:
            raise ValueError("Start time must be before end time.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid block parameters: {e}")

    conn.execute(
        "INSERT INTO blocked_times (start_at, end_at, reason) VALUES (?, ?, ?)",
        (start_dt.isoformat(), end_dt.isoformat(), reason.strip() or "Emergency / Break")
    )
    return RedirectResponse(url="/admin/blocks", status_code=303)

@app.post("/admin/blocks/{block_id}/delete")
def delete_blocked_time(
    request: Request,
    block_id: int,
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    conn.execute("DELETE FROM blocked_times WHERE id = ?", (block_id,))
    return RedirectResponse(url="/admin/blocks", status_code=303)

@app.get("/admin/services", response_class=HTMLResponse)
def admin_services_view(
    request: Request,
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    services = conn.execute(
        "SELECT id, name, duration_minutes, price_inr, is_active FROM services ORDER BY id ASC"
    ).fetchall()
    return templates.TemplateResponse(
        request=request,
        name="admin_services.html",
        context={"shop_name": SHOP_NAME, "services": [dict(s) for s in services]}
    )

@app.post("/admin/services/add")
def admin_add_service(
    request: Request,
    name: str = Form(...),
    duration_minutes: int = Form(...),
    price_inr: int = Form(...),
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    trimmed = name.strip()
    if not trimmed or duration_minutes <= 0 or price_inr < 0:
        raise HTTPException(status_code=400, detail="Invalid service details.")

    conn.execute(
        "INSERT INTO services (name, duration_minutes, price_inr, is_active) VALUES (?, ?, ?, 1)",
        (trimmed, duration_minutes, price_inr)
    )
    return RedirectResponse(url="/admin/services", status_code=303)

@app.post("/admin/services/{service_id}/toggle")
def admin_toggle_service(
    request: Request,
    service_id: int,
    auth: bool = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db)
):
    row = conn.execute("SELECT is_active FROM services WHERE id = ?", (service_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found.")
    new_status = 0 if row["is_active"] else 1
    conn.execute("UPDATE services SET is_active = ? WHERE id = ?", (new_status, service_id))
    return RedirectResponse(url="/admin/services", status_code=303)

if __name__ == "__main__":
    import os
    import uvicorn
    from config import HOST, PORT

    # Read dynamic PORT provided by Render/Cloud, defaulting to PORT from config
    port = int(os.environ.get("PORT", PORT))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"Starting server on http://{host}:{port} ...")
    uvicorn.run("main:app", host=host, port=port)
