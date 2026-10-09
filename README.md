# Bulk Certificate Generator API

A backend API that takes a list of recipients in **one request**, generates a PDF certificate for each of them from a single predefined template, and lets the client **track progress** and **retrieve** the results.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy 2 (SQLite by default, any SQL database via `DATABASE_URL`), Pydantic v2, ReportLab (PDF), pytest.

---

## Table of contents
1. [Setup](#setup) · 2. [Run](#run) · 3. [Tests](#tests) · 4. [API usage](#api-usage) · 5. [Design decisions](#design-decisions) · 6. [Project layout](#project-layout) · 7. [Limitations & future scope](#limitations--future-scope)

---

## Setup

```bash
git clone <this-repo> && cd bulk-certificate-generator
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                     # requirements.txt if you only want to run it
```

No external services are needed. The default database is a SQLite file created on first start.

Optional configuration (environment variables, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db` |
| `STORAGE_DIR` | `./data/certificates` | Where generated PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Larger requests are rejected with `413` |
| `DEFAULT_ISSUER` | `Certificate Authority` | Printed when a request has no `issuer` |
| `RECOVER_ON_STARTUP` | `true` | Resume jobs interrupted by a restart |

## Run

```bash
uvicorn app.main:create_app --factory --reload
```

* API: http://127.0.0.1:8000
* Interactive docs (Swagger UI): http://127.0.0.1:8000/docs

> Run a **single** process (no `--workers N`). See [design decisions](#2-background-processing-inside-the-api-process).

## Tests

```bash
pytest
```

43 tests, ~2 seconds, no network or external services. They cover job creation, request- and row-level validation, PDF generation (including text extraction from the PDF), progress while a job is running, per-certificate failure isolation, retry, crash recovery, single and ZIP downloads, listing, filtering and pagination.

---

## API usage

### 1. Submit a bulk generation request

`POST /api/jobs` returns **202 Accepted** immediately; generation happens in the background.

```bash
curl -s -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Annual Python Workshop 2026",
    "issuer": "Aereo Academy",
    "issued_on": "2026-10-09",
    "recipients": [
      {"name": "Priya Sharma", "email": "priya@example.com"},
      {"name": "Rahul Verma",  "email": "rahul@example.com", "achievement": "Winner"},
      {"name": "No Email Here"}
    ]
  }'
```

| Field | Required | Rules |
|---|---|---|
| `event_name` | yes | 1–100 chars |
| `issuer` | no | 1–60 chars, default `DEFAULT_ISSUER` |
| `issued_on` | no | `YYYY-MM-DD`, default today |
| `recipients` | yes | 1 … `MAX_RECIPIENTS_PER_JOB` items |
| `recipients[].name` | yes | 1–80 chars, whitespace collapsed, no control characters |
| `recipients[].email` | yes | valid e-mail, stored lower-case, **unique within the request** |
| `recipients[].achievement` | no | 1–60 chars, default `Participant` |

Response (`202`, header `Location: /api/jobs/{id}`):

```json
{
  "id": "0c2e03ac-303d-4859-8098-99bea00ce734",
  "status": "PENDING",
  "event_name": "Annual Python Workshop 2026",
  "issuer": "Aereo Academy",
  "issued_on": "2026-10-09",
  "progress": {"total": 3, "generated": 0, "failed": 1, "pending": 2, "percent": 33.3},
  "created_at": "2026-10-09T06:46:03.459613Z",
  "started_at": null,
  "completed_at": null,
  "links": {
    "self": "/api/jobs/0c2e03ac-...",
    "certificates": "/api/jobs/0c2e03ac-.../certificates",
    "download_zip": "/api/jobs/0c2e03ac-.../download",
    "retry": "/api/jobs/0c2e03ac-.../retry"
  }
}
```

Rows that fail validation are already counted as `failed` in this first response.

### 2. Track progress

```bash
curl -s http://127.0.0.1:8000/api/jobs/<job_id>
```

```json
{
  "id": "0c2e03ac-...",
  "status": "COMPLETED_WITH_ERRORS",
  "progress": {"total": 3, "generated": 2, "failed": 1, "pending": 0, "percent": 100.0},
  "failures": [
    {"index": 2, "recipient_name": null, "recipient_email": null,
     "error_type": "VALIDATION", "error": "email: Field required"}
  ],
  "failures_truncated": false,
  "...": "..."
}
```

**Job status**

| Status | Meaning |
|---|---|
| `PENDING` | accepted, worker has not started |
| `PROCESSING` | worker is generating; `progress` advances after every certificate |
| `COMPLETED` | every certificate generated |
| `COMPLETED_WITH_ERRORS` | some generated, some failed (see `failures`) |
| `FAILED` | nothing could be generated |

`failures[].error_type` is `VALIDATION` (bad input – fix the data) or `GENERATION` (valid input, rendering/IO failed – retryable). `failures` shows the first 50; use the endpoint below for the full list.

Also: `GET /api/jobs?limit=50&offset=0` lists jobs, newest first.

### 3. Per-recipient results

```bash
curl -s "http://127.0.0.1:8000/api/jobs/<job_id>/certificates?status=FAILED&limit=100&offset=0"
```

`status` (optional) is `PENDING | GENERATED | FAILED`. Each item has `index` (position in your request), recipient data, `status`, `error_type`/`error`, `generated_at`, and `download_url` (only for generated certificates). For validation failures the original payload is echoed back in `input`.

### 4. Retrieve certificates

```bash
# one certificate (PDF)
curl -OJ http://127.0.0.1:8000/api/certificates/<certificate_id>/download

# everything that was generated in a job, as a ZIP
curl -OJ http://127.0.0.1:8000/api/jobs/<job_id>/download

# certificate metadata
curl -s http://127.0.0.1:8000/api/certificates/<certificate_id>
```

ZIP entries are named `0001_Priya_Sharma.pdf`, … where the number is `index + 1` from your request. Downloading a certificate that is not `GENERATED` returns `409` with the reason.

### 5. Retry failed generations

```bash
curl -s -X POST http://127.0.0.1:8000/api/jobs/<job_id>/retry
```

Re-queues only `GENERATION` failures, and only once the job has finished (otherwise `409`). Validation failures are never retried; send a corrected job instead.

### Error codes

`202` accepted · `404` unknown job/certificate · `409` wrong state (not finished, nothing to retry, not generated) · `413` too many recipients · `422` malformed request (missing `event_name`, empty `recipients`, bad date, …)

---

## Design decisions

### 1. Two layers of validation
* **Request-level** problems (no `event_name`, empty/non-list `recipients`, too many recipients) reject the whole request with `422`/`413`. Nothing sensible can be done with such a request.
* **Recipient-level** problems never reject the request. Every recipient is validated on its own, at submit time. Invalid ones are stored as `FAILED (VALIDATION)` with a readable message and the original payload; valid ones go on to generation. This gives the client immediate feedback and satisfies "one bad row must not block the others". Every input row gets a visible outcome, addressable by its `index`.
* Duplicate e-mails inside one request are rejected (second occurrence fails) because a duplicate is almost always a copy-paste error and would produce two certificates for one person.

### 2. Background processing inside the API process
**Choice:** `POST` returns `202` and the work runs in a FastAPI background task; the client polls.

* *Why not synchronous?* 1,000 certificates would hold an HTTP request open for several seconds to minutes, hit proxy/client timeouts, and give no progress. Cost grows linearly with the batch size.
* *Why not Celery/RQ + Redis?* It is the right answer for production scale, but adds a broker, a worker deployment and operational surface that this scope doesn't need. The worker is written against a plain `session_factory`, so `process_job(job_id)` can be moved into a Celery task with no changes to its body.
* *Durability:* all state lives in the database, not in memory. If the process dies mid-job, rows are still `PENDING`; on the next start `recover_jobs` resumes them (`RECOVER_ON_STARTUP`).
* *Known constraint:* run one process. With several workers each would try to recover the same jobs. A multi-process deployment should use a real queue (or per-row claiming via `UPDATE … WHERE status='PENDING'`).
* Measured locally: 500 certificates in ~3.7 s; the `POST` itself returned in ~0.1 s.

### 3. Failure isolation
Each certificate is rendered, written and **committed individually** inside its own `try/except`. A failure is recorded on that row (`GENERATION` + exception text) and the loop continues. Side effects:
* progress is visible in real time (the `progress` numbers change after every certificate);
* a crash loses at most the certificate that was being rendered.

### 4. Progress is derived, not stored
The job has no `generated`/`failed` counters. They are `COUNT(*) GROUP BY status` over the certificate rows (indexed on `(job_id, status)`). One source of truth means counters can't drift after retries or crashes. The terminal job status is computed once from those counts.

### 5. Certificate rendering
`render_certificate(...) -> bytes` is a pure function: no DB, no filesystem. It draws a single A4-landscape template with ReportLab (pure Python, no system dependencies). The font (DejaVu Sans, bundled) is embedded so output is identical on every machine.
Defensive checks turn silent corruption into explicit per-certificate failures:
* characters the font can't draw (e.g. Devanagari) raise an error instead of printing blank boxes;
* long names shrink to fit (down to 10 pt); text that still can't fit (or a single overly long word) raises instead of overflowing the page.

Files are written to a temp name and `os.replace`d, so nobody can download a half-written PDF.

### 6. Database
SQLAlchemy ORM against SQLite by default (zero setup, WAL mode so polling reads don't block the worker). Everything is portable SQL, so PostgreSQL is a `DATABASE_URL` change. Tables: `jobs`, `certificates` (FK + cascade, `UNIQUE(job_id, idx)`). `create_all` creates the schema on start-up.

### 7. API shape
Resource-oriented and bulk-first: one `POST` for N recipients, a job resource to poll, a paginated sub-collection for per-row results, and both single-PDF and ZIP download. The ZIP is built on disk (not in memory) and deleted after the response.

---

## Project layout

```
app/
  main.py                 # app factory, lifespan (job recovery)
  config.py  db.py        # settings, engine/session
  models.py               # Job, Certificate, enums
  schemas.py              # request/response models + field rules
  validation.py           # per-recipient validation (never raises for bad rows)
  services/
    jobs.py               # create job, derive counts/status, requeue failures
    processor.py          # background worker + crash recovery
    renderer.py           # the certificate template (ReportLab)
  api/
    jobs.py certificates.py deps.py serializers.py
  assets/fonts/           # bundled DejaVu Sans (Bitstream Vera licence)
tests/                    # 43 tests
```

## Limitations & future scope

* **Queue:** move `process_job` to Celery/RQ/Arq for multi-worker deployments and horizontal scaling.
* **Migrations:** schema is created with `create_all`; adopt Alembic before changing the schema in production (an existing DB with an older schema is not altered).
* **Scripts other than Latin/Cyrillic/Greek:** need a different font (e.g. Noto Sans Devanagari) and text shaping; currently reported as a clear per-certificate failure.
* **Auth, rate limiting and per-user job ownership** are not implemented.
* **Idempotency-Key** header on `POST /api/jobs` to make client retries safe.
* **Object storage (S3)** instead of local disk; signed download URLs.
* **Template choice / branding** (logo, signature image), QR code + public verification endpoint, e-mailing certificates.
* **Streaming progress** via SSE/WebSocket instead of polling; webhook on job completion.
* **Parallel rendering** with a process pool for very large jobs (rendering is CPU-bound).
