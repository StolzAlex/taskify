import json
import uuid
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Employee(UserMixin, db.Model):
    __tablename__ = 'employees'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)  # nullable for GitHub-only accounts
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_manager = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    github_id = db.Column(db.String(50), unique=True, nullable=True)
    github_login = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    preferences = db.Column(db.Text, nullable=True)
    setup_token = db.Column(db.String(64), nullable=True)
    setup_token_expires = db.Column(db.DateTime, nullable=True)
    mantis_imported = db.Column(db.Boolean, default=False, nullable=False, server_default='0')

    messages = db.relationship('Message', backref='author', lazy='dynamic')
    assignments = db.relationship('Assignment', backref='employee', lazy='dynamic')

    def get_pref(self, key, default=None):
        try:
            return json.loads(self.preferences or '{}').get(key, default)
        except Exception:
            return default

    def set_pref(self, key, value):
        try:
            prefs = json.loads(self.preferences or '{}')
        except Exception:
            prefs = {}
        prefs[key] = value
        self.preferences = json.dumps(prefs)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='scrypt')

    def check_password(self, password):
        if self.password_hash is None:
            return False
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)


customer_groups = db.Table(
    'customer_groups',
    db.Column('customer_id', db.Integer, db.ForeignKey('customers.id'), primary_key=True),
    db.Column('group_id',    db.Integer, db.ForeignKey('groups.id'),    primary_key=True),
)


class Group(db.Model):
    __tablename__ = 'groups'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Customer(db.Model):
    __tablename__ = 'customers'

    id                  = db.Column(db.Integer, primary_key=True)
    email               = db.Column(db.String(120), unique=True, nullable=False)
    name                = db.Column(db.String(120), nullable=False)
    password_hash       = db.Column(db.String(256), nullable=False)
    is_active           = db.Column(db.Boolean, default=True, nullable=False)
    created_by_id       = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    setup_token         = db.Column(db.String(64), nullable=True)
    setup_token_expires = db.Column(db.DateTime, nullable=True)
    mantis_imported     = db.Column(db.Boolean, default=False, nullable=False, server_default='0')

    created_by = db.relationship('Employee', backref='created_customers')
    groups     = db.relationship('Group', secondary=customer_groups, backref='customers')

    def set_password(self, p):
        self.password_hash = generate_password_hash(p, method='scrypt')

    def check_password(self, p):
        return check_password_hash(self.password_hash, p)


class Ticket(db.Model):
    __tablename__ = 'tickets'

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    submitter_email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open')
    locale = db.Column(db.String(10), nullable=False, default='en', server_default='en')
    github_sync = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    internal_title = db.Column(db.String(200), nullable=True)
    github_pr_url = db.Column(db.String(500), nullable=True)
    github_pr_title = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    satisfaction_rating       = db.Column(db.Integer, nullable=True)
    satisfaction_comment      = db.Column(db.Text, nullable=True)
    satisfaction_submitted_at = db.Column(db.DateTime, nullable=True)

    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True)

    messages = db.relationship('Message', backref='ticket', lazy='dynamic', order_by='Message.created_at')
    attachments = db.relationship('Attachment', backref='ticket', lazy='dynamic')
    assignment = db.relationship('Assignment', backref='ticket', uselist=False)
    group = db.relationship('Group', backref=db.backref('tickets', lazy='dynamic'))
    events = db.relationship('TicketEvent', backref='ticket', lazy='dynamic',
                             order_by='TicketEvent.created_at')
    watches = db.relationship('TicketWatch', backref='ticket', lazy='dynamic',
                              cascade='all, delete-orphan')

    STATUS_CHOICES = ['open', 'in_progress', 'resolved', 'closed']

    @property
    def status_badge(self):
        badges = {
            'open': 'secondary',
            'in_progress': 'primary',
            'resolved': 'success',
            'closed': 'dark',
        }
        return badges.get(self.status, 'secondary')

    @property
    def assignee(self):
        if self.assignment:
            return self.assignment.employee
        return None


class Assignment(db.Model):
    __tablename__ = 'assignments'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False, unique=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)  # None for customer replies
    body = db.Column(db.Text, nullable=False)
    is_customer_visible = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    is_customer_reply = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    edited_at = db.Column(db.DateTime, nullable=True)

    attachments = db.relationship('Attachment', backref='message', lazy='dynamic')


class TicketEvent(db.Model):
    __tablename__ = 'ticket_events'

    id          = db.Column(db.Integer, primary_key=True)
    ticket_id   = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True)
    event_type  = db.Column(db.String(50),  nullable=False)  # status|assignment|github_link|attachment|customer_reply
    from_value  = db.Column(db.String(500), nullable=True)
    to_value    = db.Column(db.String(500), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actor = db.relationship('Employee')


class TicketWatch(db.Model):
    __tablename__ = 'ticket_watches'
    id          = db.Column(db.Integer, primary_key=True)
    ticket_id   = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('ticket_id', 'employee_id'),)
    employee = db.relationship('Employee', backref='watches')


class Attachment(db.Model):
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=True)
    filename = db.Column(db.String(200), nullable=False)       # UUID name on disk
    original_filename = db.Column(db.String(200), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
