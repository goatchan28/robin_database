"""Trusted, browser-facing API for the Sentinel demonstration database."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


IDENTITY_BUCKET = "identity-images"
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
OFFENSE_LEVELS = {"INFRACTION", "MISDEMEANOR", "FELONY"}
DISPOSITIONS = {"PENDING", "CONVICTED", "DISMISSED", "ACQUITTED", "DIVERSION", "CLOSED"}
CASE_STATUSES = {"OPEN", "CLOSED", "UNDER REVIEW", "INACTIVE"}
logger = logging.getLogger("sentinel.api")


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    service_role_key: str
    cors_origins: tuple[str, ...]
    signed_url_ttl: int


@lru_cache
def settings() -> Settings:
    required = {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing server configuration: {', '.join(missing)}")
    origins = tuple(origin.strip() for origin in os.getenv(
        "API_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",") if origin.strip())
    return Settings(
        supabase_url=required["SUPABASE_URL"].rstrip("/"),
        service_role_key=required["SUPABASE_SERVICE_ROLE_KEY"],
        cors_origins=origins,
        signed_url_ttl=max(60, min(int(os.getenv("SIGNED_URL_TTL_SECONDS", "300")), 900)),
    )


class CaseCreate(BaseModel):
    incident_date: str
    offense: str = Field(min_length=1, max_length=512)
    offense_level: Literal["INFRACTION", "MISDEMEANOR", "FELONY"]
    disposition: Literal["PENDING", "CONVICTED", "DISMISSED", "ACQUITTED", "DIVERSION", "CLOSED"]
    case_status: Literal["OPEN", "CLOSED", "UNDER REVIEW", "INACTIVE"]
    case_number: str = Field(min_length=1, max_length=128)
    jurisdiction: str = Field(min_length=1, max_length=256)
    sentence: str | None = Field(default=None, max_length=1024)


class CasePatch(BaseModel):
    incident_date: str | None = None
    offense: str | None = Field(default=None, min_length=1, max_length=512)
    offense_level: Literal["INFRACTION", "MISDEMEANOR", "FELONY"] | None = None
    disposition: Literal["PENDING", "CONVICTED", "DISMISSED", "ACQUITTED", "DIVERSION", "CLOSED"] | None = None
    case_status: Literal["OPEN", "CLOSED", "UNDER REVIEW", "INACTIVE"] | None = None
    case_number: str | None = Field(default=None, min_length=1, max_length=128)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=256)
    sentence: str | None = Field(default=None, max_length=1024)


app = FastAPI(title="Sentinel Trusted API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings().cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


def supabase_headers(*, prefer: str | None = None) -> dict[str, str]:
    key = settings().service_role_key
    # Supabase's current `sb_secret_...` keys are API keys, not JWTs: they
    # must be sent only in `apikey`. The Authorization header is retained for
    # the legacy JWT-based service_role key for backwards compatibility.
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    return headers


def rest_request(
    method: str,
    table: str,
    *,
    params: dict[str, Any] | None = None,
    payload: Any = None,
    prefer: str | None = None,
) -> httpx.Response:
    try:
        response = httpx.request(
            method,
            f"{settings().supabase_url}/rest/v1/{table}",
            headers=supabase_headers(prefer=prefer),
            params=params,
            json=payload,
            timeout=20,
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Unable to reach Supabase") from error
    if response.is_error:
        try:
            message = response.json().get("message") or response.json().get("hint") or "Unknown Supabase error"
        except (ValueError, AttributeError):
            message = "Unknown Supabase error"
        logger.warning("Supabase REST rejected %s %s (%s): %s", method, table, response.status_code, message)
        raise HTTPException(status_code=502, detail=f"Supabase rejected the server request: {message}")
    return response


def first_item(items: list[dict[str, Any]], detail: str) -> dict[str, Any]:
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return items[0]


def rest_rows(table: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
    return rest_request("GET", table, params=params).json()


def signed_url(bucket: str, path: str) -> str:
    try:
        response = httpx.post(
            f"{settings().supabase_url}/storage/v1/object/sign/{bucket}/{path}",
            headers=supabase_headers(), json={"expiresIn": settings().signed_url_ttl}, timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Unable to create an image link") from error
    relative_url = response.json()["signedURL"]
    return relative_url if relative_url.startswith("http") else f"{settings().supabase_url}/storage/v1{relative_url}"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/identities")
def list_identities(
    status_filter: Annotated[str, Query(alias="status")] = "active",
    search: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    if status_filter not in {"active", "archived"}:
        raise HTTPException(status_code=400, detail="Unsupported status filter")
    params: dict[str, Any] = {
        "select": "id,display_name,status,created_at,updated_at",
        "status": f"eq.{status_filter}", "order": "updated_at.desc",
        "limit": page_size, "offset": (page - 1) * page_size,
    }
    if search.strip():
        params["display_name"] = f"ilike.*{search.strip()}*"
    items = rest_rows("identities", params=params)
    identity_ids = [str(item["id"]) for item in items]
    if not identity_ids:
        return {"items": [], "page": page, "page_size": page_size}
    record_rows = rest_rows("criminal_records", params={
        "select": "id,identity_id,record_status,wanted_level,active_warrant,warrant_number,warrant_issue_date,arrest_count,conviction_count,primary_offense,last_arrest_date,created_at",
        "identity_id": f"in.({','.join(identity_ids)})", "order": "created_at.desc",
    })
    records_by_identity: dict[str, dict[str, Any]] = {}
    for record in record_rows:
        records_by_identity.setdefault(str(record["identity_id"]), record)
    record_ids = [str(record["id"]) for record in records_by_identity.values()]
    case_rows = rest_rows("crime_log", params={
        "select": "id,criminal_record_id,incident_date,offense,offense_level,disposition,case_status,case_number,jurisdiction,sentence",
        "criminal_record_id": f"in.({','.join(record_ids)})", "order": "incident_date.desc,id.desc",
    }) if record_ids else []
    cases_by_record: dict[str, list[dict[str, Any]]] = {}
    for case in case_rows:
        cases_by_record.setdefault(str(case["criminal_record_id"]), []).append(case)
    image_links = rest_rows("identity_image_links", params={
        "select": "identity_id,status", "identity_id": f"in.({','.join(identity_ids)})",
    })
    image_counts: dict[str, int] = {}
    for link in image_links:
        if (link.get("status") or "active").lower() == "active":
            identity_key = str(link["identity_id"])
            image_counts[identity_key] = image_counts.get(identity_key, 0) + 1
    for item in items:
        record = records_by_identity.get(str(item["id"]), {})
        item["criminal_record_id"] = record.get("id")
        item["record_status"] = record.get("record_status")
        item["wanted_level"] = record.get("wanted_level")
        item["primary_offense"] = record.get("primary_offense")
        item["last_arrest_date"] = record.get("last_arrest_date")
        item["image_count"] = image_counts.get(str(item["id"]), 0)
        item["criminal_record"] = {**record, "cases": cases_by_record.get(str(record["id"]), [])} if record else None
    # The current shared demo data contains duplicate display names from two
    # imports. When one duplicate owns a criminal record and another does not,
    # show the record-owning identity in this local UI. Keep all source rows in
    # Supabase unchanged, including identities with no record at all.
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(item["display_name"].strip().casefold(), []).append(item)
    items = [
        next((item for item in group if item["criminal_record_id"] is not None), group[0])
        for group in groups.values()
    ]
    return {"items": items, "page": page, "page_size": page_size}


@app.get("/identities/{identity_id}")
def get_identity(identity_id: uuid.UUID) -> dict[str, Any]:
    item = first_item(rest_rows("identities", params={"select": "id,display_name,status,created_at,updated_at", "id": f"eq.{identity_id}"}), "Identity not found")
    record_rows = rest_rows("criminal_records", params={"identity_id": f"eq.{identity_id}", "order": "created_at.desc", "limit": 1})
    record = record_rows[0] if record_rows else {}
    item.update({f"criminal_record_{key}" if key == "id" else key: value for key, value in record.items()})
    return item


@app.get("/identities/{identity_id}/images")
def get_identity_images(identity_id: uuid.UUID) -> dict[str, Any]:
    first_item(rest_rows("identities", params={"select": "id", "id": f"eq.{identity_id}"}), "Identity not found")
    links = rest_rows("identity_image_links", params={
        "select": "status,identity_images(id,storage_bucket,storage_path,content_type,created_at,width,height,face_rect)",
        "identity_id": f"eq.{identity_id}", "order": "created_at.desc",
    })
    images = [link["identity_images"] for link in links if (link.get("status") or "active").lower() == "active" and link.get("identity_images")]
    return {"items": [
        {"id": row["id"], "content_type": row["content_type"], "created_at": row["created_at"],
         "width": row["width"], "height": row["height"], "face_rect": row["face_rect"],
         "signed_url": signed_url(row["storage_bucket"], row["storage_path"])}
        for row in images
    ]}


@app.get("/identities/{identity_id}/criminal-record")
def get_criminal_record(identity_id: uuid.UUID) -> dict[str, Any]:
    record = first_item(rest_rows("criminal_records", params={"identity_id": f"eq.{identity_id}", "order": "created_at.desc", "limit": 1}), "Criminal record not found")
    record["cases"] = rest_rows("crime_log", params={"criminal_record_id": f"eq.{record['id']}", "order": "incident_date.desc,id.desc"})
    return record


@app.get("/criminal-records/{record_id}/cases")
def get_cases(record_id: int) -> dict[str, Any]:
    first_item(rest_rows("criminal_records", params={"select": "id", "id": f"eq.{record_id}"}), "Criminal record not found")
    return {"items": rest_rows("crime_log", params={"criminal_record_id": f"eq.{record_id}", "order": "incident_date.desc,id.desc"})}


@app.post("/criminal-records/{record_id}/cases", status_code=status.HTTP_201_CREATED)
def create_case(record_id: int, payload: CaseCreate) -> dict[str, Any]:
    first_item(rest_rows("criminal_records", params={"select": "id", "id": f"eq.{record_id}"}), "Criminal record not found")
    rows = rest_request("POST", "crime_log", payload={"criminal_record_id": record_id, **payload.model_dump()}, prefer="return=representation").json()
    return first_item(rows, "Unable to create case")


@app.patch("/crime-log/{case_id}")
def update_case(case_id: int, payload: CasePatch) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="At least one field is required")
    rows = rest_request("PATCH", "crime_log", params={"id": f"eq.{case_id}"}, payload=updates, prefer="return=representation").json()
    return first_item(rows, "Case not found")


def validate_image(upload: UploadFile, content: bytes) -> None:
    if upload.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported image MIME type")
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Each image must be between 1 byte and 10 MiB")
    valid_signature = (
        content.startswith(b"\xff\xd8\xff") or
        content.startswith(b"\x89PNG\r\n\x1a\n") or
        (content.startswith(b"RIFF") and content[8:12] == b"WEBP") or
        (len(content) > 12 and content[4:8] == b"ftyp")
    )
    if not valid_signature:
        raise HTTPException(status_code=415, detail="Image content does not match a supported format")


def storage_upload(path: str, content: bytes, content_type: str) -> None:
    try:
        response = httpx.post(
            f"{settings().supabase_url}/storage/v1/object/{IDENTITY_BUCKET}/{path}",
            headers={**supabase_headers(), "Content-Type": content_type, "x-upsert": "false"},
            content=content, timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Unable to upload image to Supabase") from error


def storage_delete(paths: list[str]) -> None:
    if not paths:
        return
    httpx.request(
        "DELETE", f"{settings().supabase_url}/storage/v1/object/{IDENTITY_BUCKET}",
        headers=supabase_headers(),
        json={"prefixes": paths}, timeout=20,
    )


def clean_name_part(value: str, label: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,79}", cleaned):
        raise HTTPException(status_code=422, detail=f"{label} must contain letters, spaces, apostrophes, or hyphens only")
    return cleaned


def split_full_name(value: str) -> tuple[str, str, str]:
    cleaned = " ".join(value.strip().split())
    if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{1,159}", cleaned):
        raise HTTPException(status_code=422, detail="Full name must contain letters, spaces, apostrophes, or hyphens only")
    parts = cleaned.split(" ")
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Enter a first and last name")
    normalized = " ".join(part.title() for part in parts)
    return normalized.split(" ", 1)[0], normalized.split(" ", 1)[1], normalized


def image_extension(content_type: str) -> str:
    extensions = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/heic": "heic", "image/heif": "heif",
    }
    return extensions[content_type]


def image_stem(display_name: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]", "", part) for part in display_name.split())


def image_folder(display_name: str) -> str:
    """Return the Storage folder convention for an enrolled identity.

    ``external_ref`` remains a legacy identity reference.  Storage paths are
    intentionally derived from the visible name so ``Manas Chan`` is always
    enrolled beneath ``manas_chan``.
    """
    parts = [re.sub(r"[^a-z0-9]", "", part.lower()) for part in display_name.split()]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Identity display name must contain a first and last name")
    return "_".join(parts)


def next_image_paths(folder: str, display_name: str, uploads: list[UploadFile]) -> list[str]:
    stem = image_stem(display_name)
    existing_rows = rest_rows("identity_images", params={
        "select": "storage_path", "storage_path": f"like.{folder}/{stem}%",
    })
    used = {row["storage_path"].removeprefix(f"{folder}/") for row in existing_rows}
    paths: list[str] = []
    for upload in uploads:
        extension = image_extension(upload.content_type or "")
        number = 1
        while True:
            suffix = "" if number == 1 else f"_{number:02d}"
            filename = f"{stem}{suffix}.{extension}"
            if filename not in used:
                used.add(filename)
                paths.append(f"{folder}/{filename}")
                break
            number += 1
    return paths


async def write_image_set(identity: dict[str, Any], uploads: list[UploadFile], link_status: str = "active") -> list[dict[str, Any]]:
    folder = image_folder(identity["display_name"])
    uploaded_paths: list[str] = []
    prepared: list[tuple[str, UploadFile, bytes, str]] = []
    try:
        paths = next_image_paths(folder, identity["display_name"], uploads)
        for upload, path in zip(uploads, paths, strict=True):
            content = await upload.read()
            validate_image(upload, content)
            storage_upload(path, content, upload.content_type)
            uploaded_paths.append(path)
            prepared.append((path, upload, content, hashlib.sha256(content).hexdigest()))
        image_rows = []
        for path, upload, _, digest in prepared:
            image_row = first_item(rest_request(
                "POST", "identity_images",
                payload={"storage_bucket": IDENTITY_BUCKET, "storage_path": path, "content_type": upload.content_type, "sha256": digest},
                prefer="return=representation",
            ).json(), "Unable to create image metadata")
            rest_request(
                "POST", "identity_image_links",
                payload={"identity_id": str(identity["id"]), "image_id": image_row["id"], "status": link_status.lower()},
                prefer="return=minimal",
            )
            image_rows.append(image_row)
        return image_rows
    except Exception:
        storage_delete(uploaded_paths)
        raise


@app.post("/identities/{identity_id}/image-sets", status_code=status.HTTP_201_CREATED)
async def create_image_set(
    identity_id: uuid.UUID,
    uploads: Annotated[list[UploadFile], File(alias="files")],
    link_status: Annotated[str, Form()] = "active",
) -> dict[str, Any]:
    if not uploads:
        raise HTTPException(status_code=400, detail="At least one image is required")
    identity = first_item(rest_rows("identities", params={"select": "id,display_name,external_ref", "id": f"eq.{identity_id}"}), "Identity not found")
    image_rows = await write_image_set(identity, uploads, link_status)
    return {"items": image_rows, "count": len(image_rows)}


@app.post("/identity-intake", status_code=status.HTTP_201_CREATED)
async def intake_identity_images(
    full_name: Annotated[str, Form()],
    uploads: Annotated[list[UploadFile], File(alias="files")],
) -> dict[str, Any]:
    if not uploads:
        raise HTTPException(status_code=400, detail="At least one image is required")
    _, _, display_name = split_full_name(full_name)
    external_ref = re.sub(r"[^a-z0-9]", "", display_name.lower())
    matches = rest_rows("identities", params={
        "select": "id,display_name,external_ref,status", "status": "eq.active", "display_name": f"ilike.{display_name}",
    })
    identity = next((item for item in matches if item["display_name"].casefold() == display_name.casefold()), None)
    created = False
    if identity is None:
        ref_matches = rest_rows("identities", params={"select": "id,display_name", "external_ref": f"eq.{external_ref}"})
        if ref_matches:
            raise HTTPException(status_code=409, detail="An identity with this normalized name already exists; use its exact name")
        identity = first_item(rest_request(
            "POST", "identities", payload={"display_name": display_name, "external_ref": external_ref, "status": "active"},
            prefer="return=representation",
        ).json(), "Unable to create identity")
        created = True
    try:
        image_rows = await write_image_set(identity, uploads)
    except Exception:
        if created:
            rest_request("PATCH", "identities", params={"id": f"eq.{identity['id']}"}, payload={"status": "archived"}, prefer="return=minimal")
        raise
    return {"identity": identity, "created": created, "items": image_rows, "count": len(image_rows)}


# Normal local use is a single process: after `npm run build`, FastAPI serves
# the Vite bundle as well as the local API from http://127.0.0.1:8082.
# API routes are registered above this catch-all static mount.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "dist"
# Vercel serves the Vite build from its CDN. Keeping this mount local-only
# avoids a catch-all static route taking over the serverless API function.
if FRONTEND_DIST.is_dir() and not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
