# CLAUDE.md — The Crucible Repair Shop App

## Project Overview

**The Crucible** is a Flask web application for managing a device repair shop. It handles the full repair lifecycle: customer booking, job tracking with SMS notifications, invoicing with BC tax, and a loyalty coin system ("Crucible Coins").

The name "invoicetosms" (repo name) reflects the original core feature — texting customers their invoices and status updates via Twilio.

---

## Architecture

```
invoicetosms/
├── app.py              # Flask app factory — all routes live here
├── config.py           # Config class (env vars, business settings)
├── wsgi.py             # WSGI entrypoint for gunicorn
├── models/
│   └── database.py     # SQLite schema, get_db/close_db/init_db, VALID_STATUSES
├── services/
│   ├── auth.py         # Admin auth: hashing, login_required decorator
│   ├── sms.py          # Twilio SMS: templates, send_sms, notify_customer
│   ├── invoice.py      # Invoice creation: BC tax, coin discounts, SMS delivery
│   └── wallet.py       # Crucible Coin wallet: earn, spend, balance, history
├── templates/          # Jinja2 HTML templates
├── static/css/         # Stylesheets
├── Dockerfile          # Container build; SQLite DB lives at /data/crucible.db
├── Procfile            # Railway/Render/Heroku: gunicorn on $PORT
├── requirements.txt    # Flask, python-dotenv, twilio, gunicorn
└── .env.example        # Environment variable template
```

---

## Tech Stack

- **Python 3.12 / Flask 3.1** — web framework, app factory pattern
- **SQLite** — single-file database via `sqlite3` (no ORM)
- **Twilio** — SMS notifications (`twilio==9.4.0`)
- **Gunicorn** — production WSGI server
- **Jinja2** — templating (built into Flask)
- **python-dotenv** — `.env` file loading

---

## Database Schema

All tables are created on startup via `init_db()` using `CREATE TABLE IF NOT EXISTS`.

| Table | Purpose |
|---|---|
| `customers` | Name, phone, email |
| `repair_jobs` | Device info, status, estimated/final cost, notes |
| `status_history` | Audit trail of every status change |
| `sms_log` | Every SMS attempted: message, phone, sent/failed |
| `wallets` | Per-customer Crucible Coin balance |
| `wallet_transactions` | Full debit/credit history with `balance_after` |
| `admins` | Single-admin credentials (salted SHA-256) |
| `invoices` | Subtotal, tax, coin discount, total, paid status |

**Job status pipeline** (defined in `VALID_STATUSES`):
```
intake → diagnosing → waiting_for_parts → in_repair → testing → ready_for_pickup → completed/cancelled
```

SMS notifications are sent automatically on: `diagnosing`, `waiting_for_parts`, `in_repair`, `ready_for_pickup`, `completed`.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```
SECRET_KEY=<random string>
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_PHONE_NUMBER=+1250XXXXXXX
DATABASE_PATH=/data/crucible.db   # optional; defaults to ./crucible.db
```

The app runs without Twilio configured — SMS sends are skipped with a warning log, and the UI shows a "pending" message.

---

## Running Locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY at minimum
python app.py          # dev server on http://localhost:5000
```

First visit goes to `/setup` to create the admin account. After that, `/login` is the entry point.

**Production (gunicorn):**
```bash
gunicorn wsgi:app --bind 0.0.0.0:8000 --workers 2
```

**Docker:**
```bash
docker build -t crucible .
docker run -p 8000:8000 -v crucible-data:/data --env-file .env crucible
```

Mount `/data` as a volume so `crucible.db` survives container restarts.

---

## Route Map

### Public routes (no login required)
| Route | Description |
|---|---|
| `GET/POST /book` | Customer device intake form |
| `GET /book/confirmation/<id>` | Booking confirmation page |
| `GET /track` | Phone number lookup form |
| `GET /track/result` | Shows repair status for a phone number |
| `GET/POST /login` | Admin login |
| `GET/POST /setup` | First-run admin account creation (locked after first admin) |
| `GET /logout` | Clear session |

### Admin routes (`@login_required`)
| Route | Description |
|---|---|
| `GET /` | Dashboard with job list, search, status filter |
| `GET /job/<id>` | Job detail: history, SMS log, wallet balance |
| `POST /job/<id>/update-status` | Change status → triggers SMS + coin reward |
| `POST /job/<id>/edit` | Update estimated/final cost and notes |
| `GET/POST /job/<id>/invoice/new` | Preview and create invoice |
| `GET /invoice/<id>` | Invoice detail |
| `POST /invoice/<id>/mark-paid` | Mark invoice as paid |
| `POST /invoice/<id>/send-sms` | Text invoice to customer |
| `GET /invoices` | Invoice list |
| `GET /sms-log` | Full SMS audit log |
| `GET /wallets` | All customer coin balances |
| `GET /wallet/<customer_id>` | Customer wallet detail + transaction history |
| `POST /wallet/<customer_id>/adjust` | Manual coin credit/debit |

---

## Key Business Logic

### Crucible Coins (`services/wallet.py`)
- **Earn rate:** 5 coins flat + 1 coin per $10 spent when a job is marked `completed`
- **Redemption:** Up to 25% of invoice total, 1 coin = $1 CAD
- Coins are spent atomically before invoice is saved; failed spend reverts to no discount
- `reward_completed_job()` is called automatically in the status update flow

### Invoicing (`services/invoice.py`)
- BC tax rate: **12%** (5% GST + 7% PST) — hardcoded as `TAX_RATE = 0.12`
- Flow: `preview_invoice()` (read-only) → user confirms coins to apply → `create_invoice()` (persists)
- `send_invoice_sms()` sends a formatted text and logs to `sms_log`

### Authentication (`services/auth.py`)
- Passwords hashed with `salt:SHA256(salt+password)` — no bcrypt, just `hashlib.sha256`
- Session stores `admin_id` and `admin_username`
- `login_required` decorator redirects to `/login?next=<path>` and restores destination after login
- Only one admin account supported (multi-admin not implemented)

### SMS (`services/sms.py`)
- `SMS_TEMPLATES` dict maps status → message template; statuses not in the dict send no SMS
- Twilio credentials missing → logs warning, returns `sent=False`, UI shows graceful fallback
- All SMS attempts (sent or failed) are written to `sms_log`

---

## Code Conventions

- **No ORM** — raw `sqlite3` queries everywhere. Use `db.execute(query, params)` with `?` placeholders.
- **App factory** — `create_app()` in `app.py`; all routes are nested closures inside it.
- **`g` for DB connection** — `get_db()` stores the connection on Flask's `g` object; `close_db()` is registered with `teardown_appcontext`.
- **`sqlite3.Row`** — rows behave like dicts; access columns by name (e.g. `job["status"]`).
- **Foreign keys enabled** — `PRAGMA foreign_keys = ON` is set on every connection.
- **No test suite** — there are no automated tests in the repo.
- **No migrations** — schema changes require manual `ALTER TABLE` or a DB rebuild.
- **Flash messages** for all user feedback; categories are `"success"` and `"error"`.
- **Business name** is in `config.py` as `BUSINESS_NAME = "The Crucible"` and `BUSINESS_REGION = "British Columbia"`.

---

## Adding New Features

### Adding a new route
1. Add the route function inside `create_app()` in `app.py`.
2. If admin-only, apply the `@login_required` decorator.
3. Add a template in `templates/` extending `base.html`.
4. Update nav in `base.html` if needed.

### Adding a new DB table
1. Add `CREATE TABLE IF NOT EXISTS ...` to the `SCHEMA` string in `models/database.py`.
2. The table is created automatically on next startup via `init_db()`.

### Adding a new SMS notification
Add an entry to `SMS_TEMPLATES` in `services/sms.py` with the status name as key and the template string as value. The `notify_customer()` call in `update_status` picks it up automatically.

### Adding a new coin earn trigger
Call `add_coins(customer_id, amount, reason, job_id=job_id)` from `services/wallet.py` at the appropriate point in the flow. The wallet is created automatically if the customer doesn't have one yet.

---

## Deployment Notes

- **SQLite persistence in Docker:** mount `/data` as a named volume; `DATABASE_PATH=/data/crucible.db` is set in the Dockerfile.
- **Railway/Render/Heroku:** `Procfile` runs gunicorn on `$PORT`; set all env vars in the platform dashboard.
- **Scaling:** SQLite is single-writer; this app is designed for a single-shop, low-concurrency workload. Do not run multiple gunicorn workers that write concurrently without switching to PostgreSQL.
- **2 workers** in gunicorn config (`--workers 2`) is intentional for read-heavy workloads on SQLite.

---

## Known Limitations / Future Work

- Password hashing uses SHA-256, not bcrypt — acceptable for a private single-admin tool, but not production-grade for multi-user systems.
- No automated tests.
- No database migrations — adding columns requires manual SQL or a DB reset.
- Only one admin account supported.
- No email notifications (SMS only).
- Phone number matching on `/track/result` strips common separators but is not E.164-aware.
