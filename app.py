import email as _email_lib
import email.utils
import imaplib
import os
import click
import markdown as _md_lib
from markdown.extensions.toc import TocExtension as _TocExtension
import re
import secrets
import string
import threading
import time
import uuid
from datetime import datetime, timedelta
from email.header import decode_header as _decode_header
from functools import wraps
from urllib.parse import urlparse, urljoin

from dotenv import load_dotenv
load_dotenv()

import requests as http_requests
from authlib.integrations.flask_client import OAuth
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, send_from_directory, session, jsonify)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message as MailMessage
from flask_babel import Babel, gettext as _, lazy_gettext as _l, get_locale, force_locale
from markupsafe import escape, Markup
from werkzeug.utils import secure_filename

from config import Config
from models import db, Employee, Customer, Group, Ticket, Assignment, Message, Attachment, TicketEvent, TicketWatch

app = Flask(__name__)
app.config.from_object(Config)

# Warn loudly if the dev SECRET_KEY slips into production.
if not app.debug and app.config['SECRET_KEY'] == 'dev-secret-change-in-production':
    import warnings
    warnings.warn(
        'SECRET_KEY is set to the insecure default. Set a strong random value in production!',
        stacklevel=1,
    )

db.init_app(app)
mail = Mail(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # no blanket limit; apply selectively per route
    storage_uri='memory://',
)


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = _l('Please log in to access this page.')
login_manager.login_message_category = 'warning'


def get_locale_selector():
    return session.get('lang',
                       request.accept_languages.best_match(['en', 'de'], default='en'))


babel = Babel(app, locale_selector=get_locale_selector)

oauth = OAuth(app)
github_oauth = oauth.register(
    name='github',
    client_id=app.config['GITHUB_CLIENT_ID'],
    client_secret=app.config['GITHUB_CLIENT_SECRET'],
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'read:user user:email'},
)


# ---------------------------------------------------------------------------
# Status label helper (translatable)
# ---------------------------------------------------------------------------

def status_label(status):
    labels = {
        'open':        _('Open'),
        'in_progress': _('In Progress'),
        'resolved':    _('Resolved'),
        'closed':      _('Closed'),
    }
    return labels.get(status, status)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_next(fallback):
    """Return the ?next= URL only if it points to the same host (prevent open redirect)."""
    target = request.args.get('next') or ''
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    if target and test.scheme in ('http', 'https') and test.netloc == ref.netloc:
        return target
    return fallback


def _validate_password(password):
    """Return a translated error string if the password is too weak, else None."""
    if len(password) < 12:
        return _('Password must be at least 12 characters long.')
    if not re.search(r'[A-Z]', password):
        return _('Password must contain at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        return _('Password must contain at least one lowercase letter.')
    if not re.search(r'\d', password):
        return _('Password must contain at least one digit.')
    if not re.search(r'[^A-Za-z0-9]', password):
        return _('Password must contain at least one special character.')
    return None


def get_current_customer():
    cid = session.get('customer_id')
    return db.session.get(Customer, cid) if cid else None


def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('customer_id'):
            flash(_('Please log in to your customer account.'), 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def manager_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not (current_user.is_admin or current_user.is_manager):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def log_event(ticket, event_type, from_value=None, to_value=None, actor_id=None):
    db.session.add(TicketEvent(
        ticket_id=ticket.id,
        employee_id=actor_id,
        event_type=event_type,
        from_value=str(from_value) if from_value is not None else None,
        to_value=str(to_value) if to_value is not None else None,
    ))


def github_ref_label(url):
    """Convert https://github.com/owner/repo/pull/123 → owner/repo#123"""
    if not url:
        return ''
    m = re.match(r'https://github\.com/([^/]+/[^/]+)/(pull|issues)/(\d+)', url)
    if m:
        return f'{m.group(1)} #{m.group(3)}'
    return url


@app.context_processor
def inject_globals():
    return {
        'now': datetime.utcnow(),
        'status_label': status_label,
        'get_locale': get_locale,
        'current_customer': get_current_customer(),
        'public_tickets': app.config.get('PUBLIC_TICKETS', True),
        'github_configured': bool(app.config.get('GITHUB_CLIENT_ID')),
        'github_token_configured': bool(app.config.get('GITHUB_TOKEN')),
        'github_org': app.config.get('GITHUB_ORG', ''),
        'github_ref_label': github_ref_label,
        'app_name': app.config.get('APP_NAME', 'Taskify'),
    }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Employee, int(user_id))


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def send_email(subject, recipients, body_text, body_html=None, silent=False):
    """Send an email and flash the result unless silent=True.

    silent=True is used for background notifications (watchers, customer-side
    triggers) where no employee UI is present to show the flash.
    """
    try:
        msg = MailMessage(subject=subject, recipients=recipients, body=body_text,
                          html=body_html)
        mail.send(msg)
        if app.config.get('MAIL_SUPPRESS_SEND') or app.config.get('TESTING'):
            app.logger.info(f'Email suppressed (MAIL_SUPPRESS_SEND) to {recipients}: {subject}')
            if not silent:
                flash(_('Email to %(addr)s was suppressed (MAIL_SUPPRESS_SEND is enabled).',
                        addr=', '.join(recipients)), 'warning')
            return False
        app.logger.info(f'Email sent to {recipients}: {subject}')
        if not silent:
            flash(_('Email sent to %(addr)s.', addr=', '.join(recipients)), 'info')
        return True
    except Exception as e:
        server = f"{app.config.get('MAIL_SERVER')}:{app.config.get('MAIL_PORT')}"
        app.logger.error(f'Email send failed (server={server}, suppress={app.config.get("MAIL_SUPPRESS_SEND")}): {e}')
        if not silent:
            flash(_('Email to %(addr)s could not be sent (%(error)s).',
                    addr=', '.join(recipients), error=str(e)), 'danger')
        return False


def notify_submitter_confirmation(ticket):
    status_url = url_for('ticket_status', token=ticket.token, _external=True)
    locale = getattr(ticket, 'locale', None) or 'en'
    with force_locale(locale):
        subj = _('Ticket #%(id)s received \u2013 %(subject)s',
                 id=ticket.id, subject=ticket.subject)
        keep_updated = _("We'll keep you updated by email.")
        body = (
            f"{_('Thank you for submitting your support request.')}\n\n"
            f"{_('Subject')}: {ticket.subject}\n"
            f"{_('Ticket ID')}: #{ticket.id}\n\n"
            f"{_('You can track your ticket status at:')}\n{status_url}\n\n"
            f"{keep_updated}"
        )
        send_email(
            subject=f"[{app.config['APP_NAME']}] {subj}",
            recipients=[ticket.submitter_email],
            body_text=body,
        )


def notify_submitter_update(ticket, extra_message=None):
    status_url = url_for('ticket_status', token=ticket.token, _external=True)
    locale = getattr(ticket, 'locale', None) or 'en'
    with force_locale(locale):
        subj = _('Ticket #%(id)s updated \u2013 %(subject)s',
                 id=ticket.id, subject=ticket.subject)
        body = (
            f"{_('Your support ticket has been updated.')}\n\n"
            f"{_('Subject')}: {ticket.subject}\n"
            f"{_('Status')}: {status_label(ticket.status)}\n\n"
        )
        if extra_message:
            body += f"{_('Message from support:')}\n{extra_message}\n\n"
        if ticket.status in ('resolved', 'closed'):
            body += _('This ticket is now closed. No further action is required on your part.')
        else:
            body += f"{_('View your ticket at:')}\n{status_url}"
        send_email(
            subject=f"[{app.config['APP_NAME']}] {subj}",
            recipients=[ticket.submitter_email],
            body_text=body,
        )


def notify_assignee_customer_reply(ticket):
    detail_url = url_for('ticket_detail', ticket_id=ticket.id, _external=True)
    body = (
        f"The customer has replied to ticket #{ticket.id}.\n\n"
        f"Subject: {ticket.subject}\n"
        f"From: {ticket.submitter_email}\n\n"
        f"View ticket: {detail_url}"
    )
    if ticket.assignee:
        recipients = [ticket.assignee.email]
    else:
        recipients = [e.email for e in Employee.query.filter_by(is_active=True).all()]
    if recipients:
        send_email(
            subject=f"[{app.config['APP_NAME']}] Customer replied – Ticket #{ticket.id}",
            recipients=recipients,
            body_text=body,
            silent=True,   # customer is the actor here — don't flash employee emails to them
        )


def notify_assignee_assigned(ticket, employee):
    detail_url = url_for('ticket_detail', ticket_id=ticket.id, _external=True)
    body = (
        f"You have been assigned to a support ticket.\n\n"
        f"Subject: {ticket.subject}\n"
        f"Ticket ID: #{ticket.id}\n"
        f"Submitted by: {ticket.submitter_email}\n"
        f"Status: {ticket.status.replace('_', ' ').title()}\n\n"
        f"View ticket: {detail_url}"
    )
    send_email(
        subject=f"[{app.config['APP_NAME']}] Assigned to you – Ticket #{ticket.id}: {ticket.subject}",
        recipients=[employee.email],
        body_text=body,
    )


def notify_watchers(ticket, subject, body, exclude_employee_id=None):
    assignee_id = ticket.assignment.employee_id if ticket.assignment else None
    excluded = {eid for eid in [exclude_employee_id, assignee_id] if eid}
    watches = TicketWatch.query.filter_by(ticket_id=ticket.id).all()
    for w in watches:
        if w.employee_id not in excluded and w.employee.is_active:
            send_email(subject=subject, recipients=[w.employee.email], body_text=body,
                       silent=True)   # one flash per watcher would be too noisy


def send_customer_welcome_email(customer, plain_password):
    login_url = url_for('customer_login', _external=True)
    body = (
        f"Welcome to {app.config['APP_NAME']}!\n\n"
        f"Your customer account has been created.\n\n"
        f"Email: {customer.email}\n"
        f"Password: {plain_password}\n\n"
        f"Login at: {login_url}\n\n"
        f"Please change your password after first login."
    )
    send_email(
        subject=f"{app.config['APP_NAME']} \u2013 Your Customer Account",
        recipients=[customer.email],
        body_text=body,
    )


def _make_setup_token(user):
    """Generate a 72-hour password-setup token, persist it, and return it."""
    token = secrets.token_urlsafe(32)
    user.setup_token = token
    user.setup_token_expires = datetime.utcnow() + timedelta(hours=72)
    db.session.commit()
    return token


def send_setup_email(user_email, user_name, setup_url):
    app_name = app.config['APP_NAME']
    body = (
        f"Hello {user_name},\n\n"
        f"An account has been created for you on {app_name}.\n\n"
        f"Please set your password by visiting the link below within 72 hours:\n"
        f"{setup_url}\n\n"
        f"If you did not expect this email, you can ignore it."
    )
    send_email(
        subject=f"{app_name} \u2013 {_('Set up your password')}",
        recipients=[user_email],
        body_text=body,
    )


# ---------------------------------------------------------------------------
# Language switcher
# ---------------------------------------------------------------------------

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in app.config['BABEL_SUPPORTED_LOCALES']:
        session['lang'] = lang
    return redirect(request.referrer or url_for('submit'))


# ---------------------------------------------------------------------------
# Password self-setup via email link
# ---------------------------------------------------------------------------

@app.route('/setup-password/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per hour')
def setup_password(token):
    user = (
        Customer.query.filter_by(setup_token=token).first()
        or Employee.query.filter_by(setup_token=token).first()
    )
    if not user or not user.setup_token_expires or user.setup_token_expires < datetime.utcnow():
        flash(_('This link is invalid or has expired.'), 'danger')
        return redirect(url_for('login'))
    is_employee = isinstance(user, Employee)
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        if password != confirm:
            flash(_('Passwords do not match.'), 'danger')
            return render_template('setup_password.html', user=user)
        pw_error = _validate_password(password)
        if pw_error:
            flash(pw_error, 'danger')
            return render_template('setup_password.html', user=user)
        user.set_password(password)
        user.setup_token = None
        user.setup_token_expires = None
        db.session.commit()
        flash(_('Password set! You can now log in.'), 'success')
        return redirect(url_for('login' if is_employee else 'customer_login'))
    return render_template('setup_password.html', user=user)


# ---------------------------------------------------------------------------
# Setup (first-run only)
# ---------------------------------------------------------------------------

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if Employee.query.count() > 0:
        flash(_('Setup already complete.'), 'info')
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not username or not email or not password:
            flash(_('All fields are required.'), 'danger')
            return render_template('setup.html')
        pw_error = _validate_password(password)
        if pw_error:
            flash(pw_error, 'danger')
            return render_template('setup.html')
        emp = Employee(username=username, email=email, is_admin=True, is_active=True)
        emp.set_password(password)
        db.session.add(emp)
        db.session.commit()
        flash(_('Admin account created. Please log in.'), 'success')
        return redirect(url_for('login'))
    return render_template('setup.html')


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET', 'POST'])
def submit():
    customer = get_current_customer()
    if not app.config['PUBLIC_TICKETS'] and not current_user.is_authenticated and not customer:
        return redirect(url_for('login'))
    if request.method == 'POST':
        if current_user.is_authenticated:
            email = current_user.email
        elif customer:
            email = customer.email
        else:
            email = request.form.get('email', '').strip()
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        if not email or not subject or not body:
            flash(_('All fields are required.'), 'danger')
            return render_template('submit.html', customer=customer)
        ticket = Ticket(submitter_email=email, subject=subject, body=body,
                        locale=session.get('lang', 'en'))
        db.session.add(ticket)
        db.session.commit()
        notify_submitter_confirmation(ticket)
        flash(
            _('Your ticket has been submitted! Track it at'
              ' <a href="%(url)s">/status/%(token)s\u2026</a>',
              url=url_for('ticket_status', token=ticket.token),
              token=ticket.token[:8]),
            'success'
        )
        return redirect(url_for('submit'))
    return render_template('submit.html', customer=customer)


@app.route('/status/<token>')
def ticket_status(token):
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    customer = get_current_customer()
    if not app.config['PUBLIC_TICKETS'] and not current_user.is_authenticated and not customer:
        flash(_('Please log in to view your ticket.'), 'warning')
        return redirect(url_for('login', next=request.url))
    if ticket.status in ('resolved', 'closed'):
        if not (customer and customer.email.lower() == ticket.submitter_email.lower()):
            return render_template('ticket_closed.html', ticket=ticket), 410
    thread = ticket.messages.filter(
        db.or_(Message.is_customer_visible == True, Message.is_customer_reply == True)
    ).all()
    return render_template('ticket_status.html', ticket=ticket, messages=thread,
                           current_customer=customer)


@app.route('/status/<token>/reply', methods=['POST'])
def customer_reply(token):
    customer = get_current_customer()
    if not app.config['PUBLIC_TICKETS'] and not current_user.is_authenticated and not customer:
        return redirect(url_for('login'))
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    body = request.form.get('body', '').strip()
    if not body or body == '<p><br></p>':
        flash(_('Reply cannot be empty.'), 'danger')
        return redirect(url_for('ticket_status', token=token))
    msg = Message(ticket_id=ticket.id, employee_id=None, body=body,
                  is_customer_visible=False, is_customer_reply=True)
    db.session.add(msg)
    ticket.updated_at = datetime.utcnow()
    log_event(ticket, 'customer_reply')
    db.session.commit()
    notify_assignee_customer_reply(ticket)
    notify_watchers(
        ticket,
        subject=f"[{app.config['APP_NAME']}] Kundenantwort \u2013 Ticket #{ticket.id}: {ticket.subject}",
        body=(f"Der Kunde hat auf Ticket #{ticket.id} geantwortet.\n\n"
              f"Betreff: {ticket.subject}\n\n"
              f"Ticket ansehen: {url_for('ticket_detail', ticket_id=ticket.id, _external=True)}"),
    )
    flash(_('Your reply has been sent.'), 'success')
    return redirect(url_for('ticket_status', token=token))


# ---------------------------------------------------------------------------
# Employee auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per minute')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if session.get('customer_id'):
        return redirect(url_for('customer_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        # Try employee by email
        emp = Employee.query.filter(Employee.email.ilike(email)).first()
        if emp and emp.is_active and emp.check_password(password):
            login_user(emp, remember=request.form.get('remember') == 'on')
            session.pop('customer_id', None)
            return redirect(_safe_next(url_for('dashboard')))
        # Try customer by email
        customer = Customer.query.filter(Customer.email.ilike(email)).first()
        if customer and customer.is_active and customer.check_password(password):
            session['customer_id'] = customer.id
            return redirect(_safe_next(url_for('customer_dashboard')))
        flash(_('Invalid credentials or account disabled.'), 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------

@app.route('/auth/github')
def auth_github():
    if not app.config.get('GITHUB_CLIENT_ID'):
        flash(_('GitHub OAuth is not configured.'), 'danger')
        return redirect(url_for('login'))
    callback_url = url_for('auth_github_callback', _external=True)
    return github_oauth.authorize_redirect(callback_url)


@app.route('/auth/github/callback')
def auth_github_callback():
    try:
        token = github_oauth.authorize_access_token()
    except Exception:
        flash(_('GitHub authentication failed.'), 'danger')
        return redirect(url_for('login'))

    access_token = token.get('access_token')
    resp = http_requests.get(
        'https://api.github.com/user',
        headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
        timeout=10,
    )
    if not resp.ok:
        flash(_('GitHub authentication failed.'), 'danger')
        return redirect(url_for('login'))

    profile = resp.json()
    github_id = str(profile.get('id', ''))

    # Sign in via GitHub (linking is managed by admins via /admin/employees)
    emp = Employee.query.filter_by(github_id=github_id).first()
    if emp and emp.is_active:
        login_user(emp)
        session.pop('customer_id', None)
        return redirect(url_for('dashboard'))
    flash(_('No employee account linked to this GitHub account.'), 'danger')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Customer auth + portal
# ---------------------------------------------------------------------------

@app.route('/customer/login')
def customer_login():
    return redirect(url_for('login', **request.args))


@app.route('/customer/logout')
def customer_logout():
    session.pop('customer_id', None)
    return redirect(url_for('submit'))


@app.route('/customer/dashboard')
@customer_required
def customer_dashboard():
    customer = get_current_customer()

    view          = request.args.get('view', 'all')
    if view not in ('all', 'awaiting', 'closed'):
        view = 'all'
    status_filter = request.args.get('status', '')
    q             = request.args.get('q', '').strip()

    base_query = Ticket.query.filter(Ticket.submitter_email.ilike(customer.email))

    # Tickets where the last customer-visible message is from support (not the customer)
    cust_ticket_ids_sq = db.session.query(Ticket.id).filter(
        Ticket.submitter_email.ilike(customer.email)
    )
    latest_msg_sq = (
        db.session.query(Message.ticket_id, db.func.max(Message.id).label('max_id'))
        .filter(db.or_(Message.is_customer_visible == True, Message.is_customer_reply == True))
        .group_by(Message.ticket_id)
        .subquery()
    )
    awaiting_reply_ids = {
        row[0] for row in
        db.session.query(Message.ticket_id)
        .join(latest_msg_sq, db.and_(
            Message.ticket_id == latest_msg_sq.c.ticket_id,
            Message.id == latest_msg_sq.c.max_id,
        ))
        .filter(
            Message.is_customer_reply == False,
            Message.ticket_id.in_(cust_ticket_ids_sq),
        )
        .all()
    }

    query = base_query
    hide_closed = False

    if view == 'awaiting':
        query = query.filter(Ticket.id.in_(awaiting_reply_ids))
    elif view == 'closed':
        query = query.filter(Ticket.status.in_(['resolved', 'closed']))
    else:
        if not status_filter:
            query = query.filter(Ticket.status.notin_(['resolved', 'closed']))
            hide_closed = True

    if status_filter:
        query = query.filter(Ticket.status == status_filter)

    if q:
        query = query.filter(db.or_(
            Ticket.subject.ilike(f'%{q}%'),
            Ticket.body.ilike(f'%{q}%'),
        ))

    page     = request.args.get('page', 1, type=int)
    per_page = 20
    pagination = query.order_by(Ticket.updated_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    tickets = pagination.items

    # Stats computed from the full unfiltered set
    all_tickets = base_query.all()
    stats = {
        'open':           sum(1 for t in all_tickets if t.status == 'open'),
        'in_progress':    sum(1 for t in all_tickets if t.status == 'in_progress'),
        'awaiting_reply': sum(1 for t in all_tickets
                              if t.id in awaiting_reply_ids and t.status not in ('resolved', 'closed')),
        'closed':         sum(1 for t in all_tickets if t.status in ('resolved', 'closed')),
    }

    # Per-ticket metadata for the displayed page only
    ticket_meta = {
        t.id: {
            'reply_count':    t.messages.filter_by(is_customer_visible=True).count(),
            'awaiting_reply': t.id in awaiting_reply_ids,
        }
        for t in tickets
    }

    all_ticket_ids = [t.id for t in all_tickets]
    recent_events = (TicketEvent.query
                     .filter(
                         TicketEvent.ticket_id.in_(all_ticket_ids),
                         TicketEvent.event_type.in_(['status', 'customer_reply', 'attachment', 'assignment'])
                     )
                     .order_by(TicketEvent.created_at.desc())
                     .limit(15).all()) if all_ticket_ids else []

    return render_template('customer/dashboard.html',
                           customer=customer,
                           tickets=tickets,
                           pagination=pagination, per_page=per_page,
                           ticket_meta=ticket_meta,
                           stats=stats,
                           recent_events=recent_events,
                           view=view,
                           status_filter=status_filter,
                           status_choices=Ticket.STATUS_CHOICES,
                           q=q,
                           hide_closed=hide_closed,
                           awaiting_reply_ids=awaiting_reply_ids)


@app.route('/customer/uploads/<int:ticket_id>/<filename>')
@customer_required
def serve_attachment_customer(ticket_id, filename):
    customer = get_current_customer()
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    if ticket.submitter_email.lower() != customer.email.lower():
        abort(403)
    att = Attachment.query.filter_by(ticket_id=ticket_id, filename=filename).first_or_404()
    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
    return send_from_directory(upload_dir, filename, download_name=att.original_filename)


# ---------------------------------------------------------------------------
# Manager routes
# ---------------------------------------------------------------------------

def _resolve_groups(group_ids, new_name):
    """Return a list of Group objects from selected IDs + optional new name."""
    selected = Group.query.filter(Group.id.in_(group_ids)).all() if group_ids else []
    if new_name:
        existing = Group.query.filter(Group.name.ilike(new_name)).first()
        if existing:
            if existing not in selected:
                selected.append(existing)
        else:
            grp = Group(name=new_name)
            db.session.add(grp)
            db.session.flush()
            selected.append(grp)
    return selected


@app.route('/manager/customers', methods=['GET', 'POST'])
@login_required
@manager_required
def manager_customers():
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        if not name or not email:
            flash(_('All fields are required.'), 'danger')
        elif Customer.query.filter(Customer.email.ilike(email)).first():
            flash(_('Email already in use.'), 'danger')
        else:
            customer = Customer(name=name, email=email, created_by_id=current_user.id)
            customer.set_password(secrets.token_hex(32))
            customer.groups = _resolve_groups(
                request.form.getlist('group_ids', type=int),
                request.form.get('new_group', '').strip(),
            )
            db.session.add(customer)
            db.session.commit()
            token = _make_setup_token(customer)
            setup_url = url_for('setup_password', token=token, _external=True)
            send_setup_email(customer.email, customer.name, setup_url)
            flash(_('A setup link has been sent to %(email)s.', email=email), 'success')
        return redirect(url_for('manager_customers'))
    customers  = Customer.query.order_by(Customer.created_at.desc()).all()
    all_groups = Group.query.order_by(Group.name).all()
    return render_template('manager/customers.html', customers=customers, all_groups=all_groups)


@app.route('/manager/customers/<int:cust_id>/toggle', methods=['POST'])
@login_required
@manager_required
def toggle_customer(cust_id):
    customer = db.session.get(Customer, cust_id) or abort(404)
    customer.is_active = not customer.is_active
    db.session.commit()
    if customer.is_active:
        flash(_('Customer "%(name)s" activated.', name=customer.name), 'success')
    else:
        flash(_('Customer "%(name)s" deactivated.', name=customer.name), 'success')
    return redirect(url_for('manager_customers'))


@app.route('/manager/customers/<int:cust_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_customer(cust_id):
    customer = db.session.get(Customer, cust_id) or abort(404)
    name = customer.name
    db.session.delete(customer)
    db.session.commit()
    flash(_('Customer "%(name)s" deleted.', name=name), 'success')
    return redirect(url_for('manager_customers'))


# ---------------------------------------------------------------------------
# Employee routes
# ---------------------------------------------------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    is_privileged = current_user.is_admin or current_user.is_manager
    default_view = 'all' if is_privileged else 'mine'
    if 'view' in request.args:
        view = request.args['view'] if request.args['view'] in ('mine', 'all', 'watched') else default_view
        if view != current_user.get_pref('dashboard_view'):
            current_user.set_pref('dashboard_view', view)
            db.session.commit()
    else:
        view = current_user.get_pref('dashboard_view', default_view)
    status_filter        = request.args.get('status', '')
    unassigned_filter    = request.args.get('unassigned', '') == '1'
    resolved_week_filter = request.args.get('resolved_week', '') == '1'
    group_filter         = request.args.get('group', '')
    q                    = request.args.get('q', '').strip()

    query = Ticket.query
    # Hide closed tickets by default unless explicitly requested
    hide_closed = not status_filter and not unassigned_filter and not resolved_week_filter
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    elif hide_closed:
        query = query.filter(Ticket.status != 'closed')
    if q:
        msg_ids = db.session.query(Message.ticket_id).filter(Message.body.ilike(f'%{q}%'))
        query = query.filter(db.or_(
            Ticket.subject.ilike(f'%{q}%'),
            Ticket.body.ilike(f'%{q}%'),
            Ticket.internal_title.ilike(f'%{q}%'),
            Ticket.submitter_email.ilike(f'%{q}%'),
            Ticket.id.in_(msg_ids),
        ))
    if unassigned_filter:
        query = query.filter(
            Ticket.status.in_(['open', 'in_progress']),
            ~Ticket.id.in_(db.session.query(Assignment.ticket_id))
        )
    if resolved_week_filter:
        week_ago_filter = datetime.utcnow() - timedelta(days=7)
        query = query.filter(
            Ticket.status.in_(['resolved', 'closed']),
            Ticket.updated_at >= week_ago_filter
        )
    if group_filter:
        grp = Group.query.filter_by(name=group_filter).first()
        if grp:
            grp_emails = [c.email.lower() for c in grp.customers]
            query = query.filter(db.func.lower(Ticket.submitter_email).in_(grp_emails))
    watched_ids = {w.ticket_id for w in TicketWatch.query.filter_by(employee_id=current_user.id).all()}

    # Tickets where the most recent message is a customer reply (needs a response)
    latest_msg_sq = (
        db.session.query(Message.ticket_id, db.func.max(Message.id).label('max_id'))
        .group_by(Message.ticket_id).subquery()
    )
    awaiting_reply_ids = {
        row[0] for row in
        db.session.query(Message.ticket_id)
        .join(latest_msg_sq, db.and_(
            Message.ticket_id == latest_msg_sq.c.ticket_id,
            Message.id == latest_msg_sq.c.max_id,
        ))
        .filter(Message.is_customer_reply == True)
        .all()
    }

    if view == 'mine':
        query = query.join(Assignment).filter(Assignment.employee_id == current_user.id)
    elif view == 'watched':
        query = query.filter(Ticket.id.in_(watched_ids))

    page     = request.args.get('page', 1, type=int)
    per_page = 25
    pagination = query.order_by(Ticket.updated_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    tickets = pagination.items

    # Build customer lookup for submitter column
    submitter_emails = [t.submitter_email.lower() for t in tickets]
    if submitter_emails:
        customer_map = {c.email.lower(): c for c in Customer.query.filter(
            db.func.lower(Customer.email).in_(submitter_emails)).all()}
    else:
        customer_map = {}

    # All groups for filter dropdown
    groups = Group.query.order_by(Group.name).all()

    if view == 'mine':
        my_ticket_ids = db.session.query(Assignment.ticket_id).filter(
            Assignment.employee_id == current_user.id
        )
        recent_events = (TicketEvent.query
                         .filter(TicketEvent.ticket_id.in_(my_ticket_ids))
                         .order_by(TicketEvent.created_at.desc())
                         .limit(20).all())
    elif view == 'watched':
        recent_events = (TicketEvent.query
                         .filter(TicketEvent.ticket_id.in_(watched_ids))
                         .order_by(TicketEvent.created_at.desc())
                         .limit(20).all())
    else:
        recent_events = (TicketEvent.query
                         .order_by(TicketEvent.created_at.desc())
                         .limit(20).all())

    week_ago = datetime.utcnow() - timedelta(days=7)
    active_statuses = ['open', 'in_progress']

    # Scope stat queries to the active tab so the cards reflect what the user sees.
    def _stats_base():
        if view == 'mine':
            return Ticket.query.join(Assignment).filter(Assignment.employee_id == current_user.id)
        if view == 'watched':
            return Ticket.query.filter(Ticket.id.in_(watched_ids))
        return Ticket.query

    stats = {
        'open':          _stats_base().filter(Ticket.status == 'open').count(),
        'in_progress':   _stats_base().filter(Ticket.status == 'in_progress').count(),
        # Unassigned is meaningless for 'mine' (those are by definition assigned)
        'unassigned':    _stats_base().filter(
                             Ticket.status.in_(active_statuses),
                             ~Ticket.id.in_(db.session.query(Assignment.ticket_id))
                         ).count() if view != 'mine' else 0,
        'resolved_week': _stats_base().filter(
                             Ticket.status.in_(['resolved', 'closed']),
                             Ticket.updated_at >= week_ago
                         ).count(),
        # Tab badge counts are always global so they don't zero out when active
        'mine':          Ticket.query.join(Assignment).filter(
                             Assignment.employee_id == current_user.id,
                             Ticket.status.in_(active_statuses)
                         ).count(),
        'watched':       TicketWatch.query.filter_by(employee_id=current_user.id).count(),
    }

    return render_template('dashboard.html', tickets=tickets,
                           pagination=pagination, per_page=per_page,
                           q=q,
                           status_filter=status_filter,
                           unassigned_filter=unassigned_filter,
                           resolved_week_filter=resolved_week_filter,
                           group_filter=group_filter,
                           groups=groups,
                           customer_map=customer_map,
                           view=view, is_privileged=is_privileged,
                           status_choices=Ticket.STATUS_CHOICES,
                           recent_events=recent_events,
                           stats=stats,
                           watched_ids=watched_ids,
                           awaiting_reply_ids=awaiting_reply_ids,
                           hide_closed=hide_closed)


@app.route('/search')
@login_required
def search():
    q           = request.args.get('q', '').strip()
    status_f    = request.args.get('status', '')
    date_from   = request.args.get('date_from', '')
    date_to     = request.args.get('date_to', '')
    assignee_id = request.args.get('assignee', '')
    group_f     = request.args.get('group', '')

    performed = bool(q or status_f or date_from or date_to or assignee_id or group_f)

    tickets    = []
    pagination = None

    if performed:
        query = Ticket.query

        if q:
            msg_ids = db.session.query(Message.ticket_id).filter(Message.body.ilike(f'%{q}%'))
            query = query.filter(db.or_(
                Ticket.subject.ilike(f'%{q}%'),
                Ticket.body.ilike(f'%{q}%'),
                Ticket.internal_title.ilike(f'%{q}%'),
                Ticket.submitter_email.ilike(f'%{q}%'),
                Ticket.id.in_(msg_ids),
            ))

        if status_f:
            query = query.filter(Ticket.status == status_f)

        if date_from:
            try:
                query = query.filter(Ticket.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
            except ValueError:
                pass

        if date_to:
            try:
                dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                query = query.filter(Ticket.created_at < dt)
            except ValueError:
                pass

        if assignee_id:
            try:
                query = query.join(Assignment).filter(Assignment.employee_id == int(assignee_id))
            except ValueError:
                pass

        if group_f:
            grp = Group.query.filter_by(name=group_f).first()
            if grp:
                grp_emails = [c.email.lower() for c in grp.customers]
                query = query.filter(db.func.lower(Ticket.submitter_email).in_(grp_emails))

        page     = request.args.get('page', 1, type=int)
        per_page = 25
        pagination = query.order_by(Ticket.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        tickets = pagination.items

    submitter_emails = [t.submitter_email.lower() for t in tickets]
    customer_map = {c.email.lower(): c for c in Customer.query.filter(
        db.func.lower(Customer.email).in_(submitter_emails)).all()} if submitter_emails else {}

    employees = Employee.query.filter_by(is_active=True).order_by(Employee.username).all()
    groups = Group.query.order_by(Group.name).all()

    return render_template('search.html',
                           tickets=tickets, pagination=pagination, per_page=25,
                           q=q, status_filter=status_f, date_from=date_from, date_to=date_to,
                           assignee_id=assignee_id, group_filter=group_f,
                           employees=employees, groups=groups,
                           status_choices=Ticket.STATUS_CHOICES,
                           customer_map=customer_map, performed=performed)


def _sync_github_ref(ticket):
    """Sync ticket status from the linked GitHub issue or PR."""
    url = ticket.github_pr_url or ''
    m = re.match(r'https://github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)', url)
    if not m:
        return
    repo, ref_type, number = m.group(1), m.group(2), m.group(3)
    headers = {'Accept': 'application/vnd.github+json'}
    token = app.config.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        if ref_type == 'pull':
            resp = http_requests.get(
                f'https://api.github.com/repos/{repo}/pulls/{number}',
                headers=headers, timeout=5,
            )
        else:
            resp = http_requests.get(
                f'https://api.github.com/repos/{repo}/issues/{number}',
                headers=headers, timeout=5,
            )
    except Exception:
        return
    if not resp.ok:
        return
    data = resp.json()
    gh_state  = data.get('state')          # 'open' or 'closed'
    gh_merged = data.get('merged', False)  # True only for merged PRs
    old = ticket.status
    new_status = None
    msg = None
    if gh_state == 'closed':
        if gh_merged and ticket.status not in ('resolved', 'closed'):
            new_status = 'resolved'
            msg = _('GitHub PR was merged — ticket status updated to Resolved.')
        elif not gh_merged and ticket.status not in ('resolved', 'closed'):
            new_status = 'closed'
            msg = _('GitHub issue was closed — ticket status updated to Closed.')
    elif gh_state == 'open' and ticket.status in ('resolved', 'closed'):
        new_status = 'open'
        msg = _('GitHub issue was reopened — ticket status updated to Open.')
    if new_status:
        ticket.status = new_status
        ticket.updated_at = datetime.utcnow()
        log_event(ticket, 'status', from_value=old, to_value=new_status)
        db.session.commit()
        flash(msg, 'info')


@app.route('/tickets/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    if ticket.github_sync:
        _sync_github_ref(ticket)
    employees = Employee.query.filter_by(is_active=True).all()
    events = ticket.events.all()
    is_watching = TicketWatch.query.filter_by(
        ticket_id=ticket_id, employee_id=current_user.id).first() is not None
    submitter_customer = Customer.query.filter(
        Customer.email.ilike(ticket.submitter_email)).first()
    return render_template('ticket.html', ticket=ticket, employees=employees,
                           status_choices=Ticket.STATUS_CHOICES, events=events,
                           is_watching=is_watching,
                           submitter_customer=submitter_customer)


@app.route('/tickets/<int:ticket_id>/message', methods=['POST'])
@login_required
def add_message(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    body = request.form.get('body', '').strip()
    if not body or body == '<p><br></p>':
        flash(_('Message body cannot be empty.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    is_visible = request.form.get('is_customer_visible') == 'on'
    msg = Message(ticket_id=ticket.id, employee_id=current_user.id,
                  body=body, is_customer_visible=is_visible)
    db.session.add(msg)
    ticket.updated_at = datetime.utcnow()
    db.session.flush()  # get msg.id before file save

    f = request.files.get('file')
    if f and f.filename:
        try:
            original_name = secure_filename(f.filename)
            ext = os.path.splitext(original_name)[1]
            stored_name = f'{uuid.uuid4().hex}{ext}'
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, stored_name)
            f.save(filepath)
            size = os.path.getsize(filepath)
            attachment = Attachment(ticket_id=ticket.id, message_id=msg.id,
                                    filename=stored_name, original_filename=original_name,
                                    size=size)
            db.session.add(attachment)
            log_event(ticket, 'attachment', to_value=original_name,
                      actor_id=current_user.id)
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f'Inline file upload failed: {e}')
            flash(_('File upload failed.'), 'danger')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    db.session.commit()
    if is_visible:
        plain = re.sub(r'<[^>]+>', '', body)
        notify_submitter_update(ticket, extra_message=plain)
    notify_watchers(
        ticket,
        subject=f"[{app.config['APP_NAME']}] Neue Nachricht \u2013 Ticket #{ticket.id}: {ticket.subject}",
        body=(f"Eine neue Nachricht wurde zu Ticket #{ticket.id} hinzugefuegt.\n\n"
              f"Betreff: {ticket.subject}\n\n"
              f"Ticket ansehen: {url_for('ticket_detail', ticket_id=ticket.id, _external=True)}"),
        exclude_employee_id=current_user.id,
    )
    flash(_('Message added.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/messages/<int:message_id>/edit', methods=['POST'])
@login_required
def edit_message(ticket_id, message_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    msg = db.session.get(Message, message_id) or abort(404)
    if msg.ticket_id != ticket.id or msg.is_customer_reply or msg.employee_id != current_user.id:
        abort(403)
    body = request.form.get('body', '').strip()
    if not body or body == '<p><br></p>':
        flash(_('Message body cannot be empty.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    msg.body = body
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    flash(_('Message updated.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/status', methods=['POST'])
@login_required
def change_status(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    new_status = request.form.get('status', '')
    if new_status not in Ticket.STATUS_CHOICES:
        flash(_('Invalid status.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    old_status = ticket.status
    ticket.status = new_status
    ticket.updated_at = datetime.utcnow()
    log_event(ticket, 'status', from_value=old_status, to_value=new_status,
              actor_id=current_user.id)
    db.session.commit()
    notify_submitter_update(ticket)
    notify_watchers(
        ticket,
        subject=f"[{app.config['APP_NAME']}] Status geaendert \u2013 Ticket #{ticket.id}: {ticket.subject}",
        body=(f"Status geaendert auf Ticket #{ticket.id}.\n\n"
              f"Betreff: {ticket.subject}\n"
              f"Neuer Status: {new_status.replace('_', ' ').title()}\n\n"
              f"Ticket ansehen: {url_for('ticket_detail', ticket_id=ticket.id, _external=True)}"),
        exclude_employee_id=current_user.id,
    )
    flash(_('Status changed to %(status)s.', status=status_label(new_status)), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/assign', methods=['POST'])
@login_required
def assign_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    old_assignee = ticket.assignee.username if ticket.assignee else None
    employee_id = request.form.get('employee_id', type=int)
    if employee_id:
        emp = db.session.get(Employee, employee_id)
        if not emp or not emp.is_active:
            flash(_('Invalid employee.'), 'danger')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))
        if ticket.assignment:
            ticket.assignment.employee_id = employee_id
            ticket.assignment.assigned_at = datetime.utcnow()
        else:
            assignment = Assignment(ticket_id=ticket.id, employee_id=employee_id)
            db.session.add(assignment)
        new_assignee = emp.username
    else:
        if ticket.assignment:
            db.session.delete(ticket.assignment)
        new_assignee = None
    log_event(ticket, 'assignment', from_value=old_assignee, to_value=new_assignee,
              actor_id=current_user.id)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    if employee_id and emp and new_assignee != old_assignee:
        notify_assignee_assigned(ticket, emp)
    flash(_('Assignment updated.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/watch', methods=['POST'])
@login_required
def toggle_watch(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    watch = TicketWatch.query.filter_by(ticket_id=ticket_id, employee_id=current_user.id).first()
    if watch:
        db.session.delete(watch)
        db.session.commit()
        flash(_('You are no longer watching this ticket.'), 'info')
    else:
        db.session.add(TicketWatch(ticket_id=ticket_id, employee_id=current_user.id))
        db.session.commit()
        flash(_('You are now watching this ticket.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/github_pr', methods=['POST'])
@login_required
def set_github_pr(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    pr_url = request.form.get('github_pr_url', '').strip()
    if pr_url and not pr_url.startswith('https://github.com/'):
        flash(_('Invalid GitHub PR URL.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    ticket.github_pr_url = pr_url or None
    ticket.github_pr_title = request.form.get('github_pr_title', '').strip() or None
    log_event(ticket, 'github_link', to_value=ticket.github_pr_title or pr_url or None,
              actor_id=current_user.id)
    db.session.commit()
    flash(_('GitHub link updated.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/github_sync', methods=['POST'])
@login_required
def set_github_sync(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    ticket.github_sync = request.form.get('enabled') == '1'
    db.session.commit()
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/github_search')
@login_required
def github_search(ticket_id):
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'items': []})
    org = app.config.get('GITHUB_ORG', '').strip()
    repo_prefix = re.match(r'^([\w.\-]+):\s*(.*)', q, re.DOTALL)
    if repo_prefix:
        repo_name, rest = repo_prefix.group(1), repo_prefix.group(2).strip()
        full_repo = f'{org}/{repo_name}' if org else repo_name
        base_q = f'repo:{full_repo} {rest}'.strip()
    else:
        base_q = f'org:{org} {q}' if org else q

    headers = {'Accept': 'application/vnd.github+json'}
    token = app.config.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'

    results = []
    for type_filter in ('is:pull-request', 'is:issue'):
        try:
            resp = http_requests.get(
                'https://api.github.com/search/issues',
                params={'q': f'{base_q} {type_filter}', 'per_page': 4,
                        'sort': 'created', 'order': 'desc'},
                headers=headers,
                timeout=10,
            )
        except Exception:
            return jsonify({'error': 'network'})
        if not resp.ok:
            try:
                detail = resp.json().get('message', resp.text[:120])
            except Exception:
                detail = resp.text[:120]
            app.logger.warning('GitHub search %s: %s', resp.status_code, detail)
            return jsonify({'error': detail})
        for item in resp.json().get('items', []):
            repo = '/'.join(item['repository_url'].split('/')[-2:])
            results.append({
                'number': item['number'],
                'title': item['title'],
                'url': item['html_url'],
                'state': item['state'],
                'is_pr': 'pull_request' in item,
                'repo': repo,
            })
    return jsonify({'items': results})


@app.route('/tickets/<int:ticket_id>/github_repos')
@login_required
def github_repos(ticket_id):
    org = app.config.get('GITHUB_ORG', '').strip()
    if not org:
        return jsonify({'error': 'GITHUB_ORG is not configured.'})
    headers = {'Accept': 'application/vnd.github+json'}
    token = app.config.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        resp = http_requests.get(
            f'https://api.github.com/orgs/{org}/repos',
            params={'per_page': 100, 'sort': 'updated', 'type': 'all'},
            headers=headers,
            timeout=10,
        )
    except Exception:
        return jsonify({'error': 'network'})
    if not resp.ok:
        try:
            detail = resp.json().get('message', resp.text[:120])
        except Exception:
            detail = resp.text[:120]
        return jsonify({'error': detail})
    repos = [{'name': r['name'], 'full_name': r['full_name']}
             for r in resp.json() if not r.get('archived')]
    return jsonify({'repos': repos})


@app.route('/tickets/<int:ticket_id>/internal_title', methods=['POST'])
@login_required
def set_internal_title(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    ticket.internal_title = request.form.get('internal_title', '').strip() or None
    db.session.commit()
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/github_create_issue', methods=['POST'])
@login_required
def github_create_issue(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    repo_full_name = request.form.get('repo', '').strip()
    if not repo_full_name or '/' not in repo_full_name:
        flash(_('Please select a repository.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    headers = {'Accept': 'application/vnd.github+json'}
    token = app.config.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    plain_body = re.sub(r'<[^>]+>', '', ticket.body).strip()
    issue_body = (f"{plain_body}\n\n---\n"
                  f"*Submitted by: {ticket.submitter_email}*  \n"
                  f"*{app.config['APP_NAME']} Ticket #{ticket.id}*")
    payload = {
        'title': ticket.internal_title or ticket.subject,
        'body': issue_body,
        'labels': ['enhancement', 'patch'],
    }
    if ticket.assignee and ticket.assignee.github_login:
        payload['assignees'] = [ticket.assignee.github_login]
    try:
        resp = http_requests.post(
            f'https://api.github.com/repos/{repo_full_name}/issues',
            json=payload,
            headers=headers,
            timeout=10,
        )
    except Exception:
        flash(_('GitHub API request failed.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    if not resp.ok:
        try:
            detail = resp.json().get('message', resp.text[:120])
        except Exception:
            detail = resp.text[:120]
        flash(f'GitHub: {detail}', 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    issue = resp.json()
    ticket.github_pr_url = issue['html_url']
    ticket.github_pr_title = issue['title']
    log_event(ticket, 'github_issue_created', to_value=issue['title'],
              actor_id=current_user.id)
    db.session.commit()
    flash(_('GitHub issue created and linked.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# File uploads (employee)
# ---------------------------------------------------------------------------

@app.route('/tickets/<int:ticket_id>/attachments', methods=['POST'])
@login_required
def upload_attachment(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    f = request.files.get('file')
    if not f or not f.filename:
        flash(_('No file selected.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    message_id = request.form.get('message_id', type=int)
    if message_id:
        msg = db.session.get(Message, message_id)
        if not msg or msg.ticket_id != ticket.id:
            message_id = None
    original_name = secure_filename(f.filename)
    ext = os.path.splitext(original_name)[1]
    stored_name = f'{uuid.uuid4().hex}{ext}'
    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, stored_name)
    f.save(filepath)
    size = os.path.getsize(filepath)
    attachment = Attachment(ticket_id=ticket.id, message_id=message_id,
                            filename=stored_name, original_filename=original_name, size=size)
    db.session.add(attachment)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    flash(_('File "%(name)s" uploaded.', name=original_name), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/uploads/<int:ticket_id>/<filename>')
@login_required
def serve_attachment(ticket_id, filename):
    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
    return send_from_directory(upload_dir, filename)


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin/employees', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_employees():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        is_admin = request.form.get('is_admin') == 'on'
        is_manager = request.form.get('is_manager') == 'on'
        if not username or not email:
            flash(_('All fields are required.'), 'danger')
        elif Employee.query.filter_by(username=username).first():
            flash(_('Username already taken.'), 'danger')
        elif Employee.query.filter_by(email=email).first():
            flash(_('Email already in use.'), 'danger')
        else:
            emp = Employee(username=username, email=email,
                           is_admin=is_admin, is_manager=is_manager, is_active=True)
            emp.set_password(secrets.token_hex(32))
            db.session.add(emp)
            db.session.commit()
            token = _make_setup_token(emp)
            setup_url = url_for('setup_password', token=token, _external=True)
            send_setup_email(emp.email, emp.username, setup_url)
            flash(_('A setup link has been sent to %(email)s.', email=email), 'success')
        return redirect(url_for('admin_employees'))
    employees = Employee.query.order_by(Employee.created_at.desc()).all()
    return render_template('admin/employees.html', employees=employees)


@app.route('/admin/employees/<int:emp_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_employee(emp_id):
    emp = db.session.get(Employee, emp_id) or abort(404)
    if emp.id == current_user.id:
        flash(_('You cannot deactivate your own account.'), 'danger')
        return redirect(url_for('admin_employees'))
    emp.is_active = not emp.is_active
    db.session.commit()
    if emp.is_active:
        flash(_('Employee "%(name)s" activated.', name=emp.username), 'success')
    else:
        flash(_('Employee "%(name)s" deactivated.', name=emp.username), 'success')
    return redirect(url_for('admin_employees'))


@app.route('/admin/employees/<int:emp_id>/link_github', methods=['POST'])
@login_required
@admin_required
def link_github(emp_id):
    emp = db.session.get(Employee, emp_id) or abort(404)
    username = request.form.get('github_username', '').strip()

    if not username:
        emp.github_id = None
        emp.github_login = None
        db.session.commit()
        flash(_('GitHub link removed for "%(name)s".', name=emp.username), 'success')
        return redirect(url_for('admin_employees'))

    # Resolve username via GitHub API
    auth = None
    if app.config.get('GITHUB_CLIENT_ID') and app.config.get('GITHUB_CLIENT_SECRET'):
        auth = (app.config['GITHUB_CLIENT_ID'], app.config['GITHUB_CLIENT_SECRET'])
    try:
        resp = http_requests.get(
            f'https://api.github.com/users/{username}',
            headers={'Accept': 'application/json'},
            auth=auth,
            timeout=10,
        )
    except Exception:
        flash(_('GitHub API request failed.'), 'danger')
        return redirect(url_for('admin_employees'))

    if resp.status_code == 404:
        flash(_('GitHub user "%(name)s" not found.', name=username), 'danger')
        return redirect(url_for('admin_employees'))
    if not resp.ok:
        flash(_('GitHub API request failed.'), 'danger')
        return redirect(url_for('admin_employees'))

    profile = resp.json()
    github_id = str(profile['id'])
    github_login = profile['login']

    conflict = Employee.query.filter(
        Employee.github_id == github_id, Employee.id != emp.id
    ).first()
    if conflict:
        flash(_('This GitHub account is already linked to "%(name)s".', name=conflict.username), 'danger')
        return redirect(url_for('admin_employees'))

    emp.github_id = github_id
    emp.github_login = github_login
    db.session.commit()
    flash(_('GitHub account "%(gh)s" linked to "%(emp)s".', gh=github_login, emp=emp.username), 'success')
    return redirect(url_for('admin_employees'))


@app.route('/admin/employees/<int:emp_id>/edit', methods=['POST'])
@login_required
def edit_employee(emp_id):
    if not (current_user.is_admin or current_user.is_manager):
        abort(403)
    emp = db.session.get(Employee, emp_id) or abort(404)
    if emp.id != current_user.id:
        # Editing someone else: admins can edit managers/staff; managers can edit staff only
        if emp.is_admin:
            abort(403)
        if current_user.is_manager and not current_user.is_admin and emp.is_manager:
            abort(403)
    username = request.form.get('username', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    if not username or not email:
        flash(_('Name and email are required.'), 'danger')
        return redirect(url_for('admin_employees'))
    # Uniqueness checks (exclude self)
    if Employee.query.filter(Employee.username == username, Employee.id != emp.id).first():
        flash(_('Username already taken.'), 'danger')
        return redirect(url_for('admin_employees'))
    if Employee.query.filter(Employee.email == email, Employee.id != emp.id).first():
        flash(_('Email already in use.'), 'danger')
        return redirect(url_for('admin_employees'))
    emp.username = username
    emp.email    = email
    if password:
        pw_error = _validate_password(password)
        if pw_error:
            flash(pw_error, 'danger')
            return redirect(url_for('admin_employees'))
        emp.set_password(password)
    db.session.commit()
    flash(_('Employee "%(name)s" updated.', name=emp.username), 'success')
    return redirect(url_for('admin_employees'))


@app.route('/manager/customers/<int:cust_id>/edit', methods=['POST'])
@login_required
@manager_required
def edit_customer(cust_id):
    customer = db.session.get(Customer, cust_id) or abort(404)
    name     = request.form.get('name', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    if not name or not email:
        flash(_('Name and email are required.'), 'danger')
        return redirect(url_for('manager_customers'))
    if Customer.query.filter(Customer.email == email, Customer.id != customer.id).first():
        flash(_('Email already in use.'), 'danger')
        return redirect(url_for('manager_customers'))
    customer.name      = name
    customer.email     = email
    customer.groups = _resolve_groups(
        request.form.getlist('group_ids', type=int),
        request.form.get('new_group', '').strip(),
    )
    if password:
        pw_error = _validate_password(password)
        if pw_error:
            flash(pw_error, 'danger')
            return redirect(url_for('manager_customers'))
        customer.set_password(password)
    db.session.commit()
    flash(_('Customer "%(name)s" updated.', name=customer.name), 'success')
    return redirect(url_for('manager_customers'))


@app.route('/admin/employees/<int:emp_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_employee(emp_id):
    emp = db.session.get(Employee, emp_id) or abort(404)
    if emp.id == current_user.id:
        flash(_('You cannot delete your own account.'), 'danger')
        return redirect(url_for('admin_employees'))
    username = emp.username
    # Detach authored messages (keep them, just unlink the author)
    Message.query.filter_by(employee_id=emp.id).update({'employee_id': None})
    # Remove ticket assignments
    Assignment.query.filter_by(employee_id=emp.id).delete()
    # Detach created customers
    Customer.query.filter_by(created_by_id=emp.id).update({'created_by_id': None})
    db.session.delete(emp)
    db.session.commit()
    flash(_('Employee "%(name)s" deleted.', name=username), 'success')
    return redirect(url_for('admin_employees'))


# ---------------------------------------------------------------------------
# Mail test (admin only)
# ---------------------------------------------------------------------------

@app.route('/admin/mail-test', methods=['GET', 'POST'])
@admin_required
def admin_mail_test():
    cfg = app.config
    info = {
        'MAIL_SERVER':          cfg.get('MAIL_SERVER'),
        'MAIL_PORT':            cfg.get('MAIL_PORT'),
        'MAIL_USE_TLS':         cfg.get('MAIL_USE_TLS'),
        'MAIL_USE_SSL':         cfg.get('MAIL_USE_SSL'),
        'MAIL_USERNAME':        cfg.get('MAIL_USERNAME') or '(not set)',
        'MAIL_PASSWORD':        '***' if cfg.get('MAIL_PASSWORD') else '(not set)',
        'MAIL_DEFAULT_SENDER':  cfg.get('MAIL_DEFAULT_SENDER'),
        'MAIL_SUPPRESS_SEND':   cfg.get('MAIL_SUPPRESS_SEND'),
        'TESTING':              cfg.get('TESTING'),
    }
    result = None
    if request.method == 'POST':
        recipient = request.form.get('recipient', '').strip()
        if not recipient:
            flash(_('Please enter a recipient address.'), 'danger')
        else:
            import smtplib, ssl as _ssl
            # Low-level SMTP probe so we can report exactly what fails
            try:
                port    = int(cfg.get('MAIL_PORT', 25))
                use_ssl = cfg.get('MAIL_USE_SSL', False)
                use_tls = cfg.get('MAIL_USE_TLS', False)
                host    = cfg.get('MAIL_SERVER', 'localhost')
                user    = cfg.get('MAIL_USERNAME') or ''
                pw      = cfg.get('MAIL_PASSWORD') or ''

                if use_ssl:
                    ctx  = _ssl.create_default_context()
                    smtp = smtplib.SMTP_SSL(host, port, context=ctx, timeout=10)
                else:
                    smtp = smtplib.SMTP(host, port, timeout=10)
                    if use_tls:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()

                if user and pw:
                    smtp.login(user, pw)

                sender = cfg.get('MAIL_DEFAULT_SENDER', user)
                smtp.sendmail(sender, [recipient],
                    f"From: {sender}\r\nTo: {recipient}\r\n"
                    f"Subject: [{app.config['APP_NAME']}] Mail test\r\n\r\n"
                    f"This is a test email from {app.config['APP_NAME']}.\r\n"
                    f"Server: {host}:{port}  TLS={use_tls}  SSL={use_ssl}\r\n"
                    f"Sender: {sender}\r\n"
                )
                smtp.quit()
                result = ('success', f'SMTP accepted the message for {recipient}. Check inbox (and spam).')
                app.logger.info(f'Mail test to {recipient} succeeded via {host}:{port}')
            except Exception as e:
                result = ('danger', f'{type(e).__name__}: {e}')
                app.logger.error(f'Mail test failed: {e}')

    return render_template('admin/mail_test.html', info=info, result=result)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message=_('Forbidden')), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message=_('Page not found')), 404


@app.errorhandler(429)
def too_many_requests(e):
    return render_template('error.html', code=429, message=_('Too many requests. Please wait a moment.')), 429


@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    app.logger.exception('Unhandled exception')
    return render_template('error.html', code=500, message=_('An unexpected error occurred.')), 500


def _render_markdown(path):
    """Return (content_html, toc_html) from a Markdown file."""
    with open(path, encoding='utf-8') as f:
        src = f.read()
    md = _md_lib.Markdown(
        extensions=['tables', 'fenced_code', _TocExtension(permalink=False)]
    )
    content_html = Markup(md.convert(src))
    toc_html = Markup(md.toc)
    return content_html, toc_html


@app.route('/help')
@login_required
def help_page():
    content, toc = _render_markdown(
        os.path.join(app.root_path, 'docs', 'manual-employees.md'))
    return render_template('help.html', content=content, toc=toc,
                           title=_('Employee Manual'))


@app.route('/customer/help')
def customer_help():
    if not get_current_customer():
        return redirect(url_for('customer_login'))
    content, toc = _render_markdown(
        os.path.join(app.root_path, 'docs', 'manual-customers.md'))
    return render_template('help.html', content=content, toc=toc,
                           title=_('Customer Manual'))


@app.route('/healthz')
def healthz():
    """Lightweight liveness probe — no DB query, just confirms the process is up."""
    return jsonify(status='ok'), 200


# ---------------------------------------------------------------------------
# Inbound e-mail – IMAP polling
# ---------------------------------------------------------------------------

def _decode_mime_words(value):
    """Decode a MIME encoded-word header value to a plain Unicode string."""
    if not value:
        return ''
    result = ''
    for part, enc in _decode_header(value):
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='replace')
        else:
            result += part
    return result.strip()


def _extract_email_body(msg):
    """Return the text content of an email as safe HTML ready for ticket storage."""
    plain = None
    html_raw = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get('Content-Disposition', ''))
            if 'attachment' in disp:
                continue
            charset = part.get_content_charset() or 'utf-8'
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            decoded = payload.decode(charset, errors='replace')
            if ct == 'text/plain' and plain is None:
                plain = decoded
            elif ct == 'text/html' and html_raw is None:
                html_raw = decoded
    else:
        charset = msg.get_content_charset() or 'utf-8'
        payload = msg.get_payload(decode=True)
        decoded = payload.decode(charset, errors='replace') if payload else ''
        if msg.get_content_type() == 'text/html':
            html_raw = decoded
        else:
            plain = decoded

    if plain:
        text = plain.strip()
    elif html_raw:
        # Strip tags; collapse whitespace
        text = re.sub(r'<[^>]+>', ' ', html_raw)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
    else:
        text = ''

    if not text:
        return '<p>(Empty message)</p>'

    # Escape and convert newlines to HTML paragraphs/breaks
    safe = str(escape(text))
    return '<p>' + safe.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'


def _process_imap_inbox():
    """Connect to the configured IMAP mailbox and turn every unseen message into a ticket."""
    host = app.config.get('IMAP_HOST', '').strip()
    user = app.config.get('IMAP_USER', '').strip()
    password = app.config.get('IMAP_PASSWORD', '').strip()

    if not host:
        return
    if not (user and password):
        app.logger.warning('IMAP_HOST is set but IMAP_USER / IMAP_PASSWORD are missing.')
        return

    port    = app.config.get('IMAP_PORT', 993)
    use_ssl = app.config.get('IMAP_USE_SSL', True)

    try:
        M = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        M.login(user, password)
        M.select('INBOX')
    except Exception as exc:
        app.logger.error(f'IMAP connection failed ({host}:{port}): {exc}')
        return

    try:
        status, data = M.search(None, 'UNSEEN')
        if status != 'OK' or not data[0]:
            return

        for msg_num in data[0].split():
            # Mark seen immediately to prevent reprocessing on restart
            M.store(msg_num, '+FLAGS', '\\Seen')

            try:
                status2, raw_data = M.fetch(msg_num, '(RFC822)')
                if status2 != 'OK':
                    continue

                msg = _email_lib.message_from_bytes(raw_data[0][1])

                from_addr = _email_lib.utils.parseaddr(msg.get('From', ''))[1].lower().strip()
                if not from_addr or '@' not in from_addr:
                    continue

                subject = _decode_mime_words(msg.get('Subject', '')).strip() or '(No Subject)'
                subject = subject[:200]
                body_html = _extract_email_body(msg)

                ticket = Ticket(
                    submitter_email=from_addr,
                    subject=subject,
                    body=body_html,
                    status='open',
                )
                db.session.add(ticket)
                db.session.flush()
                log_event(ticket, 'email_received')
                db.session.commit()
                notify_submitter_confirmation(ticket)
                app.logger.info(
                    f'[IMAP] Ticket #{ticket.id} created from {from_addr} – {subject}')

            except Exception as exc:
                app.logger.error(f'[IMAP] Failed to process message {msg_num}: {exc}')
                db.session.rollback()
    finally:
        try:
            M.close()
            M.logout()
        except Exception:
            pass


def _imap_poll_loop():
    interval = app.config.get('IMAP_POLL_INTERVAL', 60)
    app.logger.info(f'[IMAP] Polling {app.config["IMAP_HOST"]} every {interval}s.')
    failures = 0
    while True:
        try:
            with app.app_context():
                _process_imap_inbox()
            failures = 0
            time.sleep(interval)
        except Exception as exc:
            failures += 1
            backoff = min(interval * (2 ** failures), 3600)
            app.logger.error(f'[IMAP] Poll loop error (attempt {failures}, retry in {backoff}s): {exc}')
            time.sleep(backoff)


@app.cli.command('reset-admin')
@click.option('--username', default=None, help='Username of the admin to reset (default: first active admin)')
def cli_reset_admin(username):
    """Emergency admin password reset. Run from the server command line only."""
    if username:
        emp = Employee.query.filter_by(username=username, is_admin=True).first()
        if not emp:
            print(f'ERROR: No admin account found with username "{username}".')
            raise SystemExit(1)
    else:
        emp = Employee.query.filter_by(is_admin=True, is_active=True).first()
        if not emp:
            print('ERROR: No active admin account found.')
            raise SystemExit(1)

    # Build a 16-char password guaranteed to satisfy all validation rules
    upper   = string.ascii_uppercase
    lower   = string.ascii_lowercase
    digits  = string.digits
    special = '!@#$%^&*()-_=+'
    pool    = upper + lower + digits + special
    while True:
        chars = (
            [secrets.choice(upper),   secrets.choice(lower),
             secrets.choice(digits),  secrets.choice(special)]
            + [secrets.choice(pool) for _ in range(12)]
        )
        secrets.SystemRandom().shuffle(chars)
        pw = ''.join(chars)
        if _validate_password(pw) is None:
            break

    emp.set_password(pw)
    db.session.commit()
    print('')
    print(f'  Admin account : {emp.username} ({emp.email})')
    print(f'  New password  : {pw}')
    print('')
    print('Log in immediately and change this password.')


@app.cli.command('poll-imap')
def cli_poll_imap():
    """Manually trigger one IMAP inbox poll (for testing)."""
    with app.app_context():
        _process_imap_inbox()
    print('Done.')


# ---------------------------------------------------------------------------
# Inbound e-mail – Microsoft Graph API (Microsoft 365 shared mailbox)
# ---------------------------------------------------------------------------

_graph_token_cache: dict = {}   # keys: token, expires_at


def _get_graph_token() -> str:
    """Return a valid OAuth2 bearer token for Microsoft Graph, refreshing when needed."""
    now = time.time()
    if _graph_token_cache.get('token') and _graph_token_cache.get('expires_at', 0) > now + 30:
        return _graph_token_cache['token']

    tenant = app.config['AZURE_TENANT_ID']
    url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    resp = http_requests.post(url, data={
        'grant_type':    'client_credentials',
        'client_id':     app.config['AZURE_CLIENT_ID'],
        'client_secret': app.config['AZURE_CLIENT_SECRET'],
        'scope':         'https://graph.microsoft.com/.default',
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _graph_token_cache['token'] = data['access_token']
    _graph_token_cache['expires_at'] = now + data.get('expires_in', 3600)
    return _graph_token_cache['token']


def _process_graph_inbox():
    """Fetch unread messages from the M365 shared mailbox and create tickets."""
    mailbox = app.config.get('GRAPH_MAILBOX', '').strip()
    if not mailbox:
        app.logger.warning('[Graph] GRAPH_MAILBOX is not set.')
        return

    try:
        token = _get_graph_token()
    except Exception as exc:
        app.logger.error(f'[Graph] Failed to obtain access token: {exc}')
        return

    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    base = f'https://graph.microsoft.com/v1.0/users/{mailbox}/messages'
    params = {
        '$filter': 'isRead eq false',
        '$select': 'id,subject,from,body,receivedDateTime',
        '$top': 50,
    }

    while True:
        try:
            resp = http_requests.get(base, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            app.logger.error(f'[Graph] Failed to fetch messages: {exc}')
            return

        data = resp.json()
        messages = data.get('value', [])

        for msg in messages:
            msg_id = msg.get('id')
            # Mark as read immediately to prevent reprocessing
            try:
                http_requests.patch(
                    f'{base}/{msg_id}',
                    headers=headers,
                    json={'isRead': True},
                    timeout=10,
                ).raise_for_status()
            except Exception as exc:
                app.logger.warning(f'[Graph] Could not mark message {msg_id} as read: {exc}')

            try:
                from_info = msg.get('from', {}).get('emailAddress', {})
                from_addr = from_info.get('address', '').lower().strip()
                if not from_addr or '@' not in from_addr:
                    continue

                subject = (msg.get('subject') or '(No Subject)').strip()[:200]

                body_obj = msg.get('body', {})
                content_type = body_obj.get('contentType', 'text')
                content = body_obj.get('content', '')

                if content_type == 'html':
                    # Strip tags for a plain-text basis, then re-wrap in safe HTML
                    plain = re.sub(r'<[^>]+>', ' ', content)
                    plain = re.sub(r'[ \t]+', ' ', plain)
                    plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
                    if plain:
                        safe = str(escape(plain))
                        body_html = '<p>' + safe.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'
                    else:
                        body_html = '<p>(Empty message)</p>'
                else:
                    text = content.strip()
                    if text:
                        safe = str(escape(text))
                        body_html = '<p>' + safe.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'
                    else:
                        body_html = '<p>(Empty message)</p>'

                ticket = Ticket(
                    submitter_email=from_addr,
                    subject=subject,
                    body=body_html,
                    status='open',
                )
                db.session.add(ticket)
                db.session.flush()
                log_event(ticket, 'email_received')
                db.session.commit()
                notify_submitter_confirmation(ticket)
                app.logger.info(f'[Graph] Ticket #{ticket.id} created from {from_addr} – {subject}')

            except Exception as exc:
                app.logger.error(f'[Graph] Failed to process message {msg_id}: {exc}')
                db.session.rollback()

        # Follow @odata.nextLink for pagination
        next_link = data.get('@odata.nextLink')
        if not next_link:
            break
        base = next_link
        params = {}


def _graph_poll_loop():
    interval = app.config.get('GRAPH_POLL_INTERVAL', 60)
    app.logger.info(f'[Graph] Polling {app.config["GRAPH_MAILBOX"]} every {interval}s.')
    failures = 0
    while True:
        try:
            with app.app_context():
                _process_graph_inbox()
            failures = 0
            time.sleep(interval)
        except Exception as exc:
            failures += 1
            backoff = min(interval * (2 ** failures), 3600)
            app.logger.error(f'[Graph] Poll loop error (attempt {failures}, retry in {backoff}s): {exc}')
            time.sleep(backoff)


@app.cli.command('poll-graph')
def cli_poll_graph():
    """Manually trigger one Microsoft Graph inbox poll (for testing)."""
    with app.app_context():
        _process_graph_inbox()
    print('Done.')


# Start background polling thread.
# Graph API takes priority if Azure credentials are set; falls back to IMAP.
# In debug mode the Werkzeug reloader spawns a child process that sets
# WERKZEUG_RUN_MAIN=true — only start the thread there, not in the watcher.
_use_graph = bool(app.config.get('AZURE_TENANT_ID') and app.config.get('GRAPH_MAILBOX'))
_use_imap  = bool(app.config.get('IMAP_HOST')) and not _use_graph

if _use_graph or _use_imap:
    _debug = app.debug
    _in_reloader_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    if not _debug or _in_reloader_child:
        _target = _graph_poll_loop if _use_graph else _imap_poll_loop
        _name   = 'graph-poll' if _use_graph else 'imap-poll'
        _t = threading.Thread(target=_target, daemon=True, name=_name)
        _t.start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
