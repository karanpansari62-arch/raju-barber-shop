# Raju Barber Shop, Bhusawal — Online Appointment Booking Engine

A focused, single-chair online booking engine for a single barber shop in Bhusawal, Maharashtra, India. Built with FastAPI, SQLite, Jinja2, and mobile-first vanilla JavaScript (zero heavy frameworks).

---

## Key Features

- **Strict Timezone Handling**: All scheduling logic, slot availability, and date calculations strictly run in `Asia/Kolkata` (`zoneinfo`).
- **Single-Chair Schema**:
  - `services(id, name, duration_minutes, price_inr, is_active)`
  - `bookings(id, service_id, customer_name, phone, start_at, end_at, status, cancel_token_hash, created_at)`
  - `blocked_times(id, start_at, end_at, reason)`
  - *No `shop_id`, no `barbers` table, no `slugs`.*
- **15-Minute Availability Engine**:
  - Generates 15-minute start times between `OPEN_TIME` (10:00) and `CLOSE_TIME` (21:00).
  - Blocks duration based on service (15, 20, 30, 45 mins).
  - Automatically excludes the daily afternoon break (`BREAK_START` 14:00 to `BREAK_END` 15:00).
  - Excludes the weekly off day (e.g. Tuesday).
  - Excludes owner ad-hoc emergency blocks (`blocked_times`).
  - Restricts bookings to a 14-day advance window (`MAX_DAYS_AHEAD=14`) and filters out past times.
- **Race Condition & Spam Protection**:
  - `POST /api/book` uses SQLite `BEGIN IMMEDIATE` to acquire an exclusive write lock. Overlap detection and insertion happen atomically, preventing double-bookings and returning HTTP 409 Conflict.
  - Limits each phone number to a maximum of 2 active upcoming bookings.
- **Privacy & Security**:
  - One-time cancellation token (`secrets.token_urlsafe(32)`). Only its SHA-256 hash is persisted in the database.
  - 1-Click WhatsApp buttons for saving the receipt and notifying the barber.
  - Multi-language support: Instant 1-click toggle between **मराठी (Marathi)**, **हिंदी (Hindi)**, and **English** with `localStorage` memory.
  - Rate limiting via `slowapi`: booking 5/min/IP, cancel 10/min/IP.
  - Parameterized SQL queries only.
  - Security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`).
  - Safe DOM rendering using `textContent` only (no `innerHTML`).
  - Hinglish/English mix customer UI.

---

## Configuration (`.env`)

Configure the shop parameters in `.env` (template in `.env.example`):
```ini
SHOP_NAME="Raju Barber Shop, Bhusawal"
OPEN_TIME="10:00"
CLOSE_TIME="21:00"
WEEKLY_OFF_DAY="Tuesday"
BREAK_START="14:00"
BREAK_END="15:00"
MAX_DAYS_AHEAD=14
TIMEZONE="Asia/Kolkata"
DATABASE_PATH="barbershop.db"
PORT=8000
```

---

## Installation & Running

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Seed Services
```bash
python seed.py
```
Seeds the 4 standard barber services:
- Haircut: 30 mins, ₹150
- Beard Grooming: 15 mins, ₹80
- Haircut + Beard Combo: 45 mins, ₹200
- Head Massage: 20 mins, ₹100

### 3. Start the Server
- **Option 1 (Command line)**:
  ```bash
  python main.py
  ```
- **Option 2 (Windows Double-Click)**:
  Double-click `run.bat` in the project folder.

Open your browser at:
- **Customer Booking Page**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/) (or `http://127.0.0.1:8080/` if port 8000 is occupied)
- **Barber / Admin Dashboard**: [http://127.0.0.1:8000/admin/login](http://127.0.0.1:8000/admin/login) (Password: `raju123`)
- **Interactive API Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## Running the Automated Test Suite

Run pytest:
```bash
python -m pytest -v
```

The test suite covers:
1. **Overlap Logic**: 10:00–10:45 booked $\to$ 10:30 rejected (409), 10:45 allowed.
2. **Daily Break Overlap**: Slots overlapping 14:00–15:00 are rejected and excluded from availability.
3. **Concurrency Test**: Two concurrent requests for the exact same slot $\to$ exactly one succeeds (200), the other gets 409.
4. **Out-of-Bounds Validation**: Past slots, slots exceeding closing hours (21:00), and weekly off days are rejected.
5. **Slot Cancellation**: Cancelling an appointment immediately frees the slot for another customer.
6. **Spam Protection**: Max 2 active upcoming bookings per phone number (3rd booking is rejected).
7. **Token Hashing**: Confirms database only stores SHA-256 hash.
8. **Security Headers & Rate Limiting**: Ensures headers and SlowAPI limits are enforced.

---

## Manual Testing Steps

1. **Book an Appointment**:
   - Open [http://127.0.0.1:8080/](http://127.0.0.1:8080/).
   - Select a service (e.g. *Haircut + Beard Combo - 45 mins*).
   - Pick a booking date. Notice that:
     - Selecting a Tuesday shows "Shop aaj closed hai (Weekly Off)".
     - Selecting a normal day loads 15-minute start slots, cleanly skipping the 14:00–15:00 lunch break.
   - Select slot `10:00`.
   - Enter your name and 10-digit mobile number (e.g. `9876543210`).
   - Click **Appointment Confirm Karein**.
   - Confirmation card appears with a one-time cancellation link.
2. **Test Double-Booking Prevention**:
   - In a second browser tab, select the same date.
   - Notice that slots `10:00`, `10:15`, and `10:30` are no longer available for booking.
3. **Cancel the Appointment**:
   - Click the cancellation link (`/cancel/<token>`).
   - Click **Appointment Cancel Karein**.
   - Status updates to `CANCELLED`.
   - Re-check the booking page: slot `10:00` is now free again!

4. **Barber (Raju) Admin Portal**:
   - Open [http://127.0.0.1:8080/admin/login](http://127.0.0.1:8080/admin/login).
   - Enter password: `raju123`.
   - View today's appointments, completed revenue, customer contact details.
   - Click **📞 Call** to dial customer, or **💬 WhatsApp** to chat directly.
   - Click **✓ Complete** once service is done, or **✗ No Show** if customer didn't arrive.
   - Go to **Emergency Blocks** to block off-times (e.g. 16:00 to 18:00 for doctor visit/light cut).
   - Go to **Services & Rates** to add new services or toggle existing ones.
