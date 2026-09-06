# Robin Intelligence Database

Local team interface for enrolling identity images into the shared Robin Supabase project and viewing the associated person records. It is intended for a private development/hackathon environment.

## Can a teammate use it?

Yes. Each teammate runs a local copy of the app and API, but all copies write to the same Supabase project. A normal setup takes about 3–5 minutes after Node.js and Python are already installed.

The local API is required because it holds the server-only Supabase secret and performs Storage uploads. Do **not** put that key in frontend files, browser code, screenshots, or commits.

## One-time setup

Requirements:

- Node.js 20+ (Node 22 is known to work)
- Python 3.11+ (Python 3.13 is known to work)
- Access to the team Supabase project’s server-side key

From the project root:

```bash
npm ci
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `api/.env` locally. This file is ignored by Git and must never be shared in source control:

```dotenv
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only Supabase secret key>
```

Get both values from Supabase Dashboard → **Project Settings** → **API**. Use the server-only secret key for the local API—not the database password, personal access token, or browser-facing anon key.

## Run the app

Build the frontend once, then serve both the UI and API from one terminal:

```bash
cd /path/to/frontend_seastreet
npm run build
cd api
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8082
```

Open [http://127.0.0.1:8082](http://127.0.0.1:8082).

After any frontend change, run `npm run build` again and refresh the browser. After an API change, stop Uvicorn with `Ctrl+C` and run the Uvicorn command again.

## Enrolling images

The app opens on **Add Image**.

1. Enter first and last name.
2. Choose **Upload from computer** or **Use Mac camera**.
3. Submit the image(s).

If an active identity with the exact same name exists, the files are attached to it. Otherwise, the API creates an active identity. Files are stored in the private `identity-images` bucket under the canonical lowercase/no-space identity folder, for example:

```text
identity-images/manaschan/Manas_Chan.jpg
identity-images/manaschan/Manas_Chan_02.jpg
```

The first upload of a file type uses `First_Last.format`; later same-type images receive a numeric suffix to avoid overwriting existing evidence.

## Important team rules

- Keep `api/.env`, `.env.local`, and `.venv/` private. They are already ignored.
- Do not run `scripts/reconcile-image-storage.mjs`; it was a one-time data-repair tool, not part of normal setup.
- Do not use the Supabase database password or service secret in Vite/browser code.
- This API intentionally binds only to `127.0.0.1`. A teammate must run their own local instance; it is not a shared public server.
- The current API uses a server-only key with broad project access. Treat it as private team tooling only.

## Troubleshooting

**`Database unavailable` or `502` in the app**

- Confirm `api/.env` has the correct URL and server-only secret key.
- Restart Uvicorn after editing `api/.env`.
- Make sure the browser is opened at `http://127.0.0.1:8082`, not the Vite dev port.

**`esbuild` or `rollup` platform error on Apple Silicon**

Dependencies were likely installed under a different architecture/Rosetta mode. From the project root, delete only `node_modules` and `package-lock.json`, then run `npm install` with a native ARM Node installation.

**Mac camera does not start**

- Use `http://127.0.0.1:8082`.
- Allow the browser camera permission when prompted.
- Close any other app using the camera, then try again.
