import os
import tempfile
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
import sqlite3

from config import LOCAL_TZ, limiter, WEEKLY_OFF_DAY_INT
from database import init_db, get_connection, get_db
from main import app
from seed import seed_services

@pytest.fixture(scope="function")
def test_db():
    """Isolated temporary SQLite database for each test run."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    
    seed_services(db_path)
    yield db_path

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass

@pytest.fixture(scope="function")
def client(test_db):
    """TestClient pointing to the isolated test database."""
    def override_get_db():
        conn = get_connection(test_db)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.enabled = False
    with TestClient(app) as test_client:
        yield test_client
    limiter.enabled = True
    app.dependency_overrides.clear()

@pytest.fixture
def get_valid_future_date():
    """Returns a valid non-weekly-off future date within the 14-day window."""
    def _get_date(days_ahead=2):
        now = datetime.now(LOCAL_TZ)
        target = now.date() + timedelta(days=days_ahead)
        if target.weekday() == WEEKLY_OFF_DAY_INT:
            target += timedelta(days=1)
        return target
    return _get_date
