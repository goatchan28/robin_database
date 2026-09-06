"""Vercel entry point for the Robin FastAPI service.

Vercel sends this function requests beneath `/api`. The application itself is
shared with local Uvicorn development and intentionally declares routes such
as `/identities`, so this wrapper removes only that deployment prefix.
"""

from __future__ import annotations

from typing import Any

from app.main import app as robin_app


class StripApiPrefix:
    def __init__(self, application: Any) -> None:
        self.application = application

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in {"http", "websocket"} and scope.get("path", "").startswith("/api"):
            scope = dict(scope)
            path = scope["path"][4:] or "/"
            scope["path"] = path
            raw_path = scope.get("raw_path")
            if raw_path:
                scope["raw_path"] = path.encode()
        await self.application(scope, receive, send)


app = StripApiPrefix(robin_app)
