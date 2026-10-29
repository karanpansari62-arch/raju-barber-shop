import hmac
import hashlib
from fastapi import Request, HTTPException, status
from config import ADMIN_PASSWORD, SECRET_KEY

def get_admin_token() -> str:
    """Computes HMAC signature for admin authentication cookie."""
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        ADMIN_PASSWORD.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def is_admin_authenticated(request: Request) -> bool:
    """Checks if incoming request has valid admin cookie."""
    cookie = request.cookies.get("admin_session")
    if not cookie:
        return False
    expected = get_admin_token()
    return hmac.compare_digest(cookie, expected)

def require_admin(request: Request) -> bool:
    """FastAPI route dependency ensuring admin authentication."""
    if not is_admin_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )
    return True
