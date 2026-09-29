"""FarmNex route optimization component.

In the main backend:

    from farmnex_routes import router
    app.include_router(router, prefix="/routes")

Order linking helpers (see docs/LINKING_ORDERS.md):

    from farmnex_routes import create_delivery_for_order, on_delivery_update
"""
from .db import get_session, init_db, session_scope
from .hooks import (
    cancel_delivery_for_order,
    create_delivery_for_order,
    find_load_for_order,
    on_delivery_update,
    upsert_vehicle,
)
from .router import router
from .services import delivery_for_order

__all__ = [
    "router",
    "init_db",
    "get_session",
    "session_scope",
    "upsert_vehicle",
    "create_delivery_for_order",
    "cancel_delivery_for_order",
    "find_load_for_order",
    "delivery_for_order",
    "on_delivery_update",
]
__version__ = "0.2.0"
