# Taskify – Admin Guide

Admins have full access to everything available to managers and employees, plus the capabilities described here.

The **Help** page lets you switch between all four manuals (Employee, Manager, Admin, Customer) using the button group in the top-right corner.

## Table of Contents

1. [Employee Management](#1-employee-management)
2. [Deleting Tickets](#2-deleting-tickets)
3. [GitHub Integration](#3-github-integration)
4. [MantisBT Sync](#4-mantisbt-sync)
5. [Mail Test](#5-mail-test)
6. [System Tests](#6-system-tests)
7. [Environment Variables](#7-environment-variables)
8. [Inbound Email](#8-inbound-email)
9. [Emergency Procedures](#9-emergency-procedures)


---

## 1. Employee Management

Go to **Admin → Employees**.

### Creating an employee

Fill in **Name**, **Email**, and **Password**, then tick *Admin privileges* or *Manager privileges* if needed. Click **Create**. The employee can log in immediately.

### Roles

| Role | Can do |
|------|--------|
| Staff | Handle tickets |
| Manager | Handle tickets + manage customers and projects + edit staff |
| Admin | Full access including employee management and system configuration |

### Editing

Click the pencil icon to change an employee's name, email, or password.

- Admins can edit any employee except themselves.
- Managers can edit staff-level employees only.
- Leave the password field blank to keep the current password.
- To change a role, use the same edit form and tick or untick *Admin privileges* / *Manager privileges*. You cannot change your own role. Removing the last active admin's role is blocked.

### Deactivating / reactivating

Click **Deactivate** to block a login without deleting the account. The employee's history and ticket assignments are preserved. Click **Activate** to restore access.

An admin cannot deactivate their own account or another admin's account.

### Deleting

Click the trash icon to permanently delete an employee. This cannot be undone. The employee must be deactivated first; you cannot delete the currently logged-in account.

---

## 2. Deleting Tickets

Open the ticket and scroll to the bottom of the sidebar. Click **Delete Ticket** and confirm the prompt.

Deletion permanently removes:
- All messages and internal notes
- All file attachments (from the database and from disk)
- Status history and audit events
- Assignment and watch subscriptions

This action cannot be undone. The submitter is not notified.

---

## 3. GitHub Integration

Employees can log in with **Login with GitHub** if their GitHub account is linked.

### Linking

On the Employees page, enter the employee's GitHub username in the *GitHub* column and click **Link**. The app calls the GitHub API to verify the username exists and stores the login name.

### Unlinking

Click **✕** next to the linked username. The employee's password login is unaffected.

### Required configuration

Set the following environment variables (see [Environment Variables](#7-environment-variables)):

| Variable | Description |
|----------|-------------|
| `GITHUB_CLIENT_ID` | OAuth App client ID from GitHub |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret |

Create the OAuth App at *GitHub → Settings → Developer settings → OAuth Apps*. Set the **Authorization callback URL** to `https://<your-domain>/github/callback`.

---

## 4. MantisBT Sync

Go to **Admin → MantisBT Sync** to import data from an existing MantisBT installation.

### How it works

1. Enter the **MySQL/MariaDB connection details** for your MantisBT database and click **Load Preview**.
2. The preview panel shows three tabs — **Projects**, **Users**, and **Tickets** — each with a filter input and checkboxes.
3. Select what to import and click **Start Test Run** (dry run is on by default) to verify what would happen without saving anything.
4. Uncheck **Test Run** and click **Start Sync** to apply the changes.

### Role mapping

MantisBT access levels are mapped to Taskify roles as follows:

| MantisBT level | Taskify role |
|----------------|--------------|
| Viewer / Reporter / Updater | Customer |
| Developer | Staff employee |
| Project manager | Manager employee |

### Project handling

- MantisBT projects are imported as Taskify **Projects**.
- Projects that already exist in Taskify (matched by name) are shown with a *Already exists* badge and pre-unchecked — only new projects are selected by default.
- Customers are assigned to newly created projects based on their MantisBT project memberships. Memberships for pre-existing projects are left unchanged.

### Ticket mapping

| MantisBT status | Taskify status |
|-----------------|----------------|
| New / Feedback / Acknowledged / Confirmed | Open |
| Assigned | In Progress |
| Resolved | Resolved |
| Closed | Closed |

Tickets already imported (detected by the `[mantis:ID]` tag in the internal title) are skipped.

### Dry run

The **Test Run** checkbox is on by default. In test run mode all changes are calculated and counted, but nothing is saved to the database and no setup emails are sent to new users. The result flash message shows what *would* have happened.

### Required configuration

The MantisBT database connection can be pre-filled via environment variables:

| Variable | Description |
|----------|-------------|
| `MANTIS_DB_HOST` | MySQL/MariaDB hostname |
| `MANTIS_DB_PORT` | Port (default `3306`) |
| `MANTIS_DB_NAME` | Database name (default `bugtracker`) |
| `MANTIS_DB_USER` | Database username |
| `MANTIS_DB_PASS` | Database password |
| `MANTIS_TABLE_PREFIX` | Table prefix (default `mantis_`) |

---

## 5. Mail Test

Go to **Admin → Mail Test** to send a test email to any address. Use this to verify your SMTP configuration is working before relying on notifications.

The result shows whether the message was accepted by the server. Check the target inbox (and spam folder) to confirm delivery.

---

## 6. System Tests

Go to **Admin → System Tests** to run a full health check of the application.

Tests are grouped into two categories:

**Infrastructure checks** — read-only, safe to run at any time:
- Database connectivity
- Configuration completeness (secret key, upload folder, app name, public ticket mode)
- Email configuration and SMTP connectivity
- Inbound email configuration and thread health
- GitHub OAuth configuration and API reachability

**Functional tests** — create and immediately delete real records in the database:
- Ticket CRUD, status transitions, replies, assignment, watching
- Employee creation, password change, activation toggle
- Customer creation, group membership, activation toggle

Functional test records use the reserved domain `@taskify-test.invalid` and are purged at the start of each run, so leftover records from aborted runs are cleaned up automatically.

Results show **Pass**, **Fail**, **Warn**, or **Info**. Click the chevron on any row to expand the step-by-step detail log.

---

## 7. Environment Variables

All configuration is done via environment variables (or a `.env` file in the project root).

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | insecure default | Flask session signing key — **must be changed in production** |
| `DATABASE_URL` | `sqlite:///taskify.db` | SQLAlchemy connection string |
| `UPLOAD_FOLDER` | `uploads/` | Directory for ticket attachments |
| `APP_NAME` | `Taskify` | Displayed in the UI and emails |
| `PUBLIC_TICKETS` | `false` | Set to `true` to allow anonymous ticket submission |
| `MAIL_SERVER` | — | SMTP hostname |
| `MAIL_PORT` | `587` | SMTP port |
| `MAIL_USE_TLS` | `true` | Enable STARTTLS |
| `MAIL_USE_SSL` | `false` | Enable SMTP_SSL (alternative to TLS) |
| `MAIL_USERNAME` | — | SMTP authentication username |
| `MAIL_PASSWORD` | — | SMTP authentication password |
| `MAIL_DEFAULT_SENDER` | — | From address for outgoing emails |
| `MAIL_SUPPRESS_SEND` | `true` | Set to `false` to enable real email sending |
| `GITHUB_CLIENT_ID` | — | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | — | GitHub OAuth App client secret |
| `IMAP_HOST` | — | IMAP server for inbound email |
| `IMAP_PORT` | `993` | IMAP port |
| `IMAP_USER` | — | IMAP login username |
| `IMAP_PASSWORD` | — | IMAP login password |
| `IMAP_FOLDER` | `INBOX` | Mailbox folder to poll |
| `IMAP_INTERVAL` | `60` | Poll interval in seconds |

After changing environment variables, restart the application for changes to take effect.

---

## 8. Inbound Email

When configured, Taskify polls an IMAP mailbox and routes replies back to the correct ticket automatically.

### How it works

1. Every outgoing email to a submitter includes a reply-to address containing the ticket token.
2. The background thread polls the IMAP mailbox every `IMAP_INTERVAL` seconds.
3. Matching replies are attached to the ticket as customer messages.
4. Non-matching emails are left in the mailbox (not deleted).

### Setup

1. Create a dedicated mailbox for Taskify (e.g. `support@example.com`).
2. Set the IMAP environment variables listed above.
3. Point `MAIL_DEFAULT_SENDER` to the same address so replies arrive in the right mailbox.
4. Restart the app. The System Tests page will show whether the IMAP thread is running and can authenticate.

### Thread health

The inbound email thread is shown on the **System Tests** page under *Inbound email*. If it shows *not running*, check IMAP credentials and restart the app.

---

## 9. Emergency Procedures

### Admin locked out / forgotten password

If no admin can log in, reset a password directly in the database:

```bash
python - <<'EOF'
from app import app, db
from models import Employee
from werkzeug.security import generate_password_hash

with app.app_context():
    emp = Employee.query.filter_by(email='admin@example.com').first()
    emp.password_hash = generate_password_hash('NewSecurePass1!')
    db.session.commit()
    print('Done')
EOF
```

Replace the email and password as needed.

### Resetting a customer password

Open the customer edit form on the **Customers** page (Admin or Manager), enter a new password, and click **Save**. Leave the password field blank to keep the current password.

### Database and uploads backup

Use `backup.sh` in the project root. It creates a hot database backup (no downtime required) and a compressed archive of all uploaded attachments, then prunes files older than the configured retention period.

```bash
# Run manually
/opt/taskify/backup.sh -d /var/backups/taskify -v

# Add to cron (daily at 02:00)
0 2 * * * /opt/taskify/backup.sh -d /var/backups/taskify >> /var/log/taskify/backup.log 2>&1
```

Options: `-d DIR` destination, `-k DAYS` retention (default 14), `-v` verbose.

**Restoring:**

Run `restore.sh` without arguments for an interactive backup selection menu. Before overwriting anything it saves a safety snapshot of the current state.

```bash
# Interactive — pick from a numbered list
/opt/taskify/restore.sh -d /var/backups/taskify

# Restore latest backup without prompts
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -y

# Dry run to preview without making changes
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -n
```

### Clearing all attachments

Uploaded files are stored in `UPLOAD_FOLDER/<ticket_id>/`. Deleting a ticket via the admin interface removes the upload folder automatically. To reclaim space from any orphaned folders left by other means, delete them manually after confirming the corresponding tickets no longer exist.
