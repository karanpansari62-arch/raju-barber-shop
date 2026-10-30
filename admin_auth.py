import hmac
import hashlib
import time
from typing import Optional
from fastapi import Request, HTTPException, status
from config import ADMIN_PASSWORD, SECRET_KEY

# Admin session validity window (14 days)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14

def get_admin_token(timestamp: Optional[int] = None) -> str:
    """
    Computes a cryptographically signed, timestamped session token:
    Format: '<timestamp>.<hmac_signature>'
    """
    ts = int(time.time()) if timestamp is None else timestamp
    payload = f"admin:{ts}"
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        f"{payload}:{ADMIN_PASSWORD}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{ts}.{signature}"

def is_admin_authenticated(request: Request, max_age: int = SESSION_MAX_AGE_SECONDS) -> bool:
    """
    Verifies that the incoming admin_session cookie:
    1. Contains a valid timestamp and signature separated by '.'
    2. Has not expired (created within max_age seconds)
    3. Has not been forged or tampered with (HMAC matches)
    """
    cookie = request.cookies.get("admin_session")
    if not cookie or "." not in cookie:
        return False

    try:
        ts_str, signature = cookie.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False

    now = int(time.time())
    # Expiry verification: reject if older than max_age or skewed into the future (>60s)
    if (now - ts) > max_age or ts > (now + 60):
        return False

    # Signature verification
    expected_sig = hmac.new(
        SECRET_KEY.encode("utf-8"),
        f"admin:{ts_str}:{ADMIN_PASSWORD}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_sig)

def require_admin(request: Request) -> bool:
    """FastAPI route dependency ensuring admin authentication."""
    if not is_admin_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )
    return True
