# Taskify — Support Ticket System

A lightweight support ticket web app. Customers submit tickets via a public (or restricted) form and track progress via a private link. Employees manage tickets internally with rich-text messages, file attachments, assignments, status changes, and GitHub integration. Status updates are emailed to the submitter.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Mail, Flask-Babel, Authlib |
| Database | SQLite (single file, zero config) — PostgreSQL also supported |
| UI | Bootstrap 5 + Bootstrap Icons, Quill.js rich-text editor (all CDN) |
| Templates | Jinja2 (server-side, no build step) |
| i18n | gettext/pybabel — English and German included |
| Auth | Password-based (employees + customers) + GitHub OAuth SSO (employees) |

---

## Installation

```bash
# 1. Clone / download the project
cd taskify

# 2. Create and activate a virtual environment
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

All settings are loaded from a **`.env` file** in the project root (via `python-dotenv`). Copy `.env.example` to `.env` and fill in the values you need. `.env` is gitignored and must never be committed.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-…` | Flask session signing key — **change in production** |
| `DATABASE_URL` | `sqlite:///taskify.db` | SQLAlchemy DB URI |
| `PUBLIC_TICKETS` | `true` | Set to `false` to require login for ticket submission and status views |
| `MAIL_SERVER` | `localhost` | SMTP host |
| `MAIL_PORT` | `1025` | SMTP port |
| `MAIL_USE_TLS` | `false` | Enable STARTTLS |
| `MAIL_USERNAME` | _(none)_ | SMTP username |
| `MAIL_PASSWORD` | _(none)_ | SMTP password |
| `MAIL_DEFAULT_SENDER` | `noreply@taskify.local` | From address |
| `MAIL_USE_SSL` | `false` | Enable SSL/TLS on connect (port 465). Use instead of `MAIL_USE_TLS` for implicit TLS |
| `MAIL_SUPPRESS_SEND` | `false` | Set to `true` to silently drop all emails (dev mode) |
| `BABEL_DEFAULT_LOCALE` | `en` | Default UI language (`en` or `de`) |
| `GITHUB_CLIENT_ID` | _(none)_ | GitHub OAuth App client ID — enables SSO login button |
| `GITHUB_CLIENT_SECRET` | _(none)_ | GitHub OAuth App client secret |
| `GITHUB_ORG` | _(none)_ | GitHub organisation name — scopes PR/issue search and repo listing |
| `GITHUB_TOKEN` | _(none)_ | GitHub Personal Access Token — enables PR/issue search and issue creation |
| `MANTIS_DB_HOST` | _(none)_ | MantisBT MySQL/MariaDB hostname — pre-fills the sync form |
| `MANTIS_DB_PORT` | `3306` | MantisBT database port |
| `MANTIS_DB_NAME` | `bugtracker` | MantisBT database name |
| `MANTIS_DB_USER` | _(none)_ | MantisBT database username |
| `MANTIS_DB_PASS` | _(none)_ | MantisBT database password |
| `MANTIS_TABLE_PREFIX` | `mantis_` | MantisBT table prefix |

---

## GitHub Integration

Taskify has three independent layers of GitHub integration. Each requires different credentials and can be enabled separately.

### 1 — GitHub OAuth SSO (employee login via GitHub)

Lets employees sign in with their GitHub account instead of a password.

1. Go to **github.com → Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Fill in:

   | Field | Development | Production |
   |---|---|---|
   | Homepage URL | `http://localhost:5000` | `https://yourdomain.com` |
   | Authorization callback URL | `http://localhost:5000/auth/github/callback` | `https://yourdomain.com/auth/github/callback` |

3. Copy **Client ID** and generate a **Client Secret**, then add to `.env`:
   ```dotenv
   GITHUB_CLIENT_ID=your_client_id
   GITHUB_CLIENT_SECRET=your_client_secret
   ```
4. Restart the app — a **Login with GitHub** button appears on the login page.

Admins link employee accounts to GitHub from **`/admin/employees`** by entering the employee's GitHub username. The app resolves it via the GitHub API and stores the numeric user ID. Once linked, that employee can sign in via GitHub without a password.

> Update the callback URL in your GitHub OAuth App settings when you go to production.

---

### 2 — PR / Issue search and GitHub Issue creation

Enables live PR/issue search in the ticket sidebar and one-click GitHub issue creation from a ticket.

**Requires a Personal Access Token (PAT):**

1. Go to **github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Resource owner** to your organisation (GitHub will request org admin approval).
3. Under **Repository access**, select the repositories to include.
4. Under **Permissions**, grant at minimum:
   - **Issues: Read and write** (enables search + issue creation)
5. Copy the token and add it to `.env`:
   ```dotenv
   GITHUB_TOKEN=github_pat_xxxxxxxx
   ```

**Optionally scope search to your organisation:**

```dotenv
GITHUB_ORG=your-org-name
```

When set:
- The PR/issue search box is scoped to that org automatically.
- The **Create GitHub Issue** sidebar card lists only repos from that org.
- Search supports a `reponame: query` prefix to narrow to a single repo (see below).

> **Fine-grained token and org approval:** If the token is created with an org as resource owner, an org admin must approve it under **Organisation settings → Personal access tokens → Pending requests** before it works.
>
> **Alternative — classic PAT:** A classic token (`ghp_` prefix) with `repo` and `read:org` scopes works without org admin approval but grants broader access.

---

### 3 — Searching PRs and issues from a ticket

On any ticket detail page, the **GitHub Link** sidebar card lets you search and link a PR or issue:

- Type a query to search across all org repos (when `GITHUB_ORG` is set).
- Prefix with `reponame:` to narrow to a specific repo:
  ```
  website: login bug
  ```
  This searches `org/website` for "login bug".
- Clicking a result links it to the ticket, storing the URL and title.
- When a linked **issue** is closed on GitHub, the Taskify ticket is automatically set to **Closed** the next time the ticket detail page is opened.

---

### 4 — Creating a GitHub Issue from a ticket

The **Create GitHub Issue** card in the ticket sidebar (visible when `GITHUB_TOKEN` and `GITHUB_ORG` are configured) lets you:

1. Expand the card — repos are loaded from the org via the GitHub API.
2. Select a repository from the dropdown.
3. Click **Create Issue** — GitHub creates the issue with:
   - **Title**: ticket subject
   - **Body**: ticket description + submitter email + Taskify ticket reference
   - **Labels**: `enhancement`, `patch` (created in the repo if missing)
   - **Assignee**: the ticket's assigned employee's GitHub login (if linked)
4. The new issue is automatically linked back to the ticket.

---

## Usage

### Login

All users — employees and customers — log in at **`/login`** using their **email address** and password, or (employees only) via **GitHub OAuth**.

### Public ticket mode

When `PUBLIC_TICKETS=true` (default), anyone can submit a ticket at `/` and track it at `/status/<token>` without logging in.

When `PUBLIC_TICKETS=false`:
- `/` and `/status/<token>` require a customer or employee login.
- Unauthenticated visitors are redirected to `/login` (the status link is preserved as `?next=` so they land back after login).

### For customers (anonymous)

1. Visit **`/`** — fill in email, subject, and description to open a ticket.
2. A confirmation email is sent with a private link: **`/status/<token>`**.
3. Use that link to track status, read replies from support, and send follow-up messages.
4. Replies are disabled once a ticket is `Resolved` or `Closed`.
5. The public link shows a friendly 410 page for resolved/closed tickets. Only logged-in customer account holders can still view their own closed tickets.

Employee names are never shown to customers.

### For customers (with a customer account)

Managers create customer accounts at `/manager/customers`. The customer receives a welcome email with login credentials.

1. Log in at **`/login`** with your email and password.
2. When submitting a ticket, your email is pre-filled and locked.
3. **My Tickets** dashboard shows all tickets submitted with your email address.
4. Resolved/closed tickets appear as plain text (no active link) in the list.
5. Attachments from employee messages are visible on the `/status/<token>` page.

### For employees

1. Log in at **`/login`** with your email (or via GitHub).
2. The **Dashboard** lists tickets, with a **My Tickets / All Tickets** toggle.
   - Admin and manager accounts default to *All Tickets*.
   - Staff accounts default to *My Tickets* (tickets assigned to them).
   - A **Watched** tab shows only tickets you are subscribed to, each marked with an eye icon.
   - The chosen view is **saved per user** in the database and restored on next login.
   - Filter by status or project using the dropdowns (auto-submit on change). The project dropdown appears automatically when any projects exist.
3. On a ticket detail page you can:
   - **Add a message** using the Quill rich-text editor. Optionally attach a file inline. Check *Visible to customer* to send it as an email reply.
   - **Change status** — triggers an email notification to the submitter and all watchers.
   - **Assign** the ticket to an active employee.
   - **Watch / Unwatch** — subscribe to email notifications for this ticket (status changes, customer replies, new internal messages). The sidebar button toggles your watch. Watched tickets appear in the **Watched** tab on the dashboard and are marked with an eye icon in the ticket table.
   - **Set an internal title** — optional free-text title visible only to employees. When set, it is used as the GitHub issue title instead of the customer-facing subject.
   - **Assign to a project** — select a project from the *Project* sidebar card. All customers in that project can see the ticket in their **Project Tickets** tab.
   - **Link a GitHub PR or Issue** — search via the sidebar card; supports `reponame: query` prefix.
   - **Create a GitHub Issue** — select a repo and click Create; the issue is linked automatically. Uses the internal title if set, otherwise falls back to the ticket subject.
   - If the linked GitHub issue is closed, the ticket status is synced to *Closed* on next page load.

### For managers

Managers have access to **`/manager/customers`**:
- Create customer accounts (sends a welcome email with credentials).
- Assign customers to one or more **Projects** (multi-select; type a new name to create inline).
- Edit a customer's name, email, project assignments, or password (pencil button).
- Activate/deactivate or delete customer accounts.

### For admins

Admins have access to **`/admin/employees`**, **`/admin/mantis-sync`**, **`/admin/mail-test`**, and **`/admin/tests`**.

**`/admin/mantis-sync`** — import projects, users, and tickets from a MantisBT MySQL/MariaDB database. A **Test Run** mode (on by default) calculates and previews all changes without saving anything. MantisBT access levels map to Taskify roles: Viewer/Reporter/Updater → Customer, Developer → Staff, Project Manager → Manager. Projects that already exist in Taskify are pre-unchecked in the selection.
- Create new employee accounts with optional **Admin** or **Manager** roles.
- Edit an employee's name, email address, or password (pencil button).
- Activate/deactivate or delete employee accounts.
- Link or unlink GitHub accounts for any employee.

**`/admin/mail-test`** — displays the active mail configuration (password masked) and sends a raw SMTP test email to any address, bypassing Flask-Mail so the exact error is shown if delivery fails.

**`/admin/tests`** — runs a full health check: database connectivity, configuration completeness, SMTP reachability, inbound-email thread status, GitHub API access, and functional tests (ticket/employee/customer CRUD, status transitions, assignment, watching, deletion cascade). Infrastructure checks are read-only; functional tests create and immediately delete sentinel records.

Admins can also **delete any ticket** from the ticket detail page (sidebar → Delete Ticket). This permanently removes the ticket, all messages and attachments, the audit log, assignment, watches, and the upload directory on disk.

**Editing privileges:** Admins can edit managers and staff, but not other admins. Managers can edit staff only. Password fields are optional — leave blank to keep the current password.

**Role summary:**

| Role | Dashboard default | Manage customers | Manage employees |
|---|---|---|---|
| Staff | My Tickets | — | — |
| Manager | All Tickets | Create, edit, activate/deactivate, delete | — |
| Admin | All Tickets | ✓ (via manager page) | Create, edit, activate/deactivate, delete |

---

## Help pages

Each role gets a tailored manual at **`/help`** (employees) and **`/customer/help`** (customers), rendered from Markdown with a sticky table-of-contents sidebar.

| Role | Default manual | Can switch to |
|------|---------------|---------------|
| Admin | `manual-admin` | All four manuals |
| Manager | `manual-manager` | — |
| Staff | `manual-employee` | — |
| Customer | `manual-customers` | — |

Admins see a button group in the help page header to switch between all four manuals (`?manual=<stem>` query param).

Manuals are bilingual. When the user's session language is German, the route serves `<name>.de.md` if it exists, falling back to the English file. Adding a third language requires only a new `<name>.<locale>.md` file — no code changes.

---

## Multilanguage

The UI ships in **English** and **German**. Users switch language via the globe icon in the navbar. The choice is stored in the session.

### Adding a new language

```bash
pybabel init -i messages.pot -d translations -l fr
# Fill in msgstr entries in translations/fr/LC_MESSAGES/messages.po
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
├── backup.sh                   # Cron-ready backup script (DB + uploads)
├── restore.sh                  # Interactive restore script with safety snapshot
├── .env                        # Local secrets — never commit (gitignored)
├── .env.example                # Template committed to version control
├── babel.cfg                   # pybabel extraction config
├── messages.pot                # Translation template (generated)
├── translations/
│   └── de/LC_MESSAGES/
│       ├── messages.po         # German translations (source)
│       └── messages.mo         # German translations (compiled)
├── docs/
│   ├── manual-employee.md      # In-app help: employee (EN)
│   ├── manual-employee.de.md   # In-app help: employee (DE)
│   ├── manual-manager.md       # In-app help: manager extras (EN)
│   ├── manual-manager.de.md    # In-app help: manager extras (DE)
│   ├── manual-admin.md         # In-app help: admin reference (EN)
│   └── manual-admin.de.md      # In-app help: admin reference (DE)
├── templates/
│   ├── base.html               # Bootstrap layout, navbar, flash messages
│   ├── submit.html             # Public: submit ticket
│   ├── ticket_status.html      # Public: track ticket + customer replies
│   ├── ticket_closed.html      # 410 page for closed/resolved tickets
│   ├── login.html              # Unified login for employees and customers
│   ├── setup.html              # First-run admin setup
│   ├── dashboard.html          # Employee: ticket list with My/All toggle
│   ├── ticket.html             # Employee: ticket detail, messages, GitHub sidebar
│   ├── help.html               # Role-based in-app manual viewer
│   ├── error.html              # 403 / 404 error page
│   ├── admin/
│   │   ├── employees.html      # Admin: manage employees + GitHub linking
│   │   ├── mantis_sync.html    # Admin: MantisBT import with dry-run preview
│   │   ├── mail_test.html      # Admin: live SMTP config check + test send
│   │   └── tests.html          # Admin: infrastructure + functional system tests
│   ├── customer/
│   │   └── dashboard.html      # Customer: ticket list
│   └── manager/
│       └── customers.html      # Manager: create and manage customer accounts
├── static/
│   └── style.css
└── uploads/                    # Uploaded attachments (gitignored)
```

---

## Data Model

```
Employee   — id, username, email, password_hash (nullable), is_admin, is_manager,
             is_active, github_id (unique), github_login, preferences (JSON),
             created_at
Group      — id, name (unique), created_at           [called "Project" in the UI]
Customer   — id, email (unique), name, password_hash, is_active,
             created_by_id FK → employees, created_at
             ↔ groups  (many-to-many via customer_groups)
Ticket     — id, token (UUID), submitter_email, subject, body, status,
             internal_title, group_id FK → groups (nullable),
             github_pr_url, github_pr_title, created_at, updated_at
Assignment — ticket_id FK, employee_id FK          [one per ticket]
Message    — ticket_id FK, employee_id FK (null = customer reply),
             body (HTML), is_customer_visible, is_customer_reply,
             created_at, edited_at
TicketEvent  — ticket_id FK, employee_id FK (nullable), event_type, from_value,
               to_value, created_at
TicketWatch  — ticket_id FK, employee_id FK            [unique per pair]
Attachment   — ticket_id FK, message_id FK (nullable), filename (UUID on disk),
               original_filename, size, created_at
```

**Ticket statuses:** `open` → `in_progress` → `resolved` → `closed`

---

## Emails sent

| Trigger | Recipient |
|---|---|
| Ticket submitted | Submitter — confirmation + status link |
| Status changed | Submitter — new status; all watchers (except the employee who changed it) |
| Employee adds any message | All watchers (except the author) |
| Employee adds customer-visible message | Submitter — message content |
| Customer replies on status page | Assignee (or all active employees if unassigned); all watchers |
| Manager creates customer account | Customer — welcome email with credentials |

---

## Backups

`backup.sh` in the project root creates a timestamped database backup and a compressed uploads archive, then prunes files older than the configured retention period.

```bash
# Test manually first
/opt/taskify/backup.sh -d /var/backups/taskify -v

# Check exit code — 0 = success, 1 = something failed
echo $?
```

### Cron setup

```bash
# Add to crontab (run 'crontab -e' as the taskify user or root)
0 2 * * * /opt/taskify/backup.sh -d /var/backups/taskify >> /var/log/taskify/backup.log 2>&1
```

Cron only sends mail when the script exits with a non-zero code, so failures are reported automatically.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-d DIR` | `./backups` | Destination directory |
| `-k DAYS` | `14` | Days of backups to keep |
| `-v` | off | Verbose output |

### What gets backed up

| File | Description |
|------|-------------|
| `taskify-db-<timestamp>.sqlite3` | Hot copy of the database — safe to create while the app is running |
| `taskify-uploads-<timestamp>.tar.gz` | All ticket attachments |

The database path is read from the `DATABASE_URL` environment variable if set; otherwise defaults to `instance/taskify.db`. The uploads path is read from `UPLOAD_FOLDER`, defaulting to `uploads/`.

### Restoring

Use `restore.sh`. Run it without arguments for an interactive menu:

```bash
/opt/taskify/restore.sh -d /var/backups/taskify
```

```
Available backups (newest first):

   1)  2026-03-05T0200   db 1.2M, uploads 4.8M
   2)  2026-03-04T0200   db 1.1M, uploads 4.7M

Enter number to restore (or q to quit): 1
```

Before overwriting anything, the script saves a **safety snapshot** of the current database and uploads to a `pre-restore-<timestamp>/` folder inside the backup directory, so you can roll back the rollback if needed.

```bash
# Restore latest backup non-interactively
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -y

# Preview what would happen without changing anything
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -n

# Restore without managing the systemd service (e.g. non-systemd host)
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -s ''
```

| Flag | Default | Description |
|------|---------|-------------|
| `-d DIR` | `./backups` | Directory containing backup files |
| `-t TIMESTAMP` | interactive | Timestamp to restore, or `latest` |
| `-s SERVICE` | `taskify` | systemd service to stop/start; `''` to skip |
| `-y` | off | Skip confirmation prompt |
| `-n` | off | Dry run — show what would happen |

---

## Migrating an existing database

If you have an existing `instance/taskify.db`, run the following SQL once before restarting the app. A fresh install handles all of this via `db.create_all()` automatically.

```sql
-- Added in the initial multi-feature release
ALTER TABLE employees ADD COLUMN is_manager    BOOLEAN      NOT NULL DEFAULT 0;
ALTER TABLE employees ADD COLUMN github_id     VARCHAR(50);
ALTER TABLE employees ADD COLUMN github_login  VARCHAR(100);
ALTER TABLE tickets   ADD COLUMN github_pr_url VARCHAR(500);

CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         VARCHAR(120) UNIQUE NOT NULL,
    name          VARCHAR(120) NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT 1,
    created_by_id INTEGER REFERENCES employees(id),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Added later: GitHub issue title cache and user preferences
ALTER TABLE tickets   ADD COLUMN github_pr_title VARCHAR(500);
ALTER TABLE employees ADD COLUMN preferences      TEXT;

-- Added later: ticket activity / audit log
CREATE TABLE IF NOT EXISTS ticket_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    employee_id INTEGER REFERENCES employees(id),
    event_type  VARCHAR(50)  NOT NULL,
    from_value  VARCHAR(500),
    to_value    VARCHAR(500),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Added later: company field for customers (superseded by many-to-many below)
ALTER TABLE customers ADD COLUMN company VARCHAR(120);

-- Added later: multi-company support
CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(120) UNIQUE NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customer_companies (
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    PRIMARY KEY (customer_id, company_id)
);
-- Migrate existing single-company data:
INSERT OR IGNORE INTO companies (name)
    SELECT DISTINCT company FROM customers WHERE company IS NOT NULL AND company != '';
INSERT OR IGNORE INTO customer_companies (customer_id, company_id)
    SELECT c.id, co.id FROM customers c
    JOIN companies co ON co.name = c.company
    WHERE c.company IS NOT NULL AND c.company != '';
-- The old company column can be dropped once data is verified:
-- ALTER TABLE customers DROP COLUMN company;

-- Added later: employee-assigned internal title for tickets
ALTER TABLE tickets ADD COLUMN internal_title VARCHAR(200);

-- Added later: ticket watch subscriptions
CREATE TABLE IF NOT EXISTS ticket_watches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticket_id, employee_id)
);

-- Added later: projects (stored as "groups" internally) and per-ticket project assignment
CREATE TABLE IF NOT EXISTS groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(120) UNIQUE NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customer_groups (
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    group_id    INTEGER NOT NULL REFERENCES groups(id),
    PRIMARY KEY (customer_id, group_id)
);
ALTER TABLE tickets ADD COLUMN group_id INTEGER REFERENCES groups(id);

-- Added later: ticket locale (for submitter emails in their language)
ALTER TABLE tickets ADD COLUMN locale VARCHAR(10) NOT NULL DEFAULT 'en';

-- Added later: MantisBT import tracking (for revert support)
ALTER TABLE employees ADD COLUMN mantis_imported BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE customers ADD COLUMN mantis_imported BOOLEAN NOT NULL DEFAULT 0;
```

---

## Deploying to Production

### 1 — Prepare the server

A Linux server (Ubuntu/Debian) with Python 3.10+ is recommended. The steps below use **nginx + gunicorn** and assume the app lives at `/opt/taskify`.

```bash
# Install system packages
sudo apt update && sudo apt install -y python3-venv python3-pip nginx

# Create app directory and user
sudo useradd -r -s /bin/false taskify
sudo mkdir -p /opt/taskify
sudo chown taskify:taskify /opt/taskify

# Clone / copy files
cd /opt/taskify
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn        # production WSGI server
```

### 2 — Production `.env`

```dotenv
SECRET_KEY=replace-with-64-random-chars   # openssl rand -hex 32

DATABASE_URL=sqlite:////opt/taskify/instance/taskify.db

PUBLIC_TICKETS=true   # or false for internal-only use

MAIL_SERVER=email-smtp.us-east-1.amazonaws.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your_smtp_user
MAIL_PASSWORD=your_smtp_password
MAIL_DEFAULT_SENDER=support@yourdomain.com

BABEL_DEFAULT_LOCALE=en

GITHUB_CLIENT_ID=your_oauth_app_client_id
GITHUB_CLIENT_SECRET=your_oauth_app_client_secret
GITHUB_ORG=your-org-name
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxx
```

> **AWS SES:** Sandbox mode only delivers to verified addresses. Request production access under **SES → Account dashboard → Request production access**. Generate SMTP credentials under **SES → SMTP settings**.

### 3 — systemd service

Create `/etc/systemd/system/taskify.service`:

```ini
[Unit]
Description=Taskify support ticket system
After=network.target

[Service]
User=taskify
Group=taskify
WorkingDirectory=/opt/taskify
EnvironmentFile=/opt/taskify/.env
ExecStart=/opt/taskify/.venv/bin/gunicorn \
    --workers 2 \
    --bind 127.0.0.1:8000 \
    --access-logfile /var/log/taskify/access.log \
    --error-logfile  /var/log/taskify/error.log \
    app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/taskify
sudo chown taskify:taskify /var/log/taskify
sudo systemctl daemon-reload
sudo systemctl enable --now taskify
```

### 4 — nginx reverse proxy

Create `/etc/nginx/sites-available/taskify`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    client_max_body_size 20M;   # match MAX_CONTENT_LENGTH in config.py

    # Block direct access to uploaded files — the app serves them with auth checks
    location /uploads/ { deny all; }

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/taskify /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Obtain a free TLS certificate with Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

### 5 — Alternative: Caddy (automatic HTTPS)

If you prefer Caddy over nginx + Certbot, create a `Caddyfile` in `/opt/taskify`:

```caddy
yourdomain.com {
    reverse_proxy 127.0.0.1:8000

    # Block direct access to upload directory
    @uploads path /uploads/*
    respond @uploads 403
}
```

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
sudo systemctl enable --now caddy
```

### 6 — Initialize / migrate the database

On first deploy:

```bash
cd /opt/taskify
source .venv/bin/activate
python - <<'EOF'
from app import app
from models import db
with app.app_context():
    db.create_all()
EOF
```

On updates with schema changes, run the SQL from the **Migrating an existing database** section above against your production database:

```bash
sqlite3 /opt/taskify/instance/taskify.db < migration.sql
```

Then restart the service:

```bash
sudo systemctl restart taskify
```

### 7 — Update the GitHub OAuth callback URL

After going to production, update the callback URL in your GitHub OAuth App settings:

**github.com → Settings → Developer settings → OAuth Apps → Taskify → Edit**

Change the Authorization callback URL from `http://localhost:5000/auth/github/callback` to `https://yourdomain.com/auth/github/callback`.

### 8 — Production checklist

- [ ] `SECRET_KEY` is a long random string (not the dev default)
- [ ] Real SMTP credentials configured and tested
- [ ] HTTPS enabled with a valid certificate
- [ ] `nginx` or `Caddy` blocks direct access to `/uploads/`
- [ ] `gunicorn` running as a non-root user via systemd
- [ ] GitHub OAuth callback URL updated to the production domain
- [ ] `backup.sh` scheduled in cron (see [Backups](#backups) section below)
- [ ] `PUBLIC_TICKETS` set appropriately for your use case
- [ ] Fine-grained GitHub PAT approved by org admin (if `GITHUB_ORG` is set)
