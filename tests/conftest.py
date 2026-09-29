import os
import sys
from pathlib import Path

# Must be set before farmnex_routes is imported.
_db = Path(__file__).parent / "test_routes.db"
if _db.exists():
    _db.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["ROUTES_DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["ROUTING_PROVIDER"] = "haversine"  # tests must not depend on the internet

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
