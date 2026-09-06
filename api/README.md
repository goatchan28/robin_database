# Robin local API

After a one-time frontend build, this loopback-only service serves the UI and
the API together at `http://127.0.0.1:8082`. It connects to the shared
Supabase project with server-only credentials and exposes the local
image-database workflow.

This intentionally matches the local-only pattern used by the other projects:

```text
Browser → local FastAPI :8082 → shared Supabase database + private Storage
```

It does **not** use Supabase Auth and does not require any shared-project RLS
or Auth configuration changes. It must remain bound to `127.0.0.1`, never a
LAN address or public host.

## Setup

Build the frontend once (repeat only after changing frontend files):

```bash
cd ..
npm run build
```

Then run the complete local app from `api/`:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8082
```

Set only server-only values in `api/.env`:

```dotenv
SUPABASE_URL=https://gaeammzuxiguhayeqckc.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
```

`SUPABASE_DB_URL` is no longer used. The local API uses the project’s
server-only service key for database REST requests and private Storage. A
Supabase personal access token is not used by this app.

New image sets are stored in `identity-images/<external_ref>/`, where
`external_ref` is the identity's lowercase, no-space enrollment reference.

## Routes

```text
GET   /health
GET   /identities?status=active&search=&page=1&page_size=25
GET   /identities/{identity_id}
GET   /identities/{identity_id}/images
POST  /identities/{identity_id}/image-sets          multipart: files[]
GET   /identities/{identity_id}/criminal-record
GET   /criminal-records/{record_id}/cases
POST  /criminal-records/{record_id}/cases
PATCH /crime-log/{case_id}
```

The service issues short-lived signed URLs for the private `identity-images`
bucket and performs image object, metadata, and identity-link writes. It never
returns database credentials, service keys, or embeddings to the browser.
