import os
from datetime import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

# Load environment configuration
load_dotenv()

# Shop Details
SHOP_NAME = os.getenv("SHOP_NAME", "Raju Barber Shop, Bhusawal")
SHOP_PHONE = os.getenv("SHOP_PHONE", "919876543210").strip().replace("+", "")
OPEN_TIME_STR = os.getenv("OPEN_TIME", "10:00")
CLOSE_TIME_STR = os.getenv("CLOSE_TIME", "21:00")
WEEKLY_OFF_DAY_STR = os.getenv("WEEKLY_OFF_DAY", "Tuesday").strip().capitalize()
BREAK_START_STR = os.getenv("BREAK_START", "14:00")
BREAK_END_STR = os.getenv("BREAK_END", "15:00")
MAX_DAYS_AHEAD = int(os.getenv("MAX_DAYS_AHEAD", "14"))

# Timezone: strict Asia/Kolkata
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Kolkata")
LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)

# SQLite database path
DATABASE_PATH = os.getenv("DATABASE_PATH", "barbershop.db")

# Server host and port
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Admin security
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "raju123")
SECRET_KEY = os.getenv("SECRET_KEY", "bhusawal_barber_secret_key_raju")

# 15-minute slot grid
SLOT_GRANULARITY_MINUTES = 15

# Weekday mapping (Python weekday: Monday=0, ..., Sunday=6)
WEEKDAY_MAP = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6
}
WEEKLY_OFF_DAY_INT = WEEKDAY_MAP.get(WEEKLY_OFF_DAY_STR, 1)

def parse_time(t_str: str) -> time:
    """Parses 'HH:MM' string into datetime.time object."""
    parts = t_str.strip().split(":")
    return time(hour=int(parts[0]), minute=int(parts[1]))

OPEN_TIME = parse_time(OPEN_TIME_STR)
CLOSE_TIME = parse_time(CLOSE_TIME_STR)
BREAK_START = parse_time(BREAK_START_STR)
BREAK_END = parse_time(BREAK_END_STR)

# SlowAPI rate limiter by IP
limiter = Limiter(key_func=get_remote_address)
