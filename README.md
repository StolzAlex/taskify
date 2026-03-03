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

This is the default configuration. No environment variables needed.

---

## Configuration

All settings are controlled via environment variables. The defaults work out of the box for local development.

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

### Example: AWS SES via SMTP

```bash
export MAIL_SERVER=email-smtp.us-east-1.amazonaws.com
export MAIL_PORT=587
export MAIL_USE_TLS=true
export MAIL_USERNAME=<SES SMTP username>
export MAIL_PASSWORD=<SES SMTP password>
export MAIL_DEFAULT_SENDER=support@yourdomain.com
python app.py
```

SES SMTP credentials are generated in the AWS console under **SES → SMTP Settings → Create SMTP credentials**.

> **Note:** SES sandbox mode only delivers to verified email addresses. Request production access to send to any address.

### GitHub OAuth SSO (optional)

1. Create a GitHub OAuth App at **Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Set the **Authorization callback URL** to `https://yourdomain.com/auth/github/callback`.
3. Export the credentials:
   ```bash
   export GITHUB_CLIENT_ID=your_client_id
   export GITHUB_CLIENT_SECRET=your_client_secret
   ```
4. Restart the app. A **Login with GitHub** button appears on the employee login page.

Employees link their GitHub account from `/admin/employees`. Once linked, they can sign in via GitHub without a password.

---

## Usage

### For customers (anonymous)

1. Visit **`/`** — fill in email, subject, and description to open a ticket.
2. A confirmation email is sent with a private link: **`/status/<token>`**.
3. Use that link to track status, read replies from support, and send follow-up messages.
4. Replies are disabled once a ticket is `Resolved` or `Closed`.
5. The public link is deactivated (HTTP 410) for resolved/closed tickets. Only logged-in customer account holders can still view their own closed tickets.

Employee names are never shown to customers.

### For customers (with a customer account)

Managers can create customer accounts at `/manager/customers`. The customer receives a welcome email with login credentials.

1. Log in at **`/customer/login`**.
2. **My Tickets** dashboard shows all tickets submitted with your email address.
3. Resolved/closed tickets are shown as plain text (no active link) in the list.
4. Attachments from employee messages are visible on the `/status/<token>` page when logged in.

### For employees

1. Log in at **`/login`** (password or GitHub).
2. The **Dashboard** lists tickets, with a **My Tickets / All Tickets** toggle.
   - Admin and manager accounts default to *All Tickets*.
   - Staff accounts default to *My Tickets* (assigned to them).
   - Filter by status using the dropdown (auto-submits).
3. On a ticket detail page you can:
   - **Add a message** using the Quill rich-text editor. Optionally attach a file inline. Check *Visible to customer* to send it as an email reply.
   - **Change status** — triggers an email notification to the submitter.
   - **Assign** the ticket to an active employee.
   - **Link a GitHub PR** — paste a `https://github.com/…/pull/…` URL in the sidebar card.

### For managers

Managers have access to **`/manager/customers`** in addition to the employee dashboard:
- Create customer accounts (sends a welcome email with credentials).
- Activate or deactivate customer accounts.

### For admins

Admins have access to **`/admin/employees`**:
- Create new employee accounts with optional **Admin** or **Manager** roles.
- Activate or deactivate existing accounts.

**Role summary:**

| Role | Dashboard | Manage customers | Manage employees |
|---|---|---|---|
| Staff | My Tickets (default) | — | — |
| Manager | All Tickets (default) | ✓ | — |
| Admin | All Tickets (default) | ✓ | ✓ |

---

## Multilanguage

The UI ships in **English** and **German**. Users switch language via the globe icon in the top-right navbar. The choice is stored in the session.

### Adding a new language

```bash
# 1. Initialise the locale (e.g. French)
pybabel init -i messages.pot -d translations -l fr

# 2. Edit the translations
#    Fill in msgstr entries in translations/fr/LC_MESSAGES/messages.po

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
├── babel.cfg                   # pybabel extraction config
├── messages.pot                # Translation template (generated)
├── translations/
│   └── de/LC_MESSAGES/
│       ├── messages.po         # German translations (source)
│       └── messages.mo         # German translations (compiled)
├── templates/
│   ├── base.html               # Bootstrap layout, navbar, flash messages
│   ├── submit.html             # Public: submit ticket
│   ├── ticket_status.html      # Public: track ticket + customer replies
│   ├── ticket_closed.html      # Public: 410 page for closed/resolved tickets
│   ├── login.html              # Employee login (+ GitHub SSO button)
│   ├── setup.html              # First-run admin setup
│   ├── dashboard.html          # Employee: ticket list with My/All toggle
│   ├── ticket.html             # Employee: ticket detail, messages, GitHub PR
│   ├── error.html              # 403 / 404 error page
│   ├── admin/
│   │   └── employees.html      # Admin: manage employees + GitHub status
│   ├── customer/
│   │   ├── login.html          # Customer portal login
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

- [ ] Set a strong random `SECRET_KEY`
- [ ] Configure real SMTP credentials
- [ ] Run behind a reverse proxy (nginx, Caddy) with HTTPS
- [ ] Use a production WSGI server: `gunicorn app:app`
- [ ] Set GitHub OAuth callback URL to the HTTPS domain before going live
- [ ] Restrict access to the `uploads/` directory at the web server level (already gated in app by login)
- [ ] Back up `instance/taskify.db` and `uploads/` regularly
