"""Run the route optimizer on its own for testing (no main app needed).

    python demo/run_demo_server.py
    -> API docs:  http://localhost:8001/docs
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Minimal .env loader (so we don't need python-dotenv).
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from farmnex_routes import init_db, router  # noqa: E402

app = FastAPI(title="FarmNex Route Optimizer (standalone demo)")
app.include_router(router, prefix="/routes")


@app.get("/")
def home():
    return {"docs": "/docs", "api_prefix": "/routes"}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8001)))
