"""Vercel entry point for the Robin FastAPI service.

Vercel looks specifically for a ``FastAPI`` instance named ``app`` and passes
the full ``/api/...`` path to it.  The shared local app intentionally exposes
routes such as ``/identities``; this lightweight middleware removes only that
deployment prefix before FastAPI resolves the route.
"""

from __future__ import annotations

from app.main import app as robin_app
from fastapi import Request


@robin_app.middleware("http")
async def strip_vercel_api_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    if path == "/api" or path.startswith("/api/"):
        stripped_path = path[4:] or "/"
        request.scope["path"] = stripped_path
        if request.scope.get("raw_path"):
            request.scope["raw_path"] = stripped_path.encode()
    return await call_next(request)


# Vercel requires this to be the FastAPI instance, not a generic ASGI wrapper.
app = robin_app
