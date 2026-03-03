# Taskify — Support Ticket System

A lightweight support ticket web app. Customers submit tickets via a public form and track progress via a private link. Employees manage tickets internally with rich-text messages, file attachments, assignments, and status changes. Status updates are emailed back to the submitter.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Mail, Flask-Babel, Authlib |
| Database | SQLite (single file, zero config) |
| UI | Bootstrap 5 + Bootstrap Icons, Quill.js rich-text editor (all CDN) |
| Templates | Jinja2 (server-side, no build step) |
| i18n | gettext/pybabel — English and German included |
| Auth | Password-based (employees + customers) + GitHub OAuth SSO (employees) |

---

## Installation

```bash
# 1. Clone / download the project
cd taskify

# 2. Create and activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your local environment file
cp .env.example .env
# then edit .env with your settings
```

---

## Running in Development

```bash
python app.py
```

The server starts on **http://localhost:5000**.

### First-run setup

On a fresh database, visit **http://localhost:5000/setup** to create the first admin account. The setup page is automatically disabled once any employee account exists.

### Email in development

Start a local debug SMTP server in a separate terminal — it prints every outgoing email to the console without delivering it:

```bash
python -m aiosmtpd -n -l localhost:1025
```

This matches the default `MAIL_SERVER=localhost` / `MAIL_PORT=1025` in `.env.example`.

---

## Configuration

Settings are loaded from a **`.env` file** in the project root (via `python-dotenv`), with environment variables as fallback. Copy `.env.example` to `.env` and fill in the values you need — everything else uses the defaults shown below.

`.env` is listed in `.gitignore` and must never be committed.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-in-production` | Flask session signing key — **change in production** |
| `DATABASE_URL` | `sqlite:///taskify.db` | SQLAlchemy DB URI |
| `MAIL_SERVER` | `localhost` | SMTP host |
| `MAIL_PORT` | `1025` | SMTP port |
| `MAIL_USE_TLS` | `false` | Enable STARTTLS |
| `MAIL_USERNAME` | _(none)_ | SMTP username |
| `MAIL_PASSWORD` | _(none)_ | SMTP password |
| `MAIL_DEFAULT_SENDER` | `noreply@taskify.local` | From address |
| `MAIL_SUPPRESS_SEND` | `false` | Set to `true` to silently drop all emails |
| `BABEL_DEFAULT_LOCALE` | `en` | Default UI language (`en` or `de`) |
| `GITHUB_CLIENT_ID` | _(none)_ | GitHub OAuth App client ID (optional) |
| `GITHUB_CLIENT_SECRET` | _(none)_ | GitHub OAuth App client secret (optional) |

### Example `.env` for production

```dotenv
SECRET_KEY=replace-with-64-random-chars

DATABASE_URL=sqlite:///taskify.db

MAIL_SERVER=email-smtp.us-east-1.amazonaws.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=AKIAIOSFODNN7EXAMPLE
MAIL_PASSWORD=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
MAIL_DEFAULT_SENDER=support@yourdomain.com

GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret
```

> **AWS SES note:** SES sandbox mode only delivers to verified addresses. Request production access to send to any address. Credentials are generated under **SES → SMTP Settings → Create SMTP credentials**.

### GitHub OAuth SSO (optional)

1. Go to **github.com/settings/developers → OAuth Apps → New OAuth App**.
2. Set the fields:

   | Field | Value |
   |---|---|
   | Homepage URL | `http://localhost:5000` (dev) / `https://yourdomain.com` (prod) |
   | Authorization callback URL | `http://localhost:5000/auth/github/callback` |

3. Copy **Client ID** and generate a **Client Secret**, then add them to `.env`:
   ```dotenv
   GITHUB_CLIENT_ID=your_client_id
   GITHUB_CLIENT_SECRET=your_client_secret
   ```
4. Restart the app — a **Login with GitHub** button appears on the login page automatically.

Employees link their GitHub account from `/admin/employees`. Once linked, they can sign in via GitHub without a password. Update the callback URL in your GitHub OAuth App settings when you go to production.

---

## Usage

### Login

All users — employees and customers — log in at **`/login`** using their **email address** and password, or (employees only) via **GitHub OAuth**.

### For customers (anonymous)

1. Visit **`/`** — fill in email, subject, and description to open a ticket.
2. A confirmation email is sent with a private link: **`/status/<token>`**.
3. Use that link to track status, read replies from support, and send follow-up messages.
4. Replies are disabled once a ticket is `Resolved` or `Closed`.
5. The public link is deactivated (HTTP 410) for resolved/closed tickets. Only logged-in customer account holders can still view their own closed tickets.

Employee names are never shown to customers.

### For customers (with a customer account)

Managers can create customer accounts at `/manager/customers`. The customer receives a welcome email with login credentials.

1. Log in at **`/login`** with your email and password.
2. When submitting a ticket, your email is pre-filled and locked.
3. **My Tickets** dashboard shows all tickets submitted with your email address.
4. Resolved/closed tickets are shown as plain text (no active link) in the list.
5. Attachments from employee messages are visible on the `/status/<token>` page when logged in.

### For employees

1. Log in at **`/login`** with your email (or via GitHub).
2. The **Dashboard** lists tickets, with a **My Tickets / All Tickets** toggle.
   - Admin and manager accounts default to *All Tickets*.
   - Staff accounts default to *My Tickets* (tickets assigned to them).
   - Filter by status using the dropdown (auto-submits).
3. On a ticket detail page you can:
   - **Add a message** using the Quill rich-text editor. Optionally attach a file inline. Check *Visible to customer* to send it as an email reply.
   - **Change status** — triggers an email notification to the submitter.
   - **Assign** the ticket to an active employee.
   - **Link a GitHub PR** — paste a `https://github.com/…/pull/…` URL in the sidebar card.
4. When submitting a new ticket, your email is pre-filled and locked.

### For managers

Managers have access to **`/manager/customers`** in addition to the employee dashboard:
- Create customer accounts (sends a welcome email with credentials).
- Activate/deactivate or delete customer accounts.

### For admins

Admins have access to **`/admin/employees`**:
- Create new employee accounts with optional **Admin** or **Manager** roles.
- Activate/deactivate or delete employee accounts.
- Link GitHub accounts (own row only).

**Role summary:**

| Role | Dashboard default | Manage customers | Manage employees |
|---|---|---|---|
| Staff | My Tickets | — | — |
| Manager | All Tickets | ✓ | — |
| Admin | All Tickets | ✓ | ✓ |

---

## Multilanguage

The UI ships in **English** and **German**. Users switch language via the globe icon in the navbar. The choice is stored in the session.

### Adding a new language

```bash
# 1. Initialise the locale (e.g. French)
pybabel init -i messages.pot -d translations -l fr

# 2. Fill in msgstr entries in translations/fr/LC_MESSAGES/messages.po

# 3. Compile
pybabel compile -d translations
```

Then add `'fr'` to `BABEL_SUPPORTED_LOCALES` in `config.py` and a link in `templates/base.html`.

### Updating translations after code changes

```bash
pybabel extract -F babel.cfg -k _l -o messages.pot .
pybabel update -i messages.pot -d translations
# edit .po files, then:
pybabel compile -d translations
```

---

## Project Structure

```
taskify/
├── app.py                      # Routes and application logic
├── models.py                   # SQLAlchemy models
├── config.py                   # Environment-based configuration
├── requirements.txt
├── .env                        # Local secrets — never commit (gitignored)
├── .env.example                # Template committed to version control
├── babel.cfg                   # pybabel extraction config
├── messages.pot                # Translation template (generated)
├── translations/
│   └── de/LC_MESSAGES/
│       ├── messages.po         # German translations (source)
│       └── messages.mo         # German translations (compiled)
├── templates/
│   ├── base.html               # Bootstrap layout, navbar, flash messages
│   ├── submit.html             # Public: submit ticket (email pre-filled when logged in)
│   ├── ticket_status.html      # Public: track ticket + customer replies
│   ├── ticket_closed.html      # Public: 410 page for closed/resolved tickets
│   ├── login.html              # Unified login for employees and customers
│   ├── setup.html              # First-run admin setup
│   ├── dashboard.html          # Employee: ticket list with My/All toggle
│   ├── ticket.html             # Employee: ticket detail, messages, GitHub PR
│   ├── error.html              # 403 / 404 error page
│   ├── admin/
│   │   └── employees.html      # Admin: manage employees + GitHub status
│   ├── customer/
│   │   └── dashboard.html      # Customer: ticket list
│   └── manager/
│       └── customers.html      # Manager: create and manage customer accounts
├── static/
│   └── style.css               # Custom CSS overrides
└── uploads/                    # Uploaded attachments (gitignored)
```

---

## Data Model

```
Employee   — id, username, email, password_hash (nullable), is_admin, is_manager,
             is_active, github_id (unique), github_login, created_at
Customer   — id, email (unique), name, password_hash, is_active,
             created_by_id FK → employees, created_at
Ticket     — id, token (UUID), submitter_email, subject, body, status,
             github_pr_url, created_at, updated_at
Assignment — ticket_id FK, employee_id FK          [current assignee, one per ticket]
Message    — ticket_id FK, employee_id FK (null for customer replies),
             body (HTML), is_customer_visible, is_customer_reply, created_at
Attachment — ticket_id FK, message_id FK (null for ticket-level), filename (UUID on disk),
             original_filename, size, created_at
```

**Ticket statuses:** `open` → `in_progress` → `resolved` → `closed`

---

## Emails sent

| Trigger | Recipient |
|---|---|
| Ticket submitted | Submitter — confirmation + status link |
| Status changed | Submitter — new status |
| Employee adds customer-visible message | Submitter — message content |
| Customer replies on status page | Assignee (or all active employees if unassigned) |
| Manager creates customer account | Customer — welcome email with login credentials |

---

## Migrating an existing database

If you have an existing `instance/taskify.db`, run the following SQL before restarting:

```sql
ALTER TABLE employees ADD COLUMN is_manager BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE employees ADD COLUMN github_id  VARCHAR(50);
ALTER TABLE employees ADD COLUMN github_login VARCHAR(100);
ALTER TABLE tickets   ADD COLUMN github_pr_url VARCHAR(500);

-- employees.password_hash is already nullable in SQLite (no migration needed)

CREATE TABLE customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         VARCHAR(120) UNIQUE NOT NULL,
    name          VARCHAR(120) NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT 1,
    created_by_id INTEGER REFERENCES employees(id),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

A fresh install (`db.create_all()` on startup) handles all of this automatically.

---

## Production checklist

- [ ] Set a strong random `SECRET_KEY` in `.env`
- [ ] Configure real SMTP credentials in `.env`
- [ ] Run behind a reverse proxy (nginx, Caddy) with HTTPS
- [ ] Use a production WSGI server: `gunicorn app:app`
- [ ] Update the GitHub OAuth App callback URL to the HTTPS domain
- [ ] Restrict access to the `uploads/` directory at the web server level (already gated in app by login)
- [ ] Back up `instance/taskify.db` and `uploads/` regularly
