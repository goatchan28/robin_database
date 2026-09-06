"""Normalize enrolled-image Storage folders from linked identity display names.

By default this is a read-only audit.  Pass ``--apply`` only after reviewing
the proposed moves.  The script intentionally leaves ``identities.external_ref``
unchanged: it can remain a legacy cross-project reference while the image
Storage convention is ``first_last`` in lowercase.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "api" / ".env")

BUCKET = "identity-images"

# Raw objects created outside the intake API have no identity_images metadata.
# These source folders were verified against the supplied participant roster.
LEGACY_UNTRACKED_FOLDERS = {
    "aaron_chua": "aaron_chua",
    "aadityakadam": "aaditya_kadam",
    "albert_chen": "albert_chen",
    "anantgupta": "anant_gupta",
    "andrewfalcon": "andrew_falcon",
    "aryamantiwary": "aryaman_tiwary",
    "aryav_odhrani": "aryav_odhrani",
    "asherwang": "asher_wang",
    "brendonbazzani": "brendon_bazzani",
    "cormac_kennedy": "cormac_kennedy",
    "davidjohnson": "david_johnson",
    "diegovictoria": "diego_victoria",
    "donte_truong": "donte_truong",
    "eric_li": "eric_li",
    "fabian_ashton": "fabian_ashton",
    "hari_gridharan": "hari_gridharan",
    "jeffberkowitz": "jeff_berkowitz",
    "joshuachung": "joshua_chung",
    "maxfan": "max_fan",
    "mira_krishna": "mira_krishnaiah",
    "nicholasirving": "nicholas_irving",
    "nikhil_krishna": "nikhil_krishna",
    "parth_jaiswal": "parth_jaiswal",
    "patrickzhao": "patrick_zhao",
    "perryzhang": "perry_zhang",
    "rachellee": "rachel_lee",
    "ronith_lahoti": "ronith_lahoti",
    "shaurya_jeevagan": "shaurya_jeevagan",
    "shawnchen": "shawn_chen",
    "theodoreoltean": "theodore_oltean",
    "tyler_sacharow": "tyler_sacharow",
    "vinhhuynh": "vinh_huynh",
    "winfredlin": "winfred_lin",
    "yukiqian": "yuki_qian",
    "yash_sreepathi": "yash_sreepathi",
}


def config() -> tuple[str, dict[str, str]]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in api/.env")
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return url, headers


def folder_for(display_name: str) -> str | None:
    # Keep word boundaries as underscores, discard punctuation such as apostrophes,
    # and accept compound surnames: Cole Van Hersett -> cole_van_hersett.
    parts = [re.sub(r"[^a-z0-9]", "", part.lower()) for part in display_name.split()]
    parts = [part for part in parts if part]
    return "_".join(parts) if len(parts) >= 2 else None


def request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    response = client.request(method, url, timeout=30, **kwargs)
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{method} {url}: {response.status_code} {detail}")
    return response.json() if response.content else None


def storage_paths(client: httpx.Client, base_url: str, headers: dict[str, str], prefix: str = "") -> set[str]:
    """Return every object key under a prefix, recursively."""
    rows = request(
        client,
        "POST",
        f"{base_url}/storage/v1/object/list/{BUCKET}",
        headers=headers,
        json={"prefix": prefix, "limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"}},
    )
    paths: set[str] = set()
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        path = f"{prefix}/{name}" if prefix else name
        # Supabase returns directory placeholders without object metadata.
        if row.get("metadata") is None and row.get("id") is None:
            paths.update(storage_paths(client, base_url, headers, path))
        else:
            paths.add(path)
    return paths


def audit(client: httpx.Client, base_url: str, headers: dict[str, str]) -> tuple[list[dict[str, str]], list[str], list[str]]:
    links = request(
        client,
        "GET",
        f"{base_url}/rest/v1/identity_image_links",
        headers=headers,
        params={
            "select": "identity_id,image_id,status,identities(id,display_name),identity_images(id,storage_bucket,storage_path)",
            "order": "created_at.asc",
        },
    )

    planned: list[dict[str, str]] = []
    problems: list[str] = []
    notes: list[str] = []
    image_owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        image = link.get("identity_images")
        identity = link.get("identities")
        if not image:
            problems.append(f"link {link.get('identity_id')} → {link.get('image_id')} has no image metadata")
            continue
        image_owners[str(image["id"])].append(link)
        if not identity:
            problems.append(f"image {image['id']} ({image.get('storage_path')}) has no linked identity")

    for image_id, owners in image_owners.items():
        paths = {owner["identity_images"].get("storage_path") for owner in owners}
        names = {owner.get("identities", {}).get("display_name") for owner in owners if owner.get("identities")}
        if len(owners) != 1 or len(paths) != 1 or len(names) != 1:
            problems.append(f"image {image_id} has {len(owners)} identity links; not moving automatically")
            continue
        owner = owners[0]
        image = owner["identity_images"]
        source = image.get("storage_path") or ""
        bucket = image.get("storage_bucket") or ""
        display_name = owner["identities"]["display_name"]
        folder = folder_for(display_name)
        if bucket != BUCKET:
            problems.append(f"image {image_id} uses bucket {bucket!r}, expected {BUCKET!r}")
            continue
        if not folder:
            problems.append(f"image {image_id} has non-normalizable display name {display_name!r}")
            continue
        filename = source.rsplit("/", 1)[-1]
        if not filename:
            problems.append(f"image {image_id} has empty Storage path")
            continue
        destination = f"{folder}/{filename}"
        if source != destination:
            planned.append({"image_id": image_id, "name": display_name, "source": source, "destination": destination, "tracked": "true"})

    all_metadata = request(
        client,
        "GET",
        f"{base_url}/rest/v1/identity_images",
        headers=headers,
        params={"select": "id,storage_bucket,storage_path"},
    )
    metadata_paths = {
        row["storage_path"]
        for row in all_metadata
        if row.get("storage_bucket") == BUCKET and row.get("storage_path")
    }
    objects = storage_paths(client, base_url, headers)
    # Do not move a metadata row unless its corresponding object is present.
    present_planned: list[dict[str, str]] = []
    for item in planned:
        if item["source"] in objects:
            present_planned.append(item)
        else:
            problems.append(f"identity_images metadata points to missing Storage object {item['source']!r}")
    planned = present_planned

    identities = request(
        client,
        "GET",
        f"{base_url}/rest/v1/identities",
        headers=headers,
        params={"select": "id,display_name,external_ref"},
    )
    aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for identity in identities:
        canonical = folder_for(identity.get("display_name") or "")
        if canonical:
            aliases[canonical].append(identity)
            aliases[canonical.replace("_", "")].append(identity)
        if identity.get("external_ref"):
            aliases[str(identity["external_ref"]).lower()].append(identity)

    for path in sorted(objects - metadata_paths):
        source_folder = path.split("/", 1)[0].lower()
        candidates = {str(item["id"]): item for item in aliases.get(source_folder, [])}
        if len(candidates) == 1:
            identity = next(iter(candidates.values()))
            destination_folder = folder_for(identity["display_name"])
            display_name = identity["display_name"]
        elif source_folder in LEGACY_UNTRACKED_FOLDERS:
            destination_folder = LEGACY_UNTRACKED_FOLDERS[source_folder]
            display_name = destination_folder.replace("_", " ").title()
        else:
            problems.append(f"Storage object {path!r} has no uniquely matched identity metadata")
            continue
        destination = f"{destination_folder}/{path.rsplit('/', 1)[-1]}"
        if path != destination:
            planned.append({
                "image_id": "", "name": display_name, "source": path,
                "destination": destination, "tracked": "false",
            })
            notes.append(f"Untracked object {path!r} matched {display_name!r}")
    for path in sorted(metadata_paths - objects):
        # Paths already reported from linked metadata above need not be repeated.
        if not any(path in problem for problem in problems):
            problems.append(f"identity_images metadata points to missing Storage object {path!r}")
    return planned, problems, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="move objects and update metadata only when the audit is fully clean")
    parser.add_argument("--apply-safe", action="store_true", help="move only verified objects; leave unresolved paths untouched")
    args = parser.parse_args()
    base_url, headers = config()

    with httpx.Client() as client:
        planned, problems, notes = audit(client, base_url, headers)
        destinations: dict[str, list[dict[str, str]]] = defaultdict(list)
        for item in planned:
            destinations[item["destination"]].append(item)
            print(f"{item['name']}: {item['source']} -> {item['destination']}")
        for destination, items in destinations.items():
            if len(items) > 1:
                problems.append(f"destination collision at {destination}: {', '.join(item['image_id'] for item in items)}")

        print(f"\n{len(planned)} object move(s) proposed.")
        if problems:
            print(f"{len(problems)} item(s) require review:")
            for problem in problems:
                print(f"  - {problem}")
        if notes:
            print(f"{len(notes)} untracked object(s) have a unique identity match:")
            for note in notes:
                print(f"  - {note}")
        if not args.apply and not args.apply_safe:
            print("Dry run only. Re-run with --apply after a clean audit, or --apply-safe to move only verified objects.")
            return 1 if problems else 0
        if args.apply and problems:
            print("No changes made because the audit found items requiring review.", file=sys.stderr)
            return 2

        for item in planned:
            request(
                client,
                "POST",
                f"{base_url}/storage/v1/object/move",
                headers=headers,
                json={"bucketId": BUCKET, "sourceKey": item["source"], "destinationKey": item["destination"]},
            )
            if item["tracked"] == "true":
                try:
                    request(
                        client,
                        "PATCH",
                        f"{base_url}/rest/v1/identity_images",
                        headers={**headers, "Prefer": "return=minimal"},
                        params={"id": f"eq.{item['image_id']}"},
                        json={"storage_path": item["destination"]},
                    )
                except Exception:
                    # Keep the object and its metadata together if a database
                    # update fails after Storage accepted the move.
                    request(
                        client,
                        "POST",
                        f"{base_url}/storage/v1/object/move",
                        headers=headers,
                        json={"bucketId": BUCKET, "sourceKey": item["destination"], "destinationKey": item["source"]},
                    )
                    raise
            print(f"Moved {item['source']} -> {item['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
