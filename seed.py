import sqlite3
from database import init_db, get_connection
from config import DATABASE_PATH

SERVICES_DATA = [
    ("Haircut", 30, 150),
    ("Beard Grooming", 15, 80),
    ("Haircut + Beard Combo", 45, 200),
    ("Head Massage", 20, 100),
]

def seed_services(db_path: str = None) -> None:
    """
    Seeds the 4 standard barber services into the SQLite database.
    Idempotent: clears existing services and re-inserts.
    """
    init_db(db_path)
    conn = get_connection(db_path)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM services")
        conn.executemany(
            "INSERT INTO services (name, duration_minutes, price_inr, is_active) VALUES (?, ?, ?, 1)",
            SERVICES_DATA
        )
        conn.execute("COMMIT")
        print("Successfully seeded services:")
        for name, duration, price in SERVICES_DATA:
            print(f" - {name} ({duration} mins, Rs{price})")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"Error seeding services: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    seed_services()
