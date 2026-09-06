"""Vercel entry point for the Robin FastAPI service.

Vercel looks specifically for a ``FastAPI`` instance named ``app`` and passes
the full ``/api/...`` path to it.  The shared local app intentionally exposes
routes such as ``/identities``; this lightweight middleware removes only that
deployment prefix before FastAPI resolves the route.
"""

from __future__ import annotations

from urllib.parse import parse_qs

# Vercel imports this module from the repository root, while local Uvicorn
# imports it from within ``api``.  The package-qualified path works in both.
from api.app.main import app as robin_app
from fastapi import Request


@robin_app.middleware("http")
async def strip_vercel_api_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    if path == "/api/index":
        forwarded_path = parse_qs(request.scope.get("query_string", b"").decode()).get(
            "vercel_path", [""]
        )[0]
        if forwarded_path:
            path = forwarded_path
    if path == "/api" or path.startswith("/api/"):
        stripped_path = path[4:] or "/"
        path = stripped_path
    request.scope["path"] = path
    if request.scope.get("raw_path"):
        request.scope["raw_path"] = path.encode()
    return await call_next(request)


# Keep the exported object as FastAPI so Vercel discovers this Python function.
app = robin_app
