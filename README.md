# Robin Intelligence Database

A facial-recognition database built for Robin, an AI-powered police bodycam identification system developed at DNHacks.

Robin uses a live iPhone camera feed to detect faces, identify potential matches using Clearview AI, and retrieve associated records from Supabase. This repository contains the database management interface that allows our team to add individuals, upload reference photos, and manage the identity records used by the main system.

**The goal is simple:** Upload a person's photo, associate it with their identity, and make that information available to Robin's facial-recognition and records-retrieval pipeline.

This is a hackathon prototype using synthetic law-enforcement records, not a production police database.

---

## How It Works

The database serves as the connection between facial recognition and the information associated with an identified individual.

```text
       Robin Intelligence Database
                  |
                  v
         Add a New Individual
                  |
                  v
       Upload Reference Photos
       (Computer or Mac Camera)
                  |
                  v
       Store Images in Supabase
                  |
                  v
       Link Photos to Identity
                  |
                  v
        Available to Robin
```

When Robin identifies someone through its live camera feed, the main application uses Clearview AI embeddings to search for matching identities in Supabase and retrieve associated synthetic records.

### Database Management Interface

The web interface allows teammates to:

1. **Add Individuals:** Enter a person's first and last name to create an identity record.
2. **Upload Photos:** Add reference images directly from a computer or capture them using a Mac camera.
3. **Manage Existing Identities:** Automatically associate new photos with an existing individual when their name matches.
4. **View Records:** Access the identity records stored in the shared Supabase database.

All teammates connect to the same Supabase project, so uploaded identities and images are available across the team.

---

## Technologies

- **Supabase:** PostgreSQL database and private image storage
- **Python & FastAPI:** Backend API for database operations and image uploads
- **React & TypeScript:** Web-based database management interface
- **Node.js:** Frontend development and build tooling

### Integration with Robin

This repository handles identity enrollment and database management.

The main Robin application handles:

- Live video streaming from an iPhone to a Mac
- Real-time face detection using OpenCV
- Facial embedding generation through Clearview AI
- Identity matching through Supabase
- Retrieval of synthetic law-enforcement records
- Live identification results and audit logging

The two applications use the same Supabase project, allowing identities enrolled through this interface to be used by Robin's recognition pipeline after the necessary embeddings have been generated.

---

## Getting Started

Each teammate runs the interface locally while connecting to the shared Supabase database.

### Requirements

- Node.js 20+
- Python 3.11+
- Access to the team's Supabase project

### 1. Install Dependencies

From the project root:

```bash
npm ci
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Supabase

Create a file named `api/.env`:

```dotenv
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only Supabase secret key>
```

Find these values in:

**Supabase Dashboard → Project Settings → API**

Use the server-side secret key, not the public anonymous key or database password.

**Important:** Never commit your `.env` file or expose the Supabase service key in frontend code. The key provides privileged access to the database and must remain private.

### 3. Run the Application

From the project root:

```bash
npm run build
cd api
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8082
```

Open:

**http://127.0.0.1:8082**

The application serves both the frontend interface and the backend API locally.

After modifying the frontend, run `npm run build` again. After modifying the API, restart Uvicorn.

---

## Adding Individuals and Photos

The application opens on the **Add Image** page.

1. Enter the individual's first and last name.
2. Select **Upload from computer** or **Use Mac camera**.
3. Choose or capture the reference images.
4. Submit the images to Supabase.

If an active identity with the same name already exists, the new photos are linked to that individual.

Otherwise, the system creates a new identity.

### Image Storage

Reference photos are stored in the private Supabase Storage bucket `identity-images`.

For example:

```text
identity-images/
└── manaschan/
    ├── Manas_Chan.jpg
    └── Manas_Chan_02.jpg
```

Images are organized by identity, and additional photos receive numbered filenames to prevent existing files from being overwritten.

The main Robin application can subsequently process these images to generate facial embeddings and associate them with the corresponding identities.

---

## How the Database Connects to Facial Recognition

The full Robin identification workflow operates as follows:

```text
       iPhone Live Camera
                |
                v
       OpenCV Face Detection
                |
                v
      Clearview AI Embedding
                |
                v
      Supabase Vector Search
                |
                v
       Potential Identity Match
                |
                v
       Associated Person Record
                |
                v
      Synthetic Records Lookup
                |
                v
       Robin Interface & Logs
```

This database provides the stored identities, reference images, and associated records used by that workflow.

The database management interface itself does not perform live facial recognition. Recognition and embedding generation are handled by the main Robin application.

---

## Important Notes

- This application is intended for private development and hackathon demonstrations.
- All teammates connect to the same Supabase project.
- The API runs locally on `127.0.0.1:8082` and is not publicly accessible by default.
- Supabase credentials must remain in `api/.env`.
- Do not expose server-side credentials in React, Vite, or browser code.
- Do not run `scripts/reconcile-image-storage.mjs`; it was created for a one-time data repair.
- The current backend uses a privileged Supabase key and is not designed for public deployment.

---

## Troubleshooting

### Database unavailable or 502 error

1. Confirm that `api/.env` contains the correct Supabase URL and secret key.
2. Restart Uvicorn after changing environment variables.
3. Open the application at `http://127.0.0.1:8082`, not the Vite development port.

### Node.js build errors on Apple Silicon

If `esbuild` or `rollup` reports an architecture mismatch, dependencies may have been installed under Rosetta or a different Node.js architecture.

From the project root, remove `node_modules` and `package-lock.json`, then reinstall dependencies using a native ARM version of Node.js:

```bash
npm install
```

### Mac camera not working

- Open the application at `http://127.0.0.1:8082`.
- Allow camera access when prompted by your browser.
- Close other applications that may be using the camera.

---

## Project Context

Robin was developed during DNHacks as a prototype for integrating computer vision, facial recognition, and law-enforcement records retrieval into a live bodycam workflow.

This repository focuses specifically on the **identity database and image enrollment system**, providing the infrastructure needed to store reference images, organize identities, and support the main application's recognition pipeline.
