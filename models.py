import re
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

# Indian 10-digit mobile pattern starting with 6, 7, 8, or 9
INDIAN_PHONE_REGEX = re.compile(r"^[6-9]\d{9}$")

class BookingCreateRequest(BaseModel):
    service_id: int = Field(..., gt=0)
    start_at: str = Field(..., description="ISO 8601 string in Asia/Kolkata timezone")
    name: str = Field(..., min_length=2, max_length=60)
    phone: str = Field(...)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if len(trimmed) < 2 or len(trimmed) > 60:
            raise ValueError("Customer name must be between 2 and 60 characters.")
        return trimmed

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-\(\)]", "", v)
        if cleaned.startswith("+91"):
            cleaned = cleaned[3:]
        elif cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]
        elif cleaned.startswith("0") and len(cleaned) == 11:
            cleaned = cleaned[1:]

        if not INDIAN_PHONE_REGEX.match(cleaned):
            raise ValueError("Please enter a valid 10-digit Indian mobile number starting with 6-9.")
        return cleaned

class SlotInfo(BaseModel):
    time: str          # "10:00"
    iso_start: str     # "2026-10-30T10:00:00+05:30"

class AvailabilityResponse(BaseModel):
    date: str
    service_id: int
    service_name: str
    duration_minutes: int
    price_inr: int
    slots: List[SlotInfo]
    is_off_day: bool = False
    message: Optional[str] = None

class BookingResponse(BaseModel):
    booking_id: int
    service_name: str
    price_inr: int
    duration_minutes: int
    customer_name: str
    phone: str
    start_at: str
    end_at: str
    status: str
    cancel_token: str
    cancel_url: str
