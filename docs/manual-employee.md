# Taskify – Employee Guide

## Table of Contents

1. [Logging In](#1-logging-in)
2. [The Dashboard](#2-the-dashboard)
3. [Opening a Ticket](#3-opening-a-ticket)
4. [Replying and Adding Notes](#4-replying-and-adding-notes)
5. [Changing Status](#5-changing-status)
6. [Assigning Tickets](#6-assigning-tickets)
7. [Watching Tickets](#7-watching-tickets)
8. [Searching](#8-searching)
9. [Email Notifications](#9-email-notifications)
10. [Language](#10-language)

---

## 1. Logging In

Go to `/login` and enter your email address and password.

If your account is linked to a GitHub profile, you can also click **Login with GitHub**.

---

## 2. The Dashboard

The dashboard shows all tickets in the system.

### Views

| Tab | Shows |
|-----|-------|
| **All** | Every ticket |
| **Mine** | Tickets assigned to you |
| **Watched** | Tickets you are watching |

The badge on each tab shows how many active tickets it contains.

### Summary cards

The four cards at the top count tickets in the current view. Click a card to filter by that status instantly.

| Card | Meaning |
|------|---------|
| Open | Not yet being worked on |
| In Progress | Actively being handled |
| Unassigned | No assignee yet (hidden on the *Mine* tab) |
| Resolved this week | Resolved or closed in the last 7 days |

### Filters

- **Search box** — searches subject, description, messages, and submitter email.
- **Status dropdown** — show only one status at a time.
- **Group dropdown** — filter by customer group (appears when groups exist).
- **Clear** — removes all active filters.

Closed tickets are hidden by default. Click *Closed hidden* to show them.

### Reply pending badge

A yellow badge on a ticket row means the customer has replied and is waiting for a response from your team.

---

## 3. Opening a Ticket

Click the ticket subject or ID to open the detail view.

**Message colour coding:**

| Border colour | Meaning |
|---------------|---------|
| Orange | Customer message |
| Green | Staff reply visible to the customer |
| Grey | Internal note — staff only |

The **sidebar** on the right contains all actions: status, assignee, watching, internal title, and ticket info.

The **activity log** at the bottom of the sidebar records every status change, assignment, and file upload.

---

## 4. Replying and Adding Notes

1. Type in the editor at the bottom of the ticket.
2. Optionally attach a file using the file upload field.
3. Check **Visible to customer (sends email)** to send the message to the submitter.
   Leave it unchecked to write an internal note that only staff can see.
4. Click **Send**.

You can edit your own messages after sending using the pencil icon.

---

## 5. Changing Status

Use the *Change Status* panel in the sidebar.

| Status | Meaning |
|--------|---------|
| Open | Received — not yet being worked on |
| In Progress | Actively being handled |
| Resolved | Fixed — awaiting confirmation from submitter |
| Closed | Fully closed, no further action expected |

Setting status to *Resolved* or *Closed* sends an update email to the submitter.

---

## 6. Assigning Tickets

Use the *Assignee* panel in the sidebar. Select an employee and click **Assign**. The assigned employee receives an email notification. Select *Unassigned* to remove the current assignee.

---

## 7. Watching Tickets

Click **Watch** in the sidebar to receive email updates for tickets not assigned to you. Click **Unwatch** to stop receiving them. The button is hidden when you are already the assignee, since you receive all notifications automatically.

---

## 8. Searching

Click the magnifier icon in the navigation bar or go to `/search`.

You can filter by any combination of: keyword, status, assignee, date range, and group. Results are paginated, and the URL reflects all active filters — bookmark or share it freely.

---

## 9. Email Notifications

| Event | Who receives an email |
|-------|-----------------------|
| Ticket assigned to you | You |
| Customer replies | Assignee (or all active employees if unassigned) |
| Watched ticket updated | You |
| Status changed / message sent to customer | Submitter |

---

## 10. Language

Click the language button in the top navigation bar to switch between **English** and **German**. The preference is stored per browser session.
