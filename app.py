import email as _email_lib
import email.utils
import imaplib
import os
import shutil
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


@app.template_filter('age_label')
def age_label_filter(dt):
    """Short human-readable age from a datetime: '3h', '2d', '5w', '3mo'."""
    secs = (datetime.utcnow() - dt).total_seconds()
    m = int(secs // 60)
    if m < 1:   return '<1m'
    if m < 60:  return f'{m}m'
    h = m // 60
    if h < 24:  return f'{h}h'
    d = h // 24
    if d < 14:  return f'{d}d'
    if d < 56:  return f'{d // 7}w'
    return f'{d // 30}mo'


@app.template_filter('age_class')
def age_class_filter(dt):
    """Bootstrap text-color class based on how old a datetime is."""
    hours = (datetime.utcnow() - dt).total_seconds() / 3600
    if hours < 24:   return 'text-muted'
    if hours < 72:   return 'text-warning'
    if hours < 168:  return 'text-danger'
    return 'text-danger fw-bold'


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


def notify_mentions(ticket, mentioned_usernames, sender_id):
    """Email each @mentioned active employee, skipping anyone already notified
    through the normal watcher / assignee notification path."""
    if not mentioned_usernames:
        return
    app_name    = app.config['APP_NAME']
    detail_url  = url_for('ticket_detail', ticket_id=ticket.id, _external=True)
    assignee_id = ticket.assignment.employee_id if ticket.assignment else None
    watcher_ids = {w.employee_id for w in TicketWatch.query.filter_by(ticket_id=ticket.id).all()}
    excluded    = {eid for eid in [sender_id, assignee_id] if eid} | watcher_ids
    for username in set(mentioned_usernames):
        emp = Employee.query.filter_by(username=username, is_active=True).first()
        if emp is None or emp.id in excluded:
            continue
        body = (
            f"You were mentioned in a message on Ticket #{ticket.id}.\n\n"
            f"Subject: {ticket.subject}\n\n"
            f"View ticket: {detail_url}"
        )
        send_email(
            subject=f"[{app_name}] You were mentioned – Ticket #{ticket.id}: {ticket.subject}",
            recipients=[emp.email],
            body_text=body,
            silent=True,
        )


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
    is_privileged = current_user.is_authenticated and (
        current_user.is_admin or current_user.is_manager
    )
    if is_privileged:
        all_projects = Group.query.order_by(Group.name).all()
    else:
        all_projects = None
    customer_projects = list(customer.groups) if customer else []
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
            return render_template('submit.html', customer=customer,
                                   customer_projects=customer_projects,
                                   all_projects=all_projects)
        # Resolve project assignment
        group_id = None
        raw = request.form.get('group_id', '').strip()
        if raw.isdigit():
            gid = int(raw)
            if is_privileged:
                group_id = gid
            elif any(g.id == gid for g in customer_projects):
                group_id = gid
        elif not raw and len(customer_projects) == 1:
            group_id = customer_projects[0].id
        ticket = Ticket(submitter_email=email, subject=subject, body=body,
                        locale=session.get('lang', 'en'), group_id=group_id)
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
    return render_template('submit.html', customer=customer,
                           customer_projects=customer_projects,
                           all_projects=all_projects)


@app.route('/status/<token>')
def ticket_status(token):
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    customer = get_current_customer()
    if not app.config['PUBLIC_TICKETS'] and not current_user.is_authenticated and not customer:
        flash(_('Please log in to view your ticket.'), 'warning')
        return redirect(url_for('login', next=request.url))
    if ticket.status in ('resolved', 'closed'):
        is_submitter = customer and customer.email.lower() == ticket.submitter_email.lower()
        is_project_member = (customer and ticket.group and
                             any(g.id == ticket.group_id for g in customer.groups))
        if not (is_submitter or is_project_member or current_user.is_authenticated):
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
    db.session.flush()  # get msg.id before file save

    f = request.files.get('file')
    if f and f.filename:
        try:
            original_name = secure_filename(f.filename)
            ext = os.path.splitext(original_name)[1]
            stored_name = f'{uuid.uuid4().hex}{ext}'
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket.id))
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, stored_name)
            f.save(filepath)
            size = os.path.getsize(filepath)
            attachment = Attachment(ticket_id=ticket.id, message_id=msg.id,
                                    filename=stored_name, original_filename=original_name,
                                    size=size)
            db.session.add(attachment)
            log_event(ticket, 'attachment', to_value=original_name)
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f'Customer file upload failed: {e}')
            flash(_('File upload failed.'), 'danger')
            return redirect(url_for('ticket_status', token=token))

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


@app.route('/status/<token>/rate', methods=['POST'])
def rate_ticket(token):
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    if ticket.status not in ('resolved', 'closed'):
        abort(400)
    if ticket.satisfaction_rating is not None:
        flash(_('You have already rated this ticket.'), 'info')
        return redirect(url_for('ticket_status', token=token))
    rating = request.form.get('rating', type=int)
    if not rating or not (1 <= rating <= 5):
        flash(_('Please select a rating.'), 'danger')
        return redirect(url_for('ticket_status', token=token))
    ticket.satisfaction_rating       = rating
    ticket.satisfaction_comment      = request.form.get('comment', '').strip() or None
    ticket.satisfaction_submitted_at = datetime.utcnow()
    db.session.commit()
    flash(_('Thank you for your feedback!'), 'success')
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
    if view not in ('all', 'awaiting', 'closed', 'groups'):
        view = 'all'
    status_filter = request.args.get('status', '')
    q             = request.args.get('q', '').strip()

    customer_group_ids = [g.id for g in customer.groups]

    # Always base own-ticket queries on the customer's email (for stats + awaiting)
    own_base = Ticket.query.filter(Ticket.submitter_email.ilike(customer.email))

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

    # Choose list base: own tickets or group tickets
    if view == 'groups' and customer_group_ids:
        list_base = Ticket.query.filter(Ticket.group_id.in_(customer_group_ids))
    elif view == 'groups':
        list_base = Ticket.query.filter(db.false())
    else:
        list_base = own_base

    query       = list_base
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

    # Stats always from own tickets
    all_own_tickets = own_base.all()
    stats = {
        'open':           sum(1 for t in all_own_tickets if t.status == 'open'),
        'in_progress':    sum(1 for t in all_own_tickets if t.status == 'in_progress'),
        'awaiting_reply': sum(1 for t in all_own_tickets
                              if t.id in awaiting_reply_ids and t.status not in ('resolved', 'closed')),
        'closed':         sum(1 for t in all_own_tickets if t.status in ('resolved', 'closed')),
    }

    # Per-ticket metadata for the displayed page
    _page_ticket_ids = [t.id for t in tickets]
    _att_ids = {
        row[0] for row in
        db.session.query(Attachment.ticket_id)
        .filter(Attachment.ticket_id.in_(_page_ticket_ids))
        .distinct().all()
    } if _page_ticket_ids else set()
    ticket_meta = {
        t.id: {
            'reply_count':    t.messages.filter_by(is_customer_visible=True).count(),
            'awaiting_reply': t.id in awaiting_reply_ids,
            'has_attachment': t.id in _att_ids,
        }
        for t in tickets
    }

    all_own_ids = [t.id for t in all_own_tickets]
    recent_events = (TicketEvent.query
                     .filter(
                         TicketEvent.ticket_id.in_(all_own_ids),
                         TicketEvent.event_type.in_(['status', 'customer_reply', 'attachment', 'assignment', 'group'])
                     )
                     .order_by(TicketEvent.created_at.desc())
                     .limit(15).all()) if all_own_ids else []

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
                           awaiting_reply_ids=awaiting_reply_ids,
                           customer_group_ids=customer_group_ids,
                           customer_groups=customer.groups)


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
    group_ticket_counts = {
        g.id: g.tickets.count() for g in all_groups
    }
    return render_template('manager/customers.html', customers=customers,
                           all_groups=all_groups, group_ticket_counts=group_ticket_counts)


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


@app.route('/admin/customers/delete-bulk', methods=['POST'])
@login_required
@admin_required
def admin_customers_delete_bulk():
    ids = {int(x) for x in request.form.getlist('ids') if x.isdigit()}
    if not ids:
        flash(_('No customers selected.'), 'warning')
        return redirect(url_for('manager_customers'))
    deleted = 0
    for cust in Customer.query.filter(Customer.id.in_(ids)).all():
        db.session.delete(cust)
        deleted += 1
    db.session.commit()
    flash(_('%(n)d customer(s) deleted.', n=deleted), 'success')
    return redirect(url_for('manager_customers'))


@app.route('/manager/groups/<int:group_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_group(group_id):
    grp = db.session.get(Group, group_id)
    if not grp:
        abort(404)
    if grp.tickets.count() > 0:
        flash(_('Cannot delete project "%(name)s" because it has tickets assigned to it.', name=grp.name), 'danger')
        return redirect(url_for('manager_customers'))
    name = grp.name
    db.session.delete(grp)
    db.session.commit()
    flash(_('Project "%(name)s" deleted.', name=name), 'success')
    return redirect(url_for('manager_customers'))


# ---------------------------------------------------------------------------
# Employee routes
# ---------------------------------------------------------------------------

@app.route('/dashboard/heatmap', methods=['POST'])
@login_required
def toggle_heatmap():
    current_user.set_pref('show_heatmap', not current_user.get_pref('show_heatmap', True))
    db.session.commit()
    return redirect(url_for('dashboard'))


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
    date_filter          = request.args.get('date', '').strip()
    # Validate date format; discard silently if malformed
    if date_filter:
        try:
            datetime.strptime(date_filter, '%Y-%m-%d')
        except ValueError:
            date_filter = ''

    query = Ticket.query
    # Hide closed tickets by default unless explicitly requested
    hide_closed = not status_filter and not unassigned_filter and not resolved_week_filter and not date_filter
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
            query = query.filter(Ticket.group_id == grp.id)
    if date_filter:
        query = query.filter(db.func.date(Ticket.created_at) == date_filter)
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

    ticket_ids = [t.id for t in tickets]
    has_attachment_ids = {
        row[0] for row in
        db.session.query(Attachment.ticket_id)
        .filter(Attachment.ticket_id.in_(ticket_ids))
        .distinct().all()
    } if ticket_ids else set()

    # Build customer lookup for submitter column
    submitter_emails = [t.submitter_email.lower() for t in tickets]
    if submitter_emails:
        customer_map = {c.email.lower(): c for c in Customer.query.filter(
            db.func.lower(Customer.email).in_(submitter_emails)).all()}
    else:
        customer_map = {}

    # Only groups that have at least one ticket assigned
    groups = Group.query.filter(Group.tickets.any()).order_by(Group.name).all()

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

    # ── Activity heatmap (52-week calendar, scoped to the active view) ──────
    show_heatmap = current_user.get_pref('show_heatmap', True)
    if show_heatmap:
        _today    = datetime.utcnow().date()
        _hm_start = _today - timedelta(weeks=51)
        _hm_start -= timedelta(days=_hm_start.weekday())   # align to Monday
        _hm_start_dt = datetime(_hm_start.year, _hm_start.month, _hm_start.day)
        _raw = (_stats_base()
                .with_entities(
                    db.func.date(Ticket.created_at).label('day'),
                    db.func.count().label('cnt'),
                )
                .filter(Ticket.created_at >= _hm_start_dt)
                .group_by(db.func.date(Ticket.created_at))
                .all())
        _cnt_map    = {str(r.day): r.cnt for r in _raw}
        heatmap_weeks, _cur, _prev_month = [], _hm_start, None
        while _cur <= _today:
            days = []
            for i in range(7):
                _d = _cur + timedelta(days=i)
                days.append({'date': str(_d), 'count': _cnt_map.get(str(_d), 0), 'future': _d > _today})
            month_label = _cur.strftime('%b') if _cur.month != _prev_month else ''
            if month_label:
                _prev_month = _cur.month
            heatmap_weeks.append({'days': days, 'month_label': month_label})
            _cur += timedelta(weeks=1)
    else:
        heatmap_weeks = []

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
                           has_attachment_ids=has_attachment_ids,
                           hide_closed=hide_closed,
                           heatmap_weeks=heatmap_weeks,
                           show_heatmap=show_heatmap,
                           date_filter=date_filter)


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
                query = query.filter(Ticket.group_id == grp.id)

        page     = request.args.get('page', 1, type=int)
        per_page = 25
        pagination = query.order_by(Ticket.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        tickets = pagination.items

    submitter_emails = [t.submitter_email.lower() for t in tickets]
    customer_map = {c.email.lower(): c for c in Customer.query.filter(
        db.func.lower(Customer.email).in_(submitter_emails)).all()} if submitter_emails else {}

    employees = Employee.query.filter_by(is_active=True).order_by(Employee.username).all()
    groups = Group.query.filter(Group.tickets.any()).order_by(Group.name).all()

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
            new_status = 'resolved'
            msg = _('GitHub issue was closed — ticket status updated to Resolved.')
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
    if submitter_customer and submitter_customer.groups:
        groups = sorted(submitter_customer.groups, key=lambda g: g.name)
    else:
        groups = Group.query.order_by(Group.name).all()
    return render_template('ticket.html', ticket=ticket, employees=employees,
                           status_choices=Ticket.STATUS_CHOICES, events=events,
                           is_watching=is_watching,
                           submitter_customer=submitter_customer,
                           groups=groups)


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
    plain     = re.sub(r'<[^>]+>', '', body)
    mentioned = set(re.findall(r'data-mention="([^"]+)"', body))
    if is_visible:
        notify_submitter_update(ticket, extra_message=plain)
    notify_watchers(
        ticket,
        subject=f"[{app.config['APP_NAME']}] Neue Nachricht \u2013 Ticket #{ticket.id}: {ticket.subject}",
        body=(f"Eine neue Nachricht wurde zu Ticket #{ticket.id} hinzugefuegt.\n\n"
              f"Betreff: {ticket.subject}\n\n"
              f"Ticket ansehen: {url_for('ticket_detail', ticket_id=ticket.id, _external=True)}"),
        exclude_employee_id=current_user.id,
    )
    notify_mentions(ticket, mentioned, sender_id=current_user.id)
    flash(_('Message added.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def delete_message(ticket_id, message_id):
    if not (current_user.is_admin or current_user.is_manager):
        abort(403)
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    msg = db.session.get(Message, message_id) or abort(404)
    if msg.ticket_id != ticket.id:
        abort(404)
    for att in msg.attachments.all():
        disk_path = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket.id), att.filename)
        try:
            if os.path.exists(disk_path):
                os.remove(disk_path)
        except OSError:
            pass
        db.session.delete(att)
    db.session.delete(msg)
    db.session.commit()
    flash(_('Message deleted.'), 'success')
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
    old_mentions = set(re.findall(r'data-mention="([^"]+)"', msg.body))
    msg.body = body
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    new_mentions = set(re.findall(r'data-mention="([^"]+)"', body))
    notify_mentions(ticket, new_mentions - old_mentions, sender_id=current_user.id)
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


@app.route('/tickets/<int:ticket_id>/group', methods=['POST'])
@login_required
def set_ticket_group(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    submitter_cust = Customer.query.filter(
        Customer.email.ilike(ticket.submitter_email)).first()
    allowed_ids = {g.id for g in submitter_cust.groups} if (submitter_cust and submitter_cust.groups) else None
    old_group = ticket.group.name if ticket.group else None
    group_id_raw = request.form.get('group_id', '').strip()
    if group_id_raw and group_id_raw.isdigit():
        grp = db.session.get(Group, int(group_id_raw))
        if not grp:
            flash(_('Project not found.'), 'danger')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))
        if allowed_ids is not None and grp.id not in allowed_ids:
            flash(_('This project is not available for the ticket submitter.'), 'danger')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))
        ticket.group_id = grp.id
        new_group = grp.name
    else:
        ticket.group_id = None
        new_group = None
    ticket.updated_at = datetime.utcnow()
    log_event(ticket, 'group', from_value=old_group, to_value=new_group,
              actor_id=current_user.id)
    db.session.commit()
    flash(_('Project updated.'), 'success')
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


@app.route('/employees/for-mention')
@login_required
def employees_for_mention():
    """Return active employee usernames matching q (used by @mention autocomplete)."""
    q   = request.args.get('q', '').strip().lower()
    qry = Employee.query.filter_by(is_active=True).order_by(Employee.username)
    if q:
        qry = qry.filter(db.func.lower(Employee.username).contains(q))
    return jsonify({'employees': [{'username': e.username} for e in qry.limit(10).all()]})


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
    att = Attachment.query.filter_by(ticket_id=ticket_id, filename=filename).first()
    download_name = att.original_filename if att else filename
    return send_from_directory(upload_dir, filename, download_name=download_name)


def _hard_delete_ticket(ticket_id):
    """Delete a ticket and every related DB row, then remove its upload directory.

    Deletion order respects FK constraints: attachments → messages → events →
    watches → assignment → ticket.  The DB transaction is committed before any
    filesystem work so a DB error never leaves orphaned files.  A missing upload
    directory is silently ignored; other OS errors are logged as warnings.
    Safe to call when the ticket no longer exists (all queries become no-ops).
    """
    try:
        for att in Attachment.query.filter_by(ticket_id=ticket_id).all():
            db.session.delete(att)
        for msg in Message.query.filter_by(ticket_id=ticket_id).all():
            db.session.delete(msg)
        TicketEvent.query.filter_by(ticket_id=ticket_id).delete()
        TicketWatch.query.filter_by(ticket_id=ticket_id).delete()
        Assignment.query.filter_by(ticket_id=ticket_id).delete()
        ticket = db.session.get(Ticket, ticket_id)
        if ticket:
            db.session.delete(ticket)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
    try:
        shutil.rmtree(upload_dir)
    except FileNotFoundError:
        pass
    except OSError as e:
        app.logger.warning('Could not remove upload dir %s: %s', upload_dir, e)


@app.route('/tickets/<int:ticket_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    subject = ticket.subject
    _hard_delete_ticket(ticket_id)
    flash(_('Ticket #%(id)s "%(subject)s" deleted.', id=ticket_id, subject=subject), 'success')
    return redirect(url_for('dashboard'))


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
        is_admin   = request.form.get('is_admin') == 'on'
        is_manager = request.form.get('is_manager') == 'on' and not is_admin
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
        # Editing someone else: admins can edit anyone; managers can edit staff only
        if not current_user.is_admin and emp.is_admin:
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
    if current_user.is_admin and emp.id != current_user.id:
        new_is_admin   = 'is_admin'   in request.form
        new_is_manager = 'is_manager' in request.form and not new_is_admin
        if emp.is_admin and not new_is_admin:
            remaining = Employee.query.filter_by(is_admin=True, is_active=True).filter(
                Employee.id != emp.id
            ).count()
            if remaining == 0:
                flash(_('Cannot remove admin role: no other active admin exists.'), 'danger')
                return redirect(url_for('admin_employees'))
        emp.is_admin   = new_is_admin
        emp.is_manager = new_is_manager
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
    if emp.is_active:
        flash(_('Deactivate the employee before deleting them.'), 'danger')
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


@app.route('/admin/employees/delete-bulk', methods=['POST'])
@login_required
@admin_required
def admin_employees_delete_bulk():
    ids = {int(x) for x in request.form.getlist('ids') if x.isdigit()}
    ids.discard(current_user.id)  # never delete self
    if not ids:
        flash(_('No employees selected.'), 'warning')
        return redirect(url_for('admin_employees'))
    deleted = 0
    for emp in Employee.query.filter(Employee.id.in_(ids)).all():
        Message.query.filter_by(employee_id=emp.id).update({'employee_id': None})
        Assignment.query.filter_by(employee_id=emp.id).delete()
        Customer.query.filter_by(created_by_id=emp.id).update({'created_by_id': None})
        db.session.delete(emp)
        deleted += 1
    db.session.commit()
    flash(_('%(n)d employee(s) deleted.', n=deleted), 'success')
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


@app.route('/admin/tests')
@login_required
@admin_required
def admin_tests():
    import smtplib
    import ssl as _ssl
    import threading
    import os
    import json
    import urllib.request
    import urllib.parse
    from collections import Counter
    from sqlalchemy import text

    # Categories listed here appear in the "Infrastructure" section; anything
    # else is grouped under "Functional Tests".  This is the single source of
    # truth — the template reads it from the context.
    INFRA_CATEGORIES  = ['Database', 'Configuration', 'Email', 'Inbound email', 'GitHub']
    SENTINEL_SUFFIX   = '@taskify-test.invalid'
    SENTINEL_PASSWORD = 'Sentinel!Test99'

    results = []

    # ── Result collector ─────────────────────────────────────────────────────
    def check(category, name, fn):
        try:
            ret    = fn()
            status, msg = ret[0], ret[1]
            raw    = ret[2] if len(ret) > 2 else None
            detail = '\n'.join(raw) if isinstance(raw, list) else raw
        except Exception as e:
            status, msg, detail = 'fail', f'{type(e).__name__}: {e}', None
        results.append({'category': category, 'name': name,
                        'status': status, 'message': msg, 'detail': detail})

    # ── Step logger ──────────────────────────────────────────────────────────
    class Steps(list):
        """Auto-numbered step log shared between a test body and its finally."""
        def add(self, msg):
            self.append(f'[{len(self) + 1}] {msg}')
        def fail(self, exc):
            self.append(f'[FAIL] {type(exc).__name__}: {exc}')
        def cleanup(self, msg):
            self.append(f'[cleanup] {msg}')

    # ── Sentinel cleanup helpers ─────────────────────────────────────────────

    def _delete_obj(obj):
        """Delete a single ORM object if it still exists, then commit."""
        if obj is not None:
            db.session.delete(obj)
            db.session.commit()

    # Pre-run purge: walk child tables properly for tickets; use _delete_obj
    # for employees/customers so FK constraints are respected.
    try:
        for t in Ticket.query.filter(Ticket.submitter_email.like(f'%{SENTINEL_SUFFIX}')).all():
            _hard_delete_ticket(t.id)
        for emp in Employee.query.filter(Employee.email.like(f'%{SENTINEL_SUFFIX}')).all():
            _delete_obj(emp)
        for cust in Customer.query.filter(Customer.email.like(f'%{SENTINEL_SUFFIX}')).all():
            _delete_obj(cust)
    except Exception:
        app.logger.warning('admin_tests: pre-run sentinel purge failed', exc_info=True)
        db.session.rollback()

    # ── Functional test fixtures ─────────────────────────────────────────────
    # Each fixture creates one sentinel record, yields it to a body function,
    # then guarantees cleanup in its finally block.  Adding a new functional
    # test only requires writing a body function — no boilerplate to copy.

    def _with_ticket(subject, body_fn):
        """Create a sentinel Ticket, run body_fn(ticket, steps), then delete."""
        steps = Steps()
        tid   = None
        try:
            t = Ticket(submitter_email=f'sentinel{SENTINEL_SUFFIX}',
                       subject=subject, body='Automated test.')
            db.session.add(t)
            db.session.commit()
            tid = t.id
            steps.add(f'INSERT Ticket #{tid}, token={t.token[:8]}…')
            return body_fn(t, steps)
        except Exception as e:
            steps.fail(e)
            return 'fail', f'{type(e).__name__}: {e}', steps
        finally:
            if tid:
                _hard_delete_ticket(tid)
                steps.cleanup(f'Ticket #{tid} and child records deleted')

    def _with_employee(username_prefix, body_fn):
        """Create a sentinel Employee, run body_fn(emp, steps), then delete."""
        steps = Steps()
        eid   = None
        try:
            emp = Employee(username=f'{username_prefix}_{uuid.uuid4().hex[:8]}',
                           email=f'{username_prefix}{SENTINEL_SUFFIX}',
                           is_active=True)
            emp.set_password(SENTINEL_PASSWORD)
            db.session.add(emp)
            db.session.commit()
            eid = emp.id
            steps.add(f'INSERT Employee #{eid} ({emp.username})')
            return body_fn(emp, steps)
        except Exception as e:
            steps.fail(e)
            return 'fail', f'{type(e).__name__}: {e}', steps
        finally:
            if eid:
                _delete_obj(db.session.get(Employee, eid))
                steps.cleanup(f'Employee #{eid} deleted')

    def _with_customer(name_suffix, body_fn):
        """Create a sentinel Customer, run body_fn(cust, steps), then delete."""
        steps = Steps()
        cid   = None
        slug  = name_suffix.lower().replace(' ', '-')
        try:
            cust = Customer(name=f'Test {name_suffix}',
                            email=f'test-{slug}{SENTINEL_SUFFIX}',
                            created_by_id=current_user.id)
            cust.set_password(SENTINEL_PASSWORD)
            db.session.add(cust)
            db.session.commit()
            cid = cust.id
            steps.add(f'INSERT Customer #{cid} ({cust.email})')
            return body_fn(cust, steps)
        except Exception as e:
            steps.fail(e)
            return 'fail', f'{type(e).__name__}: {e}', steps
        finally:
            if cid:
                _delete_obj(db.session.get(Customer, cid))
                steps.cleanup(f'Customer #{cid} deleted')

    # ── Infrastructure checks ────────────────────────────────────────────────

    def db_connection():
        db.session.execute(text('SELECT 1'))
        uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        try:
            p = urllib.parse.urlparse(uri)
            uri_display = uri.replace(p.password, '***') if p.password else uri
        except Exception:
            uri_display = uri
        return 'pass', 'Database connection OK', [
            f'URI:    {uri_display}',
            f'Driver: {db.engine.dialect.name}',
            'Query:  SELECT 1 → OK',
        ]
    check('Database', 'Connection', db_connection)

    def db_employees():
        emps   = Employee.query.order_by(Employee.id).all()
        active = sum(1 for e in emps if e.is_active)
        detail = [f'Total: {len(emps)}  Active: {active}', '']
        for e in emps:
            tags = ((' [admin]'    if e.is_admin   else '')
                  + (' [manager]'  if e.is_manager else '')
                  + (' [inactive]' if not e.is_active else ''))
            detail.append(f'#{e.id}  {e.username:<20} {e.email}{tags}')
        return 'pass', f'{len(emps)} employee(s) — {active} active', detail
    check('Database', 'Employees', db_employees)

    def db_tickets():
        tickets  = Ticket.query.order_by(Ticket.id.desc()).all()
        counts   = Counter(t.status for t in tickets)
        open_    = counts['open']
        in_prog  = counts['in_progress']
        resolved = counts['resolved']
        detail   = [
            f'Total:       {len(tickets)}',
            f'Open:        {open_}',
            f'In progress: {in_prog}',
            f'Resolved:    {resolved}',
            f'Closed:      {counts["closed"]}',
        ]
        if tickets:
            detail += ['', 'Latest 5:']
            for t in tickets[:5]:
                detail.append(f'  #{t.id:<5} [{t.status:<11}] {t.subject[:55]}')
        return 'pass', f'{len(tickets)} total — {open_} open, {in_prog} in progress, {resolved} resolved', detail
    check('Database', 'Tickets', db_tickets)

    def cfg_secret():
        key = app.config.get('SECRET_KEY', '')
        detail = [
            f'Length:      {len(key)} chars',
            f'Is default:  {"YES — insecure!" if key == "dev-secret-change-in-production" else "No"}',
        ]
        if key == 'dev-secret-change-in-production':
            return 'fail', 'SECRET_KEY is the default dev value — change it before going to production', detail
        if len(key) < 24:
            return 'warn', 'SECRET_KEY is set but short (< 24 chars) — consider a longer random key', detail
        return 'pass', 'SECRET_KEY is set and non-default', detail
    check('Configuration', 'Secret key', cfg_secret)

    def cfg_uploads():
        folder   = app.config.get('UPLOAD_FOLDER', 'uploads')
        abs_path = os.path.abspath(folder)
        detail   = [f'Configured: {folder}', f'Absolute:   {abs_path}']
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
                detail.append('Action: directory missing — created now')
                return 'warn', f'Upload folder was missing and has been created: {folder}', detail
            except Exception as e:
                detail.append(f'Error: {e}')
                return 'fail', f'Upload folder missing and could not be created: {e}', detail
        writable = os.access(folder, os.W_OK)
        detail  += [f'Exists:   Yes',
                    f'Writable: {"Yes" if writable else "NO"}',
                    f'Mode:     {oct(os.stat(folder).st_mode)}']
        if not writable:
            return 'fail', f'Upload folder is not writable: {folder}', detail
        return 'pass', f'Upload folder exists and is writable: {folder}', detail
    check('Configuration', 'Upload folder', cfg_uploads)

    def cfg_public_tickets():
        enabled = app.config.get('PUBLIC_TICKETS', True)
        return 'info', f'Public ticket submission is {"enabled" if enabled else "disabled"}', [
            f'PUBLIC_TICKETS: {enabled}',
            '',
            'Enabled:  anyone can submit a ticket without logging in.',
            'Disabled: only authenticated employees can create tickets.',
        ]
    check('Configuration', 'Public tickets', cfg_public_tickets)

    def cfg_app_name():
        name    = app.config.get('APP_NAME', 'Taskify')
        locales = app.config.get('BABEL_SUPPORTED_LOCALES', [])
        return 'info', f'App name: {name}', [
            f'APP_NAME:             {name}',
            f'BABEL_DEFAULT_LOCALE: {app.config.get("BABEL_DEFAULT_LOCALE", "en")}',
            f'Supported locales:    {", ".join(locales) if locales else "(none)"}',
            f'MAX_CONTENT_LENGTH:   {app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)} MB',
        ]
    check('Configuration', 'App name', cfg_app_name)

    def mail_configured():
        cfg    = app.config
        server = cfg.get('MAIL_SERVER')
        detail = [
            f'MAIL_SERVER:         {server or "(not set)"}',
            f'MAIL_PORT:           {cfg.get("MAIL_PORT", "(not set)")}',
            f'MAIL_USE_TLS:        {cfg.get("MAIL_USE_TLS", False)}',
            f'MAIL_USE_SSL:        {cfg.get("MAIL_USE_SSL", False)}',
            f'MAIL_USERNAME:       {cfg.get("MAIL_USERNAME") or "(not set)"}',
            f'MAIL_PASSWORD:       {"*** (set)" if cfg.get("MAIL_PASSWORD") else "(not set)"}',
            f'MAIL_DEFAULT_SENDER: {cfg.get("MAIL_DEFAULT_SENDER") or "(not set)"}',
        ]
        if not server:
            return 'warn', 'MAIL_SERVER is not set — outbound email disabled', detail
        return 'pass', f'Mail server: {server}:{cfg.get("MAIL_PORT", 25)}', detail
    check('Email', 'Configuration', mail_configured)

    def mail_suppress():
        suppressed = app.config.get('MAIL_SUPPRESS_SEND', False)
        detail = [
            f'MAIL_SUPPRESS_SEND: {suppressed}',
            f'TESTING:            {app.config.get("TESTING", False)}',
            '',
            'Emails are suppressed when either flag is True.',
        ]
        if suppressed:
            return 'warn', 'MAIL_SUPPRESS_SEND=True — emails will not actually be sent', detail
        return 'pass', 'Email sending is active (MAIL_SUPPRESS_SEND=False)', detail
    check('Email', 'Sending active', mail_suppress)

    def mail_smtp():
        server = app.config.get('MAIL_SERVER')
        if not server:
            return 'info', 'Skipped — MAIL_SERVER not configured', None
        port    = int(app.config.get('MAIL_PORT', 25))
        use_ssl = app.config.get('MAIL_USE_SSL', False)
        use_tls = app.config.get('MAIL_USE_TLS', False)
        detail  = [f'Host: {server}:{port}']
        smtp    = None
        try:
            if use_ssl:
                detail.append('Mode: SMTP_SSL (implicit TLS)')
                smtp = smtplib.SMTP_SSL(server, port,
                                        context=_ssl.create_default_context(), timeout=5)
            else:
                detail.append('Mode: ' + ('SMTP + STARTTLS' if use_tls else 'Plain SMTP'))
                smtp = smtplib.SMTP(server, port, timeout=5)
            _, ehlo_resp = smtp.ehlo()
            if ehlo_resp:
                detail.append(f'EHLO:  {ehlo_resp.decode(errors="replace").splitlines()[0].strip()}')
            if not use_ssl and use_tls:
                smtp.starttls(context=_ssl.create_default_context())
                smtp.ehlo()
            if smtp.esmtp_features:
                detail.append('ESMTP: ' + ', '.join(smtp.esmtp_features.keys()))
            smtp.quit()
            return 'pass', f'SMTP connection to {server}:{port} succeeded', detail
        finally:
            # Close the socket even if quit() was never reached
            if smtp is not None:
                try:
                    smtp.close()
                except Exception:
                    pass
    check('Email', 'SMTP connectivity', mail_smtp)

    def inbound_config():
        use_graph = bool(app.config.get('AZURE_TENANT_ID') and app.config.get('GRAPH_MAILBOX'))
        use_imap  = bool(app.config.get('IMAP_HOST')) and not use_graph
        if use_graph:
            tid = app.config.get('AZURE_TENANT_ID', '')
            cid = app.config.get('AZURE_CLIENT_ID', '')
            return 'info', f'Microsoft Graph configured for {app.config.get("GRAPH_MAILBOX")}', [
                'Method:         Microsoft Graph API',
                f'Mailbox:        {app.config.get("GRAPH_MAILBOX")}',
                (f'Tenant ID:      {tid[:8]}…' if tid else 'Tenant ID:      (not set)'),
                (f'Client ID:      {cid[:8]}…' if cid else 'Client ID:      (not set)'),
                f'Poll interval:  {app.config.get("GRAPH_POLL_INTERVAL", 60)}s',
            ]
        if use_imap:
            return 'info', f'IMAP configured: {app.config.get("IMAP_HOST")}', [
                'Method:         IMAP',
                f'Host:           {app.config.get("IMAP_HOST")}:{app.config.get("IMAP_PORT", 993)}',
                f'User:           {app.config.get("IMAP_USER") or "(not set)"}',
                f'Use SSL:        {app.config.get("IMAP_USE_SSL", True)}',
                f'Poll interval:  {app.config.get("IMAP_POLL_INTERVAL", 60)}s',
            ]
        return 'warn', 'No inbound email configured (IMAP or Microsoft Graph)', [
            'Neither IMAP nor Microsoft Graph credentials are configured.',
            '',
            'To enable IMAP  set: IMAP_HOST, IMAP_USER, IMAP_PASSWORD',
            'To enable Graph set: AZURE_TENANT_ID, AZURE_CLIENT_ID,',
            '                     AZURE_CLIENT_SECRET, GRAPH_MAILBOX',
        ]
    check('Inbound email', 'Configuration', inbound_config)

    def inbound_thread():
        all_threads = threading.enumerate()
        names  = {t.name for t in all_threads}
        detail = ['Running threads:'] + [f'  {t.name}' for t in all_threads]
        if 'imap-poll' in names:
            return 'pass', 'IMAP polling thread is running', detail
        if 'graph-poll' in names:
            return 'pass', 'Microsoft Graph polling thread is running', detail
        use_graph = bool(app.config.get('AZURE_TENANT_ID') and app.config.get('GRAPH_MAILBOX'))
        use_imap  = bool(app.config.get('IMAP_HOST')) and not use_graph
        if use_graph or use_imap:
            return 'fail', 'Inbound email is configured but the polling thread is not running', detail
        return 'info', 'No inbound email configured — polling not expected', detail
    check('Inbound email', 'Polling thread', inbound_thread)

    def github_config():
        token     = app.config.get('GITHUB_TOKEN')
        client_id = app.config.get('GITHUB_CLIENT_ID')
        detail = [
            f'GITHUB_TOKEN:      {"set (" + str(len(token)) + " chars)" if token else "(not set)"}',
            f'GITHUB_CLIENT_ID:  {client_id or "(not set)"}',
            f'GITHUB_ORG:        {app.config.get("GITHUB_ORG") or "(not set)"}',
        ]
        if not token and not client_id:
            return 'info', 'GitHub integration not configured', detail
        parts = (['API token set'] if token else []) + (['OAuth app configured'] if client_id else [])
        return 'info', ', '.join(parts), detail
    check('GitHub', 'Configuration', github_config)

    def github_api():
        token = app.config.get('GITHUB_TOKEN')
        if not token:
            return 'info', 'Skipped — GITHUB_TOKEN not set', None
        req = urllib.request.Request(
            'https://api.github.com/user',
            headers={'Authorization': f'token {token}', 'User-Agent': 'Taskify'}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        return 'pass', f'GitHub API authenticated as @{data.get("login", "?")}', [
            f'Login:        @{data.get("login")}',
            f'Name:         {data.get("name") or "(not set)"}',
            f'Public repos: {data.get("public_repos", 0)}',
            f'Account type: {data.get("type", "?")}',
        ]
    check('GitHub', 'API connectivity', github_api)

    # ── Functional: Ticket ───────────────────────────────────────────────────

    def func_ticket_create_delete():
        def body(t, steps):
            fetched = db.session.get(Ticket, t.id)
            assert fetched is not None, 'Ticket not found after insert'
            steps.add(f'SELECT Ticket #{t.id} → found')
            assert fetched.status == 'open', f'Expected open, got {fetched.status}'
            steps.add(f'status = {fetched.status!r} ✓')
            return 'pass', f'Ticket #{t.id} created and retrieved (status=open)', steps
        return _with_ticket('[test] Sentinel ticket', body)
    check('Functional: Ticket', 'Create & delete', func_ticket_create_delete)

    def func_ticket_status_change():
        def body(t, steps):
            for new_status in ('in_progress', 'resolved', 'closed'):
                t.status = new_status
                db.session.commit()
                got = db.session.get(Ticket, t.id).status
                assert got == new_status, f'Expected {new_status}, got {got}'
                steps.add(f'UPDATE status → {new_status} ✓')
            return 'pass', f'Ticket #{t.id} transitioned through all statuses', steps
        return _with_ticket('[test] Status change', body)
    check('Functional: Ticket', 'Status transitions', func_ticket_status_change)

    def func_ticket_internal_reply():
        def body(t, steps):
            msg = Message(ticket_id=t.id, employee_id=current_user.id,
                          body='Internal test reply.', is_customer_visible=False)
            db.session.add(msg)
            db.session.commit()
            steps.add(f'INSERT Message #{msg.id} (internal, employee={current_user.username})')
            fetched = db.session.get(Message, msg.id)
            assert fetched is not None and fetched.is_customer_visible is False
            steps.add('is_customer_visible = False ✓')
            return 'pass', f'Internal message #{msg.id} added to ticket #{t.id}', steps
        return _with_ticket('[test] Internal reply', body)
    check('Functional: Ticket', 'Internal reply', func_ticket_internal_reply)

    def func_ticket_customer_visible_reply():
        def body(t, steps):
            msg = Message(ticket_id=t.id, employee_id=current_user.id,
                          body='Customer-visible test reply.', is_customer_visible=True)
            db.session.add(msg)
            db.session.commit()
            steps.add(f'INSERT Message #{msg.id} (customer-visible)')
            fetched = db.session.get(Message, msg.id)
            assert fetched is not None and fetched.is_customer_visible is True
            steps.add('is_customer_visible = True ✓')
            return 'pass', f'Customer-visible message #{msg.id} added to ticket #{t.id}', steps
        return _with_ticket('[test] Customer-visible reply', body)
    check('Functional: Ticket', 'Customer-visible reply', func_ticket_customer_visible_reply)

    def func_ticket_assignment():
        def body(t, steps):
            a = Assignment(ticket_id=t.id, employee_id=current_user.id)
            db.session.add(a)
            db.session.commit()
            steps.add(f'INSERT Assignment #{a.id} → employee={current_user.username}')
            refreshed = db.session.get(Ticket, t.id)
            assert refreshed.assignee is not None
            assert refreshed.assignee.id == current_user.id
            steps.add(f'ticket.assignee = {refreshed.assignee.username} ✓')
            return 'pass', f'Ticket #{t.id} assigned to {current_user.username}', steps
        return _with_ticket('[test] Assignment', body)
    check('Functional: Ticket', 'Assignment', func_ticket_assignment)

    def func_ticket_watch():
        def body(t, steps):
            w = TicketWatch(ticket_id=t.id, employee_id=current_user.id)
            db.session.add(w)
            db.session.commit()
            steps.add(f'INSERT TicketWatch #{w.id} → employee={current_user.username}')
            count = TicketWatch.query.filter_by(ticket_id=t.id).count()
            assert count == 1, f'Expected 1, got {count}'
            steps.add(f'COUNT watches = {count} ✓')
            return 'pass', f'Ticket #{t.id} watched by {current_user.username}', steps
        return _with_ticket('[test] Watch', body)
    check('Functional: Ticket', 'Watch', func_ticket_watch)

    def func_ticket_audit_event():
        def body(t, steps):
            ev = TicketEvent(ticket_id=t.id, employee_id=current_user.id,
                             event_type='status', from_value='open', to_value='in_progress')
            db.session.add(ev)
            db.session.commit()
            steps.add(f'INSERT TicketEvent #{ev.id} (status: open→in_progress)')
            count = TicketEvent.query.filter_by(ticket_id=t.id).count()
            assert count == 1, f'Expected 1, got {count}'
            steps.add(f'COUNT events = {count} ✓')
            return 'pass', f'Audit event logged for ticket #{t.id}', steps
        return _with_ticket('[test] Audit event', body)
    check('Functional: Ticket', 'Audit event', func_ticket_audit_event)

    def func_ticket_delete_cascade():
        def body(t, steps):
            # Build one of every child record type.
            msg = Message(ticket_id=t.id, employee_id=current_user.id,
                          body='Cascade test.', is_customer_visible=False)
            db.session.add(msg)
            db.session.flush()
            att = Attachment(ticket_id=t.id, message_id=msg.id,
                             filename='sentinel.txt', original_filename='sentinel.txt', size=4)
            db.session.add(att)
            db.session.add(TicketEvent(ticket_id=t.id, employee_id=current_user.id,
                                       event_type='status', from_value='open', to_value='in_progress'))
            db.session.add(TicketWatch(ticket_id=t.id, employee_id=current_user.id))
            db.session.add(Assignment(ticket_id=t.id, employee_id=current_user.id))
            db.session.commit()
            steps.add(f'INSERT message #{msg.id}, attachment #{att.id}, event, watch, assignment')

            # Create a real upload directory with a dummy file.
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(t.id))
            os.makedirs(upload_dir, exist_ok=True)
            with open(os.path.join(upload_dir, 'sentinel.txt'), 'w'):
                pass
            steps.add(f'Created upload dir with dummy file')

            # Call the same helper used by the delete_ticket route.
            _hard_delete_ticket(t.id)
            steps.add('_hard_delete_ticket() completed')

            # Verify DB.
            assert db.session.get(Ticket, t.id) is None
            steps.add('Ticket not in DB ✓')
            assert Message.query.filter_by(ticket_id=t.id).count() == 0
            steps.add('Messages deleted ✓')
            assert Attachment.query.filter_by(ticket_id=t.id).count() == 0
            steps.add('Attachments deleted ✓')
            assert TicketEvent.query.filter_by(ticket_id=t.id).count() == 0
            steps.add('Events deleted ✓')
            assert TicketWatch.query.filter_by(ticket_id=t.id).count() == 0
            steps.add('Watches deleted ✓')
            assert Assignment.query.filter_by(ticket_id=t.id).count() == 0
            steps.add('Assignment deleted ✓')

            # Verify disk.
            assert not os.path.exists(upload_dir)
            steps.add('Upload folder absent on disk ✓')

            return 'pass', f'Ticket #{t.id} and all child records deleted cleanly', steps
        return _with_ticket('[test] Delete cascade', body)
    check('Functional: Ticket', 'Delete (cascade + upload cleanup)', func_ticket_delete_cascade)

    # ── Functional: Employee ─────────────────────────────────────────────────

    def func_employee_create_delete():
        def body(emp, steps):
            fetched = db.session.get(Employee, emp.id)
            assert fetched is not None
            steps.add(f'SELECT Employee #{emp.id} → found')
            assert fetched.check_password(SENTINEL_PASSWORD), 'Password check failed'
            steps.add(f'check_password(sentinel) = True ✓')
            return 'pass', f'Employee #{emp.id} created, password verified', steps
        return _with_employee('test-create', body)
    check('Functional: Employee', 'Create & delete', func_employee_create_delete)

    def func_employee_toggle_active():
        def body(emp, steps):
            emp.is_active = False
            db.session.commit()
            assert db.session.get(Employee, emp.id).is_active is False
            steps.add('UPDATE is_active=False ✓')
            emp.is_active = True
            db.session.commit()
            assert db.session.get(Employee, emp.id).is_active is True
            steps.add('UPDATE is_active=True ✓')
            return 'pass', f'Employee #{emp.id} deactivated and reactivated', steps
        return _with_employee('test-toggle', body)
    check('Functional: Employee', 'Toggle active', func_employee_toggle_active)

    def func_employee_password_change():
        def body(emp, steps):
            assert emp.check_password(SENTINEL_PASSWORD)
            steps.add('check_password(initial) = True ✓')
            emp.set_password('NewPass!99')
            db.session.commit()
            steps.add('set_password(new) committed')
            refreshed = db.session.get(Employee, emp.id)
            assert refreshed.check_password('NewPass!99')
            steps.add('check_password(new) = True ✓')
            assert not refreshed.check_password(SENTINEL_PASSWORD)
            steps.add('check_password(initial) = False ✓ (old hash rejected)')
            return 'pass', f'Employee #{emp.id} password updated and old one rejected', steps
        return _with_employee('test-pwchange', body)
    check('Functional: Employee', 'Password change', func_employee_password_change)

    # ── Functional: Customer ─────────────────────────────────────────────────

    def func_customer_create_delete():
        def body(cust, steps):
            fetched = db.session.get(Customer, cust.id)
            assert fetched is not None
            steps.add(f'SELECT Customer #{cust.id} → found')
            assert fetched.check_password(SENTINEL_PASSWORD), 'Password check failed'
            steps.add('check_password(sentinel) = True ✓')
            return 'pass', f'Customer #{cust.id} created, password verified', steps
        return _with_customer('Customer Sentinel', body)
    check('Functional: Customer', 'Create & delete', func_customer_create_delete)

    def func_customer_group_membership():
        def body(cust, steps):
            grp_name = f'test-group-{uuid.uuid4().hex[:8]}'
            grp = Group(name=grp_name)
            db.session.add(grp)
            db.session.commit()
            gid = grp.id
            steps.add(f'INSERT Group #{gid} ({grp_name!r})')
            try:
                cust.groups.append(grp)
                db.session.commit()
                assert any(g.id == gid for g in db.session.get(Customer, cust.id).groups)
                steps.add('customer.groups.append(group) → group found ✓')
                cust.groups.remove(db.session.get(Group, gid))
                db.session.commit()
                assert len(db.session.get(Customer, cust.id).groups) == 0
                steps.add('customer.groups.remove(group) → groups empty ✓')
                return 'pass', f'Customer #{cust.id} added to / removed from group "{grp_name}"', steps
            finally:
                _delete_obj(db.session.get(Group, gid))
                steps.cleanup(f'Group #{gid} deleted')
        return _with_customer('Group Customer', body)
    check('Functional: Customer', 'Group membership', func_customer_group_membership)

    def func_customer_toggle_active():
        def body(cust, steps):
            cust.is_active = False
            db.session.commit()
            assert db.session.get(Customer, cust.id).is_active is False
            steps.add('UPDATE is_active=False ✓')
            cust.is_active = True
            db.session.commit()
            assert db.session.get(Customer, cust.id).is_active is True
            steps.add('UPDATE is_active=True ✓')
            return 'pass', f'Customer #{cust.id} deactivated and reactivated', steps
        return _with_customer('Toggle Customer', body)
    check('Functional: Customer', 'Toggle active', func_customer_toggle_active)

    counts = {}
    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1

    return render_template('admin/tests.html', results=results, counts=counts,
                           infra_categories=INFRA_CATEGORIES)


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


def _manual_path(stem):
    """Return the best-matching manual file path for the current locale."""
    locale = str(get_locale())
    localized = os.path.join(app.root_path, 'docs', f'{stem}.{locale}.md')
    if os.path.exists(localized):
        return localized
    return os.path.join(app.root_path, 'docs', f'{stem}.md')


MANUALS = [
    ('manual-employee', _l('Employee Manual')),
    ('manual-manager',  _l('Manager Manual')),
    ('manual-admin',    _l('Admin Manual')),
    ('manual-customers', _l('Customer Manual')),
]


# ---------------------------------------------------------------------------
# Admin – MantisBT synchronisation
# ---------------------------------------------------------------------------

_MANTIS_STATUS_LABEL = {
    10: 'new', 20: 'feedback', 30: 'acknowledged',
    40: 'confirmed', 50: 'assigned', 80: 'resolved', 90: 'closed',
}

_MANTIS_STATUS_MAP = {
    10: 'open',         # new
    20: 'open',         # feedback
    30: 'open',         # acknowledged
    40: 'open',         # confirmed
    50: 'in_progress',  # assigned
    80: 'resolved',     # resolved
    90: 'closed',       # closed
}

# Maps MantisBT access_level → (target_type, is_manager)
# target_type: 'customer' | 'employee'
_MANTIS_ROLE_MAP = {
    10: ('customer', False),   # Betrachter     → Kunde
    25: ('customer', False),   # Melder         → Kunde
    40: ('customer', False),   # Aktualisierer  → Kunde
    55: ('employee', False),   # Entwickler     → Mitarbeiter
    70: ('employee', True),    # Projektleiter  → Manager
}


def _mantis_wiki_to_html(text: str) -> str:
    """Convert MantisBT wiki markup to HTML suitable for Quill/storage."""
    import re, html as _html
    if not text:
        return ''

    # 1. HTML-escape everything first so inline patterns are safe
    t = _html.escape(text, quote=False)

    # 2. Block-level: code blocks  {code}...{code} or {code:lang}...{code}
    t = re.sub(
        r'\{code(?::[^}]*)?\}(.*?)\{code\}',
        lambda m: '<pre><code>' + m.group(1).strip() + '</code></pre>',
        t, flags=re.DOTALL | re.IGNORECASE)

    # 3. Block-level: quote blocks  {quote}...{/quote}  or  {quote}...{quote}
    t = re.sub(
        r'\{quote\}(.*?)\{/?quote\}',
        lambda m: '<blockquote>' + m.group(1).strip() + '</blockquote>',
        t, flags=re.DOTALL | re.IGNORECASE)

    # 4. Process remaining text line by line for headings, lists, hr
    raw_lines = t.split('\n')
    out_lines  = []
    ul_open    = False
    ol_open    = False

    def close_lists():
        nonlocal ul_open, ol_open
        buf = ''
        if ul_open: buf += '</ul>'; ul_open = False
        if ol_open: buf += '</ol>'; ol_open = False
        return buf

    for line in raw_lines:
        # Skip lines already wrapped by block handlers
        if re.match(r'\s*<(pre|blockquote)', line):
            out_lines.append(close_lists() + line)
            continue

        # Headings  = H1 =  == H2 ==  up to ======
        m = re.match(r'^(={1,6})\s+(.+?)\s+\1\s*$', line)
        if m:
            lvl = len(m.group(1))
            out_lines.append(close_lists() + f'<h{lvl}>{m.group(2)}</h{lvl}>')
            continue

        # Horizontal rule  ----
        if re.match(r'^-{4,}\s*$', line):
            out_lines.append(close_lists() + '<hr>')
            continue

        # Unordered list  * item  ** nested (flattened to single level)
        m = re.match(r'^\*+\s+(.+)$', line)
        if m:
            if not ul_open:
                if ol_open: out_lines.append('</ol>'); ol_open = False
                out_lines.append('<ul>'); ul_open = True
            out_lines.append(f'<li>{m.group(1)}</li>')
            continue

        # Ordered list  # item  ## nested
        m = re.match(r'^#+\s+(.+)$', line)
        if m:
            if not ol_open:
                if ul_open: out_lines.append('</ul>'); ul_open = False
                out_lines.append('<ol>'); ol_open = True
            out_lines.append(f'<li>{m.group(1)}</li>')
            continue

        out_lines.append(close_lists() + line)

    out_lines.append(close_lists())
    t = '\n'.join(out_lines)

    # 5. Inline: bold '''text'''
    t = re.sub(r"'''(.+?)'''", r'<strong>\1</strong>', t, flags=re.DOTALL)
    # 6. Inline: italic ''text''
    t = re.sub(r"''(.+?)''",   r'<em>\1</em>',         t, flags=re.DOTALL)
    # 7. Inline: underline __text__
    t = re.sub(r'__(.+?)__',   r'<u>\1</u>',           t, flags=re.DOTALL)
    # 8. Inline: monospace @text@
    t = re.sub(r'@(.+?)@',     r'<code>\1</code>',     t)
    # 9. Inline: strikethrough --text-- (requires non-space at edges to avoid em-dashes)
    t = re.sub(r'--(\S.*?\S)--', r'<del>\1</del>',     t)
    # 10. Named links [[URL|label]] then bare [[URL]]
    t = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'<a href="\1">\2</a>', t)
    t = re.sub(r'\[\[([^\]]+)\]\]',             r'<a href="\1">\1</a>', t)
    # 11. Auto-link bare URLs (not already inside href="...")
    t = re.sub(r'(?<!href=")(https?://[^\s<>"]+)', r'<a href="\1">\1</a>', t)

    # 12. Wrap remaining non-block lines in paragraphs
    block_tags = ('pre', 'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3',
                  'h4', 'h5', 'h6', 'hr', 'div')
    parts = []
    for para in re.split(r'\n{2,}', t):
        para = para.strip()
        if not para:
            continue
        if any(para.startswith(f'<{tag}') or para.startswith(f'</{tag}')
               for tag in block_tags):
            parts.append(para)
        else:
            parts.append('<p>' + para.replace('\n', '<br>') + '</p>')
    return '\n'.join(parts) or '<p></p>'


# Keep the old name as an alias so nothing else breaks
_plain_to_html = _mantis_wiki_to_html


def _save_mantis_attachment(ticket_id, message_id, att_row, mantis_upload_path=None):
    """Write a Mantis attachment to Taskify's upload folder and create an Attachment record.

    When mantis_upload_path is given, reads from the Mantis upload folder on
    disk using the diskfile column (standard disk-storage mode).
    Otherwise falls back to the DB-stored content blob.
    Returns True on success, False when the file cannot be obtained.
    """
    diskfile = getattr(att_row, 'diskfile', None) or ''
    folder   = getattr(att_row, 'folder',   None) or ''
    basename = os.path.basename(diskfile) or diskfile

    candidates = []
    # 1. Original Mantis path from DB columns (works when on the same server)
    if folder and diskfile:
        candidates.append(os.path.join(folder, diskfile))
    # 2. Provided upload path + bare filename
    if mantis_upload_path and basename:
        candidates.append(os.path.join(mantis_upload_path, basename))
    # 3. Provided upload path + full diskfile (in case it carries subdirs)
    if mantis_upload_path and diskfile and diskfile != basename:
        candidates.append(os.path.join(mantis_upload_path, diskfile))
    # 4. diskfile as-is when it is an absolute path
    if os.path.isabs(diskfile):
        candidates.append(diskfile)

    raw = None
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, 'rb') as fh:
                    raw = fh.read()
            except PermissionError:
                app.logger.warning('Permission denied reading Mantis attachment: %s', path)
                return False
            break

    # Fall back to DB-stored content (database-storage mode)
    if not raw:
        blob = att_row.content
        if isinstance(blob, memoryview):
            raw = blob.tobytes()
        elif blob and isinstance(blob, (bytes, bytearray)):
            raw = blob

    if not raw:
        return False

    ext = os.path.splitext(att_row.filename or '')[1]
    disk_name = f'{uuid.uuid4().hex}{ext}'
    upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket_id))
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, disk_name), 'wb') as fh:
        fh.write(raw)
    attachment = Attachment(
        ticket_id=ticket_id,
        message_id=message_id,
        filename=disk_name,
        original_filename=att_row.filename or att_row.title or disk_name,
        size=len(raw),
    )
    if att_row.date_added:
        attachment.created_at = datetime.utcfromtimestamp(att_row.date_added)
    db.session.add(attachment)
    db.session.flush()
    return True


def _mantis_engine(host, port, dbname, user, password):
    """Create a disposable SQLAlchemy engine for a MantisBT MySQL database."""
    from sqlalchemy import create_engine
    import urllib.parse
    pw  = urllib.parse.quote_plus(password)
    usr = urllib.parse.quote_plus(user)
    return create_engine(
        f'mysql+pymysql://{usr}:{pw}@{host}:{port}/{dbname}',
        connect_args={'connect_timeout': 5},
        pool_pre_ping=True,
    )


@app.route('/admin/mantis-sync')
@login_required
@admin_required
def admin_mantis_sync():
    cfg  = app.config
    conn = {
        'host':        cfg.get('MANTIS_DB_HOST', ''),
        'port':        str(cfg.get('MANTIS_DB_PORT', 3306)),
        'dbname':      cfg.get('MANTIS_DB_NAME', 'bugtracker'),
        'user':        cfg.get('MANTIS_DB_USER', ''),
        'password':    cfg.get('MANTIS_DB_PASS', ''),
        'prefix':      cfg.get('MANTIS_TABLE_PREFIX', 'mantis_'),
        'upload_path': cfg.get('MANTIS_UPLOAD_PATH', ''),
    }
    revert_counts = {
        'tickets':   Ticket.query.filter(Ticket.internal_title.like('%[mantis:%')).count(),
        'customers': Customer.query.filter_by(mantis_imported=True).count(),
        'employees': Employee.query.filter_by(mantis_imported=True).count(),
    }
    return render_template('admin/mantis_sync.html', conn=conn, revert_counts=revert_counts)


@app.route('/admin/mantis-sync/preview', methods=['POST'])
@login_required
@admin_required
def admin_mantis_preview():
    from sqlalchemy import text as _sa_text

    host     = request.form.get('host', '').strip()
    port     = request.form.get('port', '3306').strip() or '3306'
    dbname   = request.form.get('dbname', '').strip()
    user     = request.form.get('user', '').strip()
    password = request.form.get('password', '')
    prefix   = request.form.get('prefix', 'mantis_').strip() or 'mantis_'

    if not host or not dbname or not user:
        return jsonify({'error': 'Host, Datenbankname und Benutzer sind erforderlich.'}), 400

    try:
        engine = _mantis_engine(host, port, dbname, user, password)
        p = prefix

        with engine.connect() as con:
            projects = [dict(r._mapping) for r in con.execute(_sa_text(
                f"SELECT id, name FROM {p}project_table "
                f"WHERE enabled=1 ORDER BY name"
            ))]

            users = [dict(r._mapping) for r in con.execute(_sa_text(
                f"SELECT id, username, realname, email, access_level "
                f"FROM {p}user_table "
                f"WHERE access_level < 90 AND enabled = 1 "
                f"ORDER BY username"
            ))]

            memberships = [dict(r._mapping) for r in con.execute(_sa_text(
                f"SELECT user_id, project_id FROM {p}project_user_list_table"
            ))]

            bugs = [dict(r._mapping) for r in con.execute(_sa_text(
                f"SELECT b.id, b.project_id, b.summary, b.status, b.date_submitted, "
                f"u.email AS reporter_email, u.username AS reporter_username, "
                f"h.username AS handler_username, h.realname AS handler_realname "
                f"FROM {p}bug_table b "
                f"LEFT JOIN {p}user_table u ON u.id = b.reporter_id "
                f"LEFT JOIN {p}user_table h ON h.id = b.handler_id "
                f"ORDER BY b.id ASC"
            ))]

        engine.dispose()

        # Build user → project_ids map
        user_proj: dict = {}
        for m in memberships:
            user_proj.setdefault(m['user_id'], []).append(m['project_id'])
        for u in users:
            u['project_ids'] = user_proj.get(u['id'], [])

        # Annotate bugs with project name
        proj_map = {row['id']: row['name'] for row in projects}
        for b in bugs:
            b['project_name'] = proj_map.get(b['project_id'], '?')
            b['date_submitted'] = b.get('date_submitted') or 0

        # Mark projects that already exist in Taskify
        existing_names = {
            g.name for g in Group.query.filter(
                Group.name.in_([p['name'] for p in projects])
            ).all()
        } if projects else set()
        for p in projects:
            p['existing'] = p['name'] in existing_names

        # Mark users already in Taskify (by email)
        import re as _re
        all_emp_emails  = {e.email.lower() for e in Employee.query.with_entities(Employee.email).all()}
        all_cust_emails = {c.email.lower() for c in Customer.query.with_entities(Customer.email).all()}
        existing_emails = all_emp_emails | all_cust_emails
        for u in users:
            u['exists_in_taskify'] = (u.get('email') or '').lower() in existing_emails

        # Mark bugs already imported into Taskify (by mantis ID in internal_title)
        existing_mantis_ids = set()
        for (title,) in db.session.query(Ticket.internal_title).filter(
            Ticket.internal_title.like('[mantis:%')
        ).all():
            m = _re.match(r'\[mantis:(\d+)\]', title or '')
            if m:
                existing_mantis_ids.add(int(m.group(1)))
        for b in bugs:
            b['exists_in_taskify'] = b['id'] in existing_mantis_ids

        return jsonify({'projects': projects, 'users': users, 'bugs': bugs})

    except Exception as e:
        app.logger.exception('MantisBT preview failed')
        return jsonify({'error': str(e)}), 500


# ── Background sync task store ────────────────────────────────────────────────
_sync_tasks: dict = {}


def _do_mantis_sync(flask_app, task: dict, host_url: str) -> None:
    """Run MantisBT sync in a background thread, writing progress to *task*."""
    from sqlalchemy import text as _sa_text

    def log(msg: str, level: str = 'info') -> None:
        task['log'].append({
            'level': level,
            'msg': msg,
            'time': datetime.utcnow().strftime('%H:%M:%S'),
        })

    with flask_app.test_request_context(base_url=host_url):
        try:
            pr       = task['params']
            host     = pr['host']
            port     = pr['port']
            dbname   = pr['dbname']
            user     = pr['user']
            password = pr['password']
            prefix   = pr['prefix']
            upload_path     = pr.get('upload_path') or None
            dry_run         = pr['dry_run']
            sel_project_ids = pr['sel_project_ids']
            sel_user_ids    = pr['sel_user_ids']
            sel_bug_ids     = pr['sel_bug_ids']
            creator_id      = pr['user_id']
            p = prefix
            stats = task['stats']

            log(f'Verbinde mit {host}:{port}/{dbname}…')
            engine = _mantis_engine(host, port, dbname, user, password)

            with engine.connect() as mcon:

                # 1. Projects
                group_map: dict = {}
                new_project_ids: set = set()
                if sel_project_ids:
                    log(f'Importiere {len(sel_project_ids)} Projekt(e)…')
                    id_list = ','.join(str(i) for i in sel_project_ids)
                    for row in mcon.execute(_sa_text(
                        f"SELECT id, name FROM {p}project_table WHERE id IN ({id_list})"
                    )).fetchall():
                        grp = Group.query.filter_by(name=row.name).first()
                        if not grp:
                            grp = Group(name=row.name)
                            db.session.add(grp)
                            db.session.flush()
                            stats['groups'] += 1
                            new_project_ids.add(row.id)
                        group_map[row.id] = grp

                # 2. Users
                if sel_user_ids:
                    log(f'Importiere {len(sel_user_ids)} Benutzer…')
                    id_list = ','.join(str(i) for i in sel_user_ids)
                    user_rows = mcon.execute(_sa_text(
                        f"SELECT id, username, realname, email, access_level "
                        f"FROM {p}user_table WHERE id IN ({id_list})"
                    )).fetchall()
                    memb_rows = mcon.execute(_sa_text(
                        f"SELECT user_id, project_id FROM {p}project_user_list_table "
                        f"WHERE user_id IN ({id_list})"
                    )).fetchall()
                    user_proj_map: dict = {}
                    for m in memb_rows:
                        user_proj_map.setdefault(m.user_id, []).append(m.project_id)

                    for row in user_rows:
                        target_type, is_manager = _MANTIS_ROLE_MAP.get(
                            row.access_level, ('customer', False)
                        )
                        display_name = (row.realname or '').strip() or row.username
                        if target_type == 'employee':
                            if Employee.query.filter_by(email=row.email).first():
                                stats['employees_skipped'] += 1
                            else:
                                emp = Employee(
                                    username=display_name, email=row.email,
                                    is_manager=is_manager, is_active=True,
                                    mantis_imported=True,
                                )
                                emp.set_password(secrets.token_hex(32))
                                db.session.add(emp)
                                db.session.flush()
                                if not dry_run:
                                    token     = _make_setup_token(emp)
                                    setup_url = url_for('setup_password', token=token, _external=True)
                                    send_setup_email(emp.email, emp.username, setup_url)
                                stats['employees'] += 1
                                log(f'Mitarbeiter importiert: {display_name}')
                        else:
                            existing_cust = Customer.query.filter_by(email=row.email).first()
                            if existing_cust:
                                cust = existing_cust
                                stats['customers_skipped'] += 1
                            else:
                                cust = Customer(
                                    email=row.email, name=display_name,
                                    created_by_id=creator_id, mantis_imported=True,
                                )
                                cust.set_password(secrets.token_hex(32))
                                db.session.add(cust)
                                db.session.flush()
                                stats['customers'] += 1
                                log(f'Kunde importiert: {display_name}')
                            for pid in user_proj_map.get(row.id, []):
                                if pid in new_project_ids and pid in group_map \
                                        and group_map[pid] not in cust.groups:
                                    cust.groups.append(group_map[pid])

                # 3. Tickets
                if sel_bug_ids:
                    log(f'Lade {len(sel_bug_ids)} Ticket(e) aus MantisBT…')
                    id_list = ','.join(str(i) for i in sel_bug_ids)
                    bug_rows = mcon.execute(_sa_text(
                        f"SELECT b.id, b.project_id, b.summary, b.status, "
                        f"b.date_submitted, "
                        f"u.email AS reporter_email, "
                        f"h.email AS handler_email, h.username AS handler_username, "
                        f"bt.description "
                        f"FROM {p}bug_table b "
                        f"JOIN {p}bug_text_table bt ON bt.id = b.bug_text_id "
                        f"LEFT JOIN {p}user_table u ON u.id = b.reporter_id "
                        f"LEFT JOIN {p}user_table h ON h.id = b.handler_id "
                        f"WHERE b.id IN ({id_list})"
                    )).fetchall()

                    unmapped_pids = {r.project_id for r in bug_rows} - set(group_map.keys())
                    if unmapped_pids:
                        pid_csv = ','.join(str(i) for i in unmapped_pids)
                        for proj in mcon.execute(_sa_text(
                            f"SELECT id, name FROM {p}project_table WHERE id IN ({pid_csv})"
                        )).fetchall():
                            existing_grp = Group.query.filter_by(name=proj.name).first()
                            if existing_grp:
                                group_map[proj.id] = existing_grp

                    task['total_bugs'] = len(bug_rows)
                    log(f'Importiere {len(bug_rows)} Ticket(e)…')

                    for idx, row in enumerate(bug_rows):
                        task['bugs_done'] = idx + 1
                        if Ticket.query.filter(
                            Ticket.internal_title.like(f'%[mantis:{row.id}]%')
                        ).first():
                            stats['tickets_skipped'] += 1
                            continue

                        log(f'#{row.id}: {row.summary[:70]}')
                        ticket = Ticket(
                            submitter_email=row.reporter_email or 'mantis-import@taskify.local',
                            subject=row.summary,
                            body=_plain_to_html(row.description),
                            status=_MANTIS_STATUS_MAP.get(row.status, 'open'),
                            internal_title=f'[mantis:{row.id}] {row.summary}',
                            group_id=group_map[row.project_id].id if row.project_id in group_map else None,
                        )
                        if row.date_submitted:
                            ts = datetime.utcfromtimestamp(row.date_submitted)
                            ticket.created_at = ts
                            ticket.updated_at = ts
                        db.session.add(ticket)
                        db.session.flush()
                        stats['tickets'] += 1

                        if not dry_run:
                            os.makedirs(os.path.join(
                                flask_app.config['UPLOAD_FOLDER'], str(ticket.id)
                            ), exist_ok=True)

                        if row.handler_email:
                            handler_emp = Employee.query.filter(
                                Employee.email.ilike(row.handler_email)
                            ).first()
                            if handler_emp:
                                db.session.add(Assignment(
                                    ticket_id=ticket.id, employee_id=handler_emp.id,
                                ))

                        note_rows = mcon.execute(_sa_text(
                            f"SELECT bn.id, bn.date_submitted, bn.view_state, "
                            f"u.email AS reporter_email, bnt.note "
                            f"FROM {p}bugnote_table bn "
                            f"JOIN {p}bugnote_text_table bnt ON bnt.id = bn.bugnote_text_id "
                            f"LEFT JOIN {p}user_table u ON u.id = bn.reporter_id "
                            f"WHERE bn.bug_id = :bid ORDER BY bn.date_submitted"
                        ), {'bid': row.id}).fetchall()

                        for note in note_rows:
                            note_body = _plain_to_html(note.note)
                            if not note_body:
                                continue
                            is_public = (note.view_state == 10)
                            emp = cust_author = None
                            if note.reporter_email:
                                emp = Employee.query.filter(
                                    Employee.email.ilike(note.reporter_email)
                                ).first()
                                if not emp:
                                    cust_author = Customer.query.filter(
                                        Customer.email.ilike(note.reporter_email)
                                    ).first()
                            msg = Message(
                                ticket_id=ticket.id,
                                employee_id=emp.id if emp else None,
                                body=note_body,
                                is_customer_visible=True if cust_author else is_public,
                                is_customer_reply=bool(cust_author),
                            )
                            if note.date_submitted:
                                msg.created_at = datetime.utcfromtimestamp(note.date_submitted)
                            db.session.add(msg)
                            db.session.flush()
                            stats['notes'] += 1

                            if not dry_run:
                                att_rows = mcon.execute(_sa_text(
                                    f"SELECT title, filename, diskfile, folder, content, date_added "
                                    f"FROM {p}bug_file_table "
                                    f"WHERE bug_id = :bid AND bugnote_id = :nid"
                                ), {'bid': row.id, 'nid': note.id}).fetchall()
                                for att in att_rows:
                                    if _save_mantis_attachment(ticket.id, msg.id, att, upload_path):
                                        stats['attachments'] += 1
                                    else:
                                        stats['attachments_skipped'] += 1

                        if not dry_run:
                            att_rows = mcon.execute(_sa_text(
                                f"SELECT title, filename, diskfile, folder, content, date_added "
                                f"FROM {p}bug_file_table "
                                f"WHERE bug_id = :bid AND (bugnote_id = 0 OR bugnote_id IS NULL)"
                            ), {'bid': row.id}).fetchall()
                            for att in att_rows:
                                if _save_mantis_attachment(ticket.id, None, att, upload_path):
                                    stats['attachments'] += 1
                                else:
                                    stats['attachments_skipped'] += 1

                        hist_rows = mcon.execute(_sa_text(
                            f"SELECT bh.date_modified, bh.field_name, bh.old_value, "
                            f"bh.new_value, bh.type, u.email AS user_email "
                            f"FROM {p}bug_history_table bh "
                            f"LEFT JOIN {p}user_table u ON u.id = bh.user_id "
                            f"WHERE bh.bug_id = :bid AND bh.type = 0 "
                            f"ORDER BY bh.date_modified"
                        ), {'bid': row.id}).fetchall()

                        for h in hist_rows:
                            actor = (Employee.query.filter(Employee.email.ilike(h.user_email)).first()
                                     if h.user_email else None)
                            if h.field_name == 'status':
                                try:
                                    from_val = _MANTIS_STATUS_LABEL.get(int(h.old_value), h.old_value)
                                    to_val   = _MANTIS_STATUS_LABEL.get(int(h.new_value), h.new_value)
                                except (ValueError, TypeError):
                                    from_val, to_val = h.old_value, h.new_value
                                ev_type = 'status'
                            elif h.field_name == 'assigned_to':
                                ev_type  = 'assignment'
                                from_val = h.old_value
                                to_val   = h.new_value
                            else:
                                ev_type  = 'mantis_history'
                                from_val = f'{h.field_name}: {h.old_value}' if h.old_value else h.field_name
                                to_val   = h.new_value
                            ev = TicketEvent(
                                ticket_id=ticket.id,
                                employee_id=actor.id if actor else None,
                                event_type=ev_type,
                                from_value=str(from_val)[:500] if from_val else None,
                                to_value=str(to_val)[:500]     if to_val   else None,
                            )
                            if h.date_modified:
                                ev.created_at = datetime.utcfromtimestamp(h.date_modified)
                            db.session.add(ev)
                            stats['history'] += 1

            if dry_run:
                db.session.rollback()
                log('Testlauf abgeschlossen – keine Änderungen gespeichert.', 'warning')
            else:
                db.session.commit()
                log('Synchronisation abgeschlossen.', 'info')

            engine.dispose()
            task['status'] = 'done'

        except Exception as e:
            db.session.rollback()
            flask_app.logger.exception('MantisBT sync failed')
            log(f'Fehler: {e}', 'error')
            task['status'] = 'error'
            task['error'] = str(e)


@app.route('/admin/mantis-sync/execute', methods=['POST'])
@login_required
@admin_required
def admin_mantis_execute():
    host     = request.form.get('host', '').strip()
    port     = request.form.get('port', '3306').strip() or '3306'
    dbname   = request.form.get('dbname', '').strip()
    user     = request.form.get('user', '').strip()
    password = request.form.get('password', '')
    prefix      = request.form.get('prefix', 'mantis_').strip() or 'mantis_'
    upload_path = request.form.get('upload_path', '').strip() or None
    dry_run     = request.form.get('dry_run') == '1'

    sel_project_ids = {int(x) for x in request.form.getlist('project_ids') if x.isdigit()}
    sel_user_ids    = {int(x) for x in request.form.getlist('user_ids')    if x.isdigit()}
    sel_bug_ids     = {int(x) for x in request.form.getlist('bug_ids')     if x.isdigit()}

    if not host or not dbname or not user:
        return jsonify({'error': 'Host, Datenbankname und Benutzer sind erforderlich.'}), 400
    if not sel_project_ids and not sel_user_ids and not sel_bug_ids:
        return jsonify({'error': 'Nichts zur Synchronisation ausgewählt.'}), 400

    task_id = str(uuid.uuid4())
    task = {
        'status': 'running',
        'log': [],
        'stats': dict(groups=0, customers=0, customers_skipped=0,
                      employees=0, employees_skipped=0,
                      tickets=0, tickets_skipped=0,
                      notes=0, attachments=0, attachments_skipped=0, history=0),
        'dry_run': dry_run,
        'total_bugs': len(sel_bug_ids),
        'bugs_done': 0,
        'error': None,
        'params': {
            'host': host, 'port': port, 'dbname': dbname,
            'user': user, 'password': password, 'prefix': prefix,
            'upload_path': upload_path, 'dry_run': dry_run,
            'sel_project_ids': sel_project_ids,
            'sel_user_ids': sel_user_ids,
            'sel_bug_ids': sel_bug_ids,
            'user_id': current_user.id,
        },
    }
    _sync_tasks[task_id] = task
    host_url = request.host_url
    threading.Thread(
        target=_do_mantis_sync,
        args=(app, task, host_url),
        daemon=True,
    ).start()
    return jsonify({'task_id': task_id})


@app.route('/admin/mantis-sync/task/<task_id>')
@login_required
@admin_required
def admin_mantis_task_status(task_id):
    task = _sync_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify({
        'status':     task['status'],
        'log':        task['log'],
        'stats':      task['stats'],
        'dry_run':    task['dry_run'],
        'total_bugs': task.get('total_bugs', 0),
        'bugs_done':  task.get('bugs_done', 0),
        'error':      task.get('error'),
    })


@app.route('/admin/mantis-sync/revert', methods=['POST'])
@login_required
@admin_required
def admin_mantis_revert():
    revert_tickets   = request.form.get('revert_tickets')   == '1'
    revert_customers = request.form.get('revert_customers') == '1'
    revert_employees = request.form.get('revert_employees') == '1'

    stats = dict(tickets=0, customers=0, employees=0)

    try:
        if revert_tickets:
            mantis_tickets = Ticket.query.filter(
                Ticket.internal_title.like('%[mantis:%')
            ).all()
            for ticket in mantis_tickets:
                upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(ticket.id))
                for att in ticket.attachments.all():
                    disk_path = os.path.join(upload_dir, att.filename)
                    try:
                        if os.path.exists(disk_path):
                            os.remove(disk_path)
                    except OSError:
                        app.logger.warning('Could not remove attachment file: %s', disk_path)
                    db.session.delete(att)
                # Flush attachment deletes before message deletes (FK: attachments → messages)
                db.session.flush()
                for msg in ticket.messages.all():
                    db.session.delete(msg)
                for ev in ticket.events.all():
                    db.session.delete(ev)
                if ticket.assignment:
                    db.session.delete(ticket.assignment)
                db.session.delete(ticket)
                stats['tickets'] += 1
                # Remove the upload directory if now empty
                try:
                    if os.path.isdir(upload_dir) and not os.listdir(upload_dir):
                        os.rmdir(upload_dir)
                except OSError:
                    pass

        if revert_customers:
            for cust in Customer.query.filter_by(mantis_imported=True).all():
                cust.groups.clear()
                db.session.delete(cust)
                stats['customers'] += 1

        if revert_employees:
            for emp in Employee.query.filter_by(mantis_imported=True).all():
                for msg in emp.messages.all():
                    msg.employee_id = None
                db.session.delete(emp)
                stats['employees'] += 1

        db.session.commit()

        parts = []
        if stats['tickets']:   parts.append(_('%(n)d ticket(s) deleted',   n=stats['tickets']))
        if stats['customers']: parts.append(_('%(n)d customer(s) deleted', n=stats['customers']))
        if stats['employees']: parts.append(_('%(n)d employee(s) deleted', n=stats['employees']))
        flash(_('Revert complete: %(details)s.', details=', '.join(parts) or _('nothing to do')), 'success')

    except Exception as e:
        db.session.rollback()
        app.logger.exception('MantisBT revert failed')
        flash(_('Revert failed: %(error)s', error=str(e)), 'danger')

    return redirect(url_for('admin_mantis_sync'))


@app.route('/help')
@login_required
def help_page():
    if current_user.is_admin:
        default_stem = 'manual-admin'
    elif current_user.is_manager:
        default_stem = 'manual-manager'
    else:
        default_stem = 'manual-employee'

    requested = request.args.get('manual', '')
    valid_stems = {s for s, _ in MANUALS}
    if current_user.is_admin and requested in valid_stems:
        stem = requested
    else:
        stem = default_stem

    title = next((t for s, t in MANUALS if s == stem), _('Help'))
    content, toc = _render_markdown(_manual_path(stem))
    return render_template('help.html', content=content, toc=toc, title=title,
                           manuals=MANUALS if current_user.is_admin else None,
                           active_manual=stem)


@app.route('/customer/help')
def customer_help():
    if not get_current_customer():
        return redirect(url_for('customer_login'))
    content, toc = _render_markdown(_manual_path('manual-customers'))
    return render_template('help.html', content=content, toc=toc,
                           title=_('Customer Manual'))


@app.route('/healthz')
def healthz():
    """Readiness probe — confirms the process is up and the database is reachable."""
    from sqlalchemy import text as _text
    try:
        db.session.execute(_text('SELECT 1'))
        admin_ok = Employee.query.filter_by(is_admin=True, is_active=True).count() > 0
        return jsonify(status='ok', db='ok', admin=admin_ok), 200
    except Exception as exc:
        return jsonify(status='error', db='unreachable', error=str(exc)), 503


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

def _startup_checks():
    """Run critical pre-flight checks and log warnings to the app logger.

    Called once at startup inside an app context.  Only checks things that must
    work for the application to serve traffic correctly; heavier checks (SMTP,
    GitHub, functional tests) remain in the Admin → System Tests page.
    """
    import os
    from sqlalchemy import text as _text

    ok   = True
    log  = app.logger

    # 1. Database connectivity
    try:
        db.session.execute(_text('SELECT 1'))
        log.info('[startup] DB connection OK')
    except Exception as exc:
        log.error('[startup] FAIL – database connection error: %s', exc)
        ok = False

    # 2. At least one active admin account
    try:
        admin_count = Employee.query.filter_by(is_admin=True, is_active=True).count()
        if admin_count == 0:
            log.warning('[startup] WARN – no active admin account found; visit /setup to create one')
        else:
            log.info('[startup] Admin accounts: %d', admin_count)
    except Exception as exc:
        log.warning('[startup] WARN – could not query admin accounts: %s', exc)

    # 3. Secret key
    key = app.config.get('SECRET_KEY', '')
    if key == 'dev-secret-change-in-production':
        log.warning('[startup] WARN – SECRET_KEY is the default dev value; '
                    'set a strong random key before going to production')
    elif len(key) < 24:
        log.warning('[startup] WARN – SECRET_KEY is short (%d chars); '
                    'use a random key of at least 24 characters', len(key))
    else:
        log.info('[startup] SECRET_KEY is set and non-default')

    # 4. Upload folder — create if missing, warn if not writable
    folder = app.config.get('UPLOAD_FOLDER', 'uploads')
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
            log.warning('[startup] WARN – upload folder was missing; created: %s', folder)
        except Exception as exc:
            log.error('[startup] FAIL – upload folder missing and could not be created: %s', exc)
            ok = False
    elif not os.access(folder, os.W_OK):
        log.error('[startup] FAIL – upload folder is not writable: %s', folder)
        ok = False
    else:
        log.info('[startup] Upload folder OK: %s', folder)

    # 5. Mail suppression notice
    if app.config.get('MAIL_SUPPRESS_SEND', False):
        log.warning('[startup] WARN – MAIL_SUPPRESS_SEND=True; outbound emails are suppressed')
    elif not app.config.get('MAIL_SERVER'):
        log.warning('[startup] WARN – MAIL_SERVER not configured; outbound email is disabled')

    # 6. Auto-migrate: new columns
    for table, col, defn in [
        ('tickets',   'satisfaction_rating',       'INTEGER'),
        ('tickets',   'satisfaction_comment',      'TEXT'),
        ('tickets',   'satisfaction_submitted_at', 'DATETIME'),
        ('tickets',   'group_id',                  'INTEGER REFERENCES groups(id)'),
        ('employees', 'mantis_imported',           'BOOLEAN NOT NULL DEFAULT 0'),
        ('customers', 'mantis_imported',           'BOOLEAN NOT NULL DEFAULT 0'),
    ]:
        try:
            db.session.execute(_text(f'ALTER TABLE {table} ADD COLUMN {col} {defn}'))
            db.session.commit()
            log.info('[startup] Added column %s.%s', table, col)
        except Exception:
            db.session.rollback()

    return ok


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _startup_checks()
    app.run(debug=True, port=5000)
