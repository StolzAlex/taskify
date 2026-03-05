# Taskify – Employee & Admin Manual

## Table of Contents
1. [Logging In](#1-logging-in)
2. [Dashboard](#2-dashboard)
3. [Working with Tickets](#3-working-with-tickets)
4. [GitHub Integration](#4-github-integration)
5. [Search](#5-search)
6. [Email Notifications](#6-email-notifications)
7. [Admin: Employee Management](#7-admin-employee-management)
8. [Manager: Customer Management](#8-manager-customer-management)
9. [Language](#9-language)
10. [Emergency: Lost Admin Password](#10-emergency-lost-admin-password)

---

## 1. Logging In

Go to `/login`.

- **Username + password** — enter your credentials directly.
- **GitHub** — click *Login with GitHub* if your account is linked to a GitHub profile (configured by an admin).

After login you are taken to the dashboard.

---

## 2. Dashboard

The dashboard is the main ticket overview. It is divided into a ticket list (left) and a recent-activity feed (right).

### Views (tabs)

| Tab | Shows |
|-----|-------|
| **All** | Every ticket in the system |
| **Mine** | Tickets assigned to you |
| **Watched** | Tickets you are watching |

The badge next to *Mine* and *Watched* shows the count of active tickets in that view.

### Stat cards

Four cards at the top summarise the tickets **in the current view**:

| Card | Meaning |
|------|---------|
| Open | Tickets with status *Open* |
| In Progress | Tickets with status *In Progress* |
| Unassigned | Open/in-progress tickets with no assignee (hidden on the *Mine* tab) |
| Resolved this week | Tickets resolved or closed in the last 7 days |

Click a card to apply the corresponding status filter instantly.

### Filters

- **Search box** — matches subject, body, message content, and submitter e-mail.
- **Status dropdown** — filter by a single status.
- **Group dropdown** — show only tickets from customers belonging to a specific group (only shown when groups exist).
- **Clear** button — resets all filters.
- *Closed hidden* link — closed tickets are hidden by default; click to show them.

### Awaiting Reply badge

A yellow *Reply pending* badge on a ticket row means the customer has replied and is waiting for a response from support.

---

## 3. Working with Tickets

### Opening a ticket

Click the ticket subject or ID to open the detail view.

### Ticket detail layout

- **Top card** — subject, internal title (if set), status badge, submitter e-mail, creation time, and public token.
- **Messages** — chronological list of all messages:
  - Orange left border = customer reply
  - Green left border = staff reply visible to the customer
  - Grey left border = internal note (not visible to the customer)
- **Activity log** — collapsible log of all status changes, assignments, GitHub links, and file attachments.
- **Sidebar** — action panels (status, assignee, watch, internal title, GitHub, ticket info).

### Adding a message

1. Type in the rich text editor at the bottom of the left column.
2. Optionally attach a file.
3. Check **Visible to customer (sends email)** to send the message to the submitter.
   Leave unchecked for internal notes only visible to staff.
4. Click **Send**.

Employees can edit their own messages after sending by clicking the pencil icon.

### Changing status

Use the *Change Status* panel in the sidebar. Available statuses:

| Status | Meaning |
|--------|---------|
| Open | Newly received, not yet being worked on |
| In Progress | Actively being handled |
| Resolved | Issue fixed, awaiting confirmation |
| Closed | Fully closed — no further action expected |

Every status change sends an update e-mail to the submitter.

### Assigning a ticket

Select an employee in the *Assignee* panel and click **Assign**.
The assigned employee receives an e-mail notification.
Set to *Unassigned* to remove the current assignee.

### Watching a ticket

Click **Watch** in the sidebar to receive e-mail updates for tickets that are not assigned to you.
Click **Unwatch** to stop. The Watch button is hidden when you are the assignee (you already receive all notifications).

### Internal title

A short, private label for the ticket — visible only to staff.
It is also used as the title when creating a GitHub issue from the ticket.

---

## 4. GitHub Integration

All GitHub features are in the **GitHub** card in the sidebar.

### Sync status from GitHub

Toggle switch (default: **off**). When enabled, opening the ticket automatically checks the linked GitHub item and updates the ticket status:

| GitHub state | Ticket status set to |
|---|---|
| Issue closed | Closed |
| PR merged | Resolved |
| PR closed (not merged) | Closed |
| Reopened | Open |

### Linking an existing PR or Issue

Type in the *Link PR / Issue* search box (minimum 2 characters). Results show PRs and issues from your organisation.
Use `repo: <query>` to search within a specific repository (e.g. `myrepo: login bug`).
Click a result to link it. Click the **×** button next to the current link to unlink.

### Creating a new GitHub Issue

Available when `GITHUB_TOKEN` and `GITHUB_ORG` are configured.

1. Select a repository from the dropdown.
2. Click **Create Issue**.

The issue is created with the *Internal Title* (or subject) as the title and is automatically linked to the ticket.
If the assignee has a GitHub login configured, they are also assigned on GitHub.

---

## 5. Search

Go to `/search` (magnifier icon in the navbar).

Filter by any combination of:
- Free-text query (subject, body, messages, submitter e-mail)
- Status
- Assignee
- Date range (created from / until)
- Group

Results are paginated. All filter values are preserved in the URL and can be bookmarked or shared.

---

## 6. Email Notifications

| Event | Recipient |
|-------|-----------|
| Ticket submitted | Submitter (confirmation) |
| Ticket assigned | Assigned employee |
| Customer replies | Assignee (or all active employees if unassigned) |
| Status updated / message sent to customer | Submitter |
| Watched ticket updated | Watchers (except the assignee) |

Confirmation e-mails to the submitter are sent **in the submitter's interface language** (the language they had selected when submitting the ticket).

> **Development mode** — if `MAIL_SUPPRESS_SEND=true` (the default), e-mails are logged but not actually sent. A flash warning is shown instead.

---

## 7. Admin: Employee Management

Available to admins at **Admin → Employees**.

### Creating an employee

Fill in username, e-mail, password, and optionally assign the *Admin* or *Manager* role.

Password requirements: minimum 12 characters, including uppercase, lowercase, digit, and special character.

### Roles

| Role | Capabilities |
|------|--------------|
| Employee | View and work on tickets |
| Manager | + Manage customers and groups |
| Admin | + Manage employees, access all admin settings |

### Deactivating / reactivating

Use the toggle button in the employee row. Deactivated employees cannot log in.

### Linking a GitHub account

Enter the employee's GitHub username in the *Link GitHub* field.
Taskify resolves the username to a GitHub user ID via the API. Once linked, the employee can sign in via GitHub OAuth.

---

## 8. Manager: Customer Management

Available to managers and admins at **Manager → Customers**.

### Creating a customer

Fill in name, e-mail, password, and optionally assign one or more groups.
A welcome e-mail with the login credentials is sent automatically.

### Groups

Groups allow you to filter tickets by customer segment on the dashboard and in search.
A group is created automatically when you type a new name in the *Add new group* field.
Multiple groups per customer are allowed.

### Editing a customer

Click the pencil icon. You can update the name, e-mail, groups, and password.
Leave the password field blank to keep the current password.

### Deactivating / deleting

- **Deactivate** — the customer cannot log in but their tickets are preserved.
- **Delete** — permanently removes the customer account. Existing tickets are kept but the customer account link is removed.

---

## 9. Language

Click the language button in the top navigation bar to switch between **English** and **German**.
The language is stored per browser session.

---

## 10. Emergency: Lost Admin Password

If you cannot log in as an admin, run this command **directly on the server**:

```bash
flask reset-admin
```

This resets the password of the first active admin account and prints the new password to the terminal.

To reset a specific admin account:

```bash
flask reset-admin --username alice
```

**The new password is displayed only once.** Log in immediately and change it via *Admin → Employees*.

> This command requires shell access to the server and cannot be triggered from the web interface.
