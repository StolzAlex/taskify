import os
import re
import uuid
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, send_from_directory, session)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message as MailMessage
from flask_babel import Babel, gettext as _, lazy_gettext as _l, get_locale
from markupsafe import escape
from werkzeug.utils import secure_filename

from config import Config
from models import db, Employee, Ticket, Assignment, Message, Attachment

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


@app.context_processor
def inject_globals():
    return {
        'now': datetime.utcnow(),
        'status_label': status_label,
        'get_locale': get_locale,
    }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Employee, int(user_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def send_email(subject, recipients, body_text, body_html=None):
    try:
        msg = MailMessage(subject=subject, recipients=recipients, body=body_text,
                          html=body_html)
        mail.send(msg)
    except Exception as e:
        app.logger.warning(f'Email send failed: {e}')


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
        subject=f"[Taskify] Ticket #{ticket.id} received – {ticket.subject}",
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
    body += f"View your ticket at:\n{status_url}"
    send_email(
        subject=f"[Taskify] Ticket #{ticket.id} updated – {ticket.subject}",
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
            subject=f"[Taskify] Customer replied – Ticket #{ticket.id}",
            recipients=recipients,
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
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        if not email or not subject or not body:
            flash(_('All fields are required.'), 'danger')
            return render_template('submit.html')
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
    return render_template('submit.html')


@app.route('/status/<token>')
def ticket_status(token):
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    thread = ticket.messages.filter(
        db.or_(Message.is_customer_visible == True, Message.is_customer_reply == True)
    ).all()
    return render_template('ticket_status.html', ticket=ticket, messages=thread)


@app.route('/status/<token>/reply', methods=['POST'])
def customer_reply(token):
    ticket = Ticket.query.filter_by(token=token).first_or_404()
    body = request.form.get('body', '').strip()
    if not body:
        flash(_('Reply cannot be empty.'), 'danger')
        return redirect(url_for('ticket_status', token=token))
    safe_body = '<p>' + str(escape(body)).replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'
    msg = Message(ticket_id=ticket.id, employee_id=None, body=safe_body,
                  is_customer_visible=False, is_customer_reply=True)
    db.session.add(msg)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    notify_assignee_customer_reply(ticket)
    flash(_('Your reply has been sent.'), 'success')
    return redirect(url_for('ticket_status', token=token))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        emp = Employee.query.filter_by(username=username).first()
        if emp and emp.is_active and emp.check_password(password):
            login_user(emp, remember=request.form.get('remember') == 'on')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        flash(_('Invalid credentials or account disabled.'), 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Employee routes
# ---------------------------------------------------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    status_filter = request.args.get('status', '')
    assignee_filter = request.args.get('assignee', '')

    query = Ticket.query
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    if assignee_filter == 'me':
        query = query.join(Assignment).filter(Assignment.employee_id == current_user.id)
    elif assignee_filter == 'unassigned':
        query = query.filter(~Ticket.assignment.has())

    tickets = query.order_by(Ticket.updated_at.desc()).all()
    employees = Employee.query.filter_by(is_active=True).all()
    return render_template('dashboard.html', tickets=tickets, employees=employees,
                           status_filter=status_filter, assignee_filter=assignee_filter,
                           status_choices=Ticket.STATUS_CHOICES)


@app.route('/tickets/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    employees = Employee.query.filter_by(is_active=True).all()
    return render_template('ticket.html', ticket=ticket, employees=employees,
                           status_choices=Ticket.STATUS_CHOICES)


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
    db.session.commit()
    if is_visible:
        plain = re.sub(r'<[^>]+>', '', body)
        notify_submitter_update(ticket, extra_message=plain)
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
    ticket.status = new_status
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    notify_submitter_update(ticket)
    flash(_('Status changed to %(status)s.', status=status_label(new_status)), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/tickets/<int:ticket_id>/assign', methods=['POST'])
@login_required
def assign_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
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
    else:
        if ticket.assignment:
            db.session.delete(ticket.assignment)
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    flash(_('Assignment updated.'), 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------

@app.route('/tickets/<int:ticket_id>/attachments', methods=['POST'])
@login_required
def upload_attachment(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    f = request.files.get('file')
    if not f or not f.filename:
        flash(_('No file selected.'), 'danger')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    # Resolve the target message (must belong to this ticket)
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
        if not username or not email or not password:
            flash(_('All fields are required.'), 'danger')
        elif Employee.query.filter_by(username=username).first():
            flash(_('Username already taken.'), 'danger')
        elif Employee.query.filter_by(email=email).first():
            flash(_('Email already in use.'), 'danger')
        else:
            emp = Employee(username=username, email=email, is_admin=is_admin, is_active=True)
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
