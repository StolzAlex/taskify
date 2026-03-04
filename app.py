import os
import re
import uuid
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

import requests as http_requests
from authlib.integrations.flask_client import OAuth
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, send_from_directory, session, jsonify)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message as MailMessage
from flask_babel import Babel, gettext as _, lazy_gettext as _l, get_locale
from markupsafe import escape
from werkzeug.utils import secure_filename

from config import Config
from models import db, Employee, Customer, Company, Ticket, Assignment, Message, Attachment, TicketEvent, TicketWatch

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
mail = Mail(app)

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

def send_email(subject, recipients, body_text, body_html=None):
    try:
        msg = MailMessage(subject=subject, recipients=recipients, body=body_text,
                          html=body_html)
        mail.send(msg)
        if app.config.get('MAIL_SUPPRESS_SEND') or app.config.get('TESTING'):
            app.logger.info(f'Email suppressed (MAIL_SUPPRESS_SEND) to {recipients}: {subject}')
            flash(_('Email to %(addr)s was suppressed (MAIL_SUPPRESS_SEND is enabled).',
                    addr=', '.join(recipients)), 'warning')
            return False
        app.logger.info(f'Email sent to {recipients}: {subject}')
        return True
    except Exception as e:
        server = f"{app.config.get('MAIL_SERVER')}:{app.config.get('MAIL_PORT')}"
        app.logger.error(f'Email send failed (server={server}, suppress={app.config.get("MAIL_SUPPRESS_SEND")}): {e}')
        flash(_('Email to %(addr)s could not be sent (%(error)s).',
                addr=', '.join(recipients), error=str(e)), 'danger')
        return False


def notify_submitter_confirmation(ticket):
    status_url = url_for('ticket_status', token=ticket.token, _external=True)
    body = (
        f"Thank you for submitting your support request.\n\n"
        f"Subject: {ticket.subject}\n"
        f"Ticket ID: #{ticket.id}\n\n"
        f"You can track your ticket status at:\n{status_url}\n\n"
        f"We'll keep you updated by email."
    )
    send_email(
        subject=f"[{app.config['APP_NAME']}] Ticket #{ticket.id} received – {ticket.subject}",
        recipients=[ticket.submitter_email],
        body_text=body,
    )


def notify_submitter_update(ticket, extra_message=None):
    status_url = url_for('ticket_status', token=ticket.token, _external=True)
    body = (
        f"Your support ticket has been updated.\n\n"
        f"Subject: {ticket.subject}\n"
        f"Status: {ticket.status.replace('_', ' ').title()}\n\n"
    )
    if extra_message:
        body += f"Message from support:\n{extra_message}\n\n"
    if ticket.status in ('resolved', 'closed'):
        body += "This ticket is now closed. No further action is required on your part."
    else:
        body += f"View your ticket at:\n{status_url}"
    send_email(
        subject=f"[{app.config['APP_NAME']}] Ticket #{ticket.id} updated – {ticket.subject}",
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
            send_email(subject=subject, recipients=[w.employee.email], body_text=body)


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


# ---------------------------------------------------------------------------
# Language switcher
# ---------------------------------------------------------------------------

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in app.config['BABEL_SUPPORTED_LOCALES']:
        session['lang'] = lang
    return redirect(request.referrer or url_for('submit'))


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
        ticket = Ticket(submitter_email=email, subject=subject, body=body)
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
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        # Try customer by email
        customer = Customer.query.filter(Customer.email.ilike(email)).first()
        if customer and customer.is_active and customer.check_password(password):
            session['customer_id'] = customer.id
            next_page = request.args.get('next')
            return redirect(next_page or url_for('customer_dashboard'))
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
    tickets = Ticket.query.filter(
        Ticket.submitter_email.ilike(customer.email)
    ).order_by(Ticket.updated_at.desc()).all()

    # Per-ticket metadata
    ticket_meta = {}
    for t in tickets:
        visible = t.messages.filter_by(is_customer_visible=True)
        last_msg = (Message.query
                    .filter(Message.ticket_id == t.id,
                            db.or_(Message.is_customer_visible == True,
                                   Message.is_customer_reply == True))
                    .order_by(Message.created_at.desc())
                    .first())
        ticket_meta[t.id] = {
            'reply_count':    visible.count(),
            'awaiting_reply': last_msg is not None and not last_msg.is_customer_reply,
        }

    stats = {
        'open':           sum(1 for t in tickets if t.status == 'open'),
        'in_progress':    sum(1 for t in tickets if t.status == 'in_progress'),
        'awaiting_reply': sum(1 for t in tickets if ticket_meta[t.id]['awaiting_reply']),
        'resolved':       sum(1 for t in tickets if t.status == 'resolved'),
        'closed':         sum(1 for t in tickets if t.status == 'closed'),
    }

    ticket_ids = [t.id for t in tickets]
    recent_events = (TicketEvent.query
                     .filter(
                         TicketEvent.ticket_id.in_(ticket_ids),
                         TicketEvent.event_type.in_(['status', 'customer_reply', 'attachment', 'assignment'])
                     )
                     .order_by(TicketEvent.created_at.desc())
                     .limit(15).all()) if ticket_ids else []

    return render_template('customer/dashboard.html', customer=customer, tickets=tickets,
                           ticket_meta=ticket_meta, stats=stats, recent_events=recent_events)


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

def _resolve_companies(company_ids, new_name):
    """Return a list of Company objects from selected IDs + optional new name."""
    selected = Company.query.filter(Company.id.in_(company_ids)).all() if company_ids else []
    if new_name:
        existing = Company.query.filter(Company.name.ilike(new_name)).first()
        if existing:
            if existing not in selected:
                selected.append(existing)
        else:
            co = Company(name=new_name)
            db.session.add(co)
            db.session.flush()
            selected.append(co)
    return selected


@app.route('/manager/customers', methods=['GET', 'POST'])
@login_required
@manager_required
def manager_customers():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not name or not email or not password:
            flash(_('All fields are required.'), 'danger')
        elif Customer.query.filter(Customer.email.ilike(email)).first():
            flash(_('Email already in use.'), 'danger')
        else:
            customer = Customer(name=name, email=email, created_by_id=current_user.id)
            customer.set_password(password)
            customer.companies = _resolve_companies(
                request.form.getlist('company_ids', type=int),
                request.form.get('new_company', '').strip(),
            )
            db.session.add(customer)
            db.session.commit()
            send_customer_welcome_email(customer, password)
            flash(_('Customer "%(name)s" created.', name=name), 'success')
        return redirect(url_for('manager_customers'))
    customers    = Customer.query.order_by(Customer.created_at.desc()).all()
    all_companies = Company.query.order_by(Company.name).all()
    return render_template('manager/customers.html', customers=customers, all_companies=all_companies)


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
    company_filter       = request.args.get('company', '')
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
    if company_filter:
        co = Company.query.filter_by(name=company_filter).first()
        if co:
            co_emails = [c.email.lower() for c in co.customers]
            query = query.filter(db.func.lower(Ticket.submitter_email).in_(co_emails))
    watched_ids = {w.ticket_id for w in TicketWatch.query.filter_by(employee_id=current_user.id).all()}
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

    # All companies for filter dropdown
    companies = Company.query.order_by(Company.name).all()

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
    stats = {
        'open':           Ticket.query.filter_by(status='open').count(),
        'in_progress':    Ticket.query.filter_by(status='in_progress').count(),
        'unassigned':     Ticket.query.filter(
                              Ticket.status.in_(active_statuses),
                              ~Ticket.id.in_(db.session.query(Assignment.ticket_id))
                          ).count(),
        'resolved_week':  Ticket.query.filter(
                              Ticket.status.in_(['resolved', 'closed']),
                              Ticket.updated_at >= week_ago
                          ).count(),
        'mine':           Ticket.query.join(Assignment).filter(
                              Assignment.employee_id == current_user.id,
                              Ticket.status.in_(active_statuses)
                          ).count(),
        'watched':        TicketWatch.query.filter_by(employee_id=current_user.id).count(),
    }

    return render_template('dashboard.html', tickets=tickets,
                           pagination=pagination, per_page=per_page,
                           q=q,
                           status_filter=status_filter,
                           unassigned_filter=unassigned_filter,
                           resolved_week_filter=resolved_week_filter,
                           company_filter=company_filter,
                           companies=companies,
                           customer_map=customer_map,
                           view=view, is_privileged=is_privileged,
                           status_choices=Ticket.STATUS_CHOICES,
                           recent_events=recent_events,
                           stats=stats,
                           watched_ids=watched_ids,
                           hide_closed=hide_closed)


@app.route('/search')
@login_required
def search():
    q           = request.args.get('q', '').strip()
    status_f    = request.args.get('status', '')
    date_from   = request.args.get('date_from', '')
    date_to     = request.args.get('date_to', '')
    assignee_id = request.args.get('assignee', '')
    company_f   = request.args.get('company', '')

    performed = bool(q or status_f or date_from or date_to or assignee_id or company_f)

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

        if company_f:
            co = Company.query.filter_by(name=company_f).first()
            if co:
                co_emails = [c.email.lower() for c in co.customers]
                query = query.filter(db.func.lower(Ticket.submitter_email).in_(co_emails))

        page     = request.args.get('page', 1, type=int)
        per_page = 25
        pagination = query.order_by(Ticket.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        tickets = pagination.items

    submitter_emails = [t.submitter_email.lower() for t in tickets]
    customer_map = {c.email.lower(): c for c in Customer.query.filter(
        db.func.lower(Customer.email).in_(submitter_emails)).all()} if submitter_emails else {}

    employees = Employee.query.filter_by(is_active=True).order_by(Employee.username).all()
    companies = Company.query.order_by(Company.name).all()

    return render_template('search.html',
                           tickets=tickets, pagination=pagination, per_page=25,
                           q=q, status_filter=status_f, date_from=date_from, date_to=date_to,
                           assignee_id=assignee_id, company_filter=company_f,
                           employees=employees, companies=companies,
                           status_choices=Ticket.STATUS_CHOICES,
                           customer_map=customer_map, performed=performed)


def _sync_github_issue(ticket):
    """If ticket links to a GitHub issue, close the ticket when the issue is closed."""
    m = re.match(r'https://github\.com/([^/]+/[^/]+)/issues/(\d+)', ticket.github_pr_url or '')
    if not m:
        return
    headers = {'Accept': 'application/vnd.github+json'}
    token = app.config.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        resp = http_requests.get(
            f'https://api.github.com/repos/{m.group(1)}/issues/{m.group(2)}',
            headers=headers, timeout=5,
        )
    except Exception:
        return
    if not resp.ok:
        return
    gh_state = resp.json().get('state')
    if gh_state == 'closed' and ticket.status not in ('closed', 'resolved'):
        old = ticket.status
        ticket.status = 'closed'
        ticket.updated_at = datetime.utcnow()
        log_event(ticket, 'status', from_value=old, to_value='closed')
        db.session.commit()
        flash(_('GitHub issue was closed — ticket status updated to Closed.'), 'info')
    elif gh_state == 'open' and ticket.status == 'closed':
        ticket.status = 'open'
        ticket.updated_at = datetime.utcnow()
        log_event(ticket, 'status', from_value='closed', to_value='open')
        db.session.commit()
        flash(_('GitHub issue was reopened — ticket status updated to Open.'), 'info')


@app.route('/tickets/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    _sync_github_issue(ticket)
    employees = Employee.query.filter_by(is_active=True).all()
    events = ticket.events.all()
    is_watching = TicketWatch.query.filter_by(
        ticket_id=ticket_id, employee_id=current_user.id).first() is not None
    return render_template('ticket.html', ticket=ticket, employees=employees,
                           status_choices=Ticket.STATUS_CHOICES, events=events,
                           is_watching=is_watching)


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
        password = request.form.get('password', '')
        is_admin = request.form.get('is_admin') == 'on'
        is_manager = request.form.get('is_manager') == 'on'
        if not username or not email or not password:
            flash(_('All fields are required.'), 'danger')
        elif Employee.query.filter_by(username=username).first():
            flash(_('Username already taken.'), 'danger')
        elif Employee.query.filter_by(email=email).first():
            flash(_('Email already in use.'), 'danger')
        else:
            emp = Employee(username=username, email=email,
                           is_admin=is_admin, is_manager=is_manager, is_active=True)
            emp.set_password(password)
            db.session.add(emp)
            db.session.commit()
            flash(_('Employee "%(name)s" created.', name=username), 'success')
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
    customer.companies = _resolve_companies(
        request.form.getlist('company_ids', type=int),
        request.form.get('new_company', '').strip(),
    )
    if password:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
