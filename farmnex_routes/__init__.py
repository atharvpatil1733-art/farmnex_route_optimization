"""FarmNex route optimization component.

    from farmnex_routes import router
    app.include_router(router, prefix="/routes")
"""
from .db import get_session, init_db
from .router import router

__all__ = ["router", "init_db", "get_session"]
__version__ = "0.1.0"
