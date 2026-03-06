import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///taskify.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'localhost')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 1025))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'false').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@taskify.local')
    MAIL_SUPPRESS_SEND = os.environ.get('MAIL_SUPPRESS_SEND', 'false').lower() == 'true'

    BABEL_DEFAULT_LOCALE = os.environ.get('BABEL_DEFAULT_LOCALE', 'en')
    BABEL_SUPPORTED_LOCALES = ['en', 'de']

    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    PUBLIC_TICKETS = os.environ.get('PUBLIC_TICKETS', 'true').lower() == 'true'
    APP_NAME = os.environ.get('APP_NAME', 'Taskify')

    GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
    GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
    GITHUB_ORG = os.environ.get('GITHUB_ORG', '')
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

    # ── Inbound e-mail (IMAP polling) ────────────────────────────────────────
    IMAP_HOST          = os.environ.get('IMAP_HOST', '')
    IMAP_PORT          = int(os.environ.get('IMAP_PORT', 993))
    IMAP_USER          = os.environ.get('IMAP_USER', '')
    IMAP_PASSWORD      = os.environ.get('IMAP_PASSWORD', '')
    IMAP_USE_SSL       = os.environ.get('IMAP_USE_SSL', 'true').lower() == 'true'
    IMAP_POLL_INTERVAL = int(os.environ.get('IMAP_POLL_INTERVAL', 60))

    # ── Inbound e-mail via Microsoft Graph API (Microsoft 365) ───────────────
    AZURE_TENANT_ID     = os.environ.get('AZURE_TENANT_ID', '')
    AZURE_CLIENT_ID     = os.environ.get('AZURE_CLIENT_ID', '')
    AZURE_CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET', '')
    GRAPH_MAILBOX       = os.environ.get('GRAPH_MAILBOX', '')   # e.g. support@example.com
    GRAPH_POLL_INTERVAL = int(os.environ.get('GRAPH_POLL_INTERVAL', 60))

    # ── MantisBT synchronisation ─────────────────────────────────────────────
    MANTIS_DB_HOST   = os.environ.get('MANTIS_DB_HOST', '')
    MANTIS_DB_PORT   = int(os.environ.get('MANTIS_DB_PORT', 3306))
    MANTIS_DB_NAME   = os.environ.get('MANTIS_DB_NAME', 'bugtracker')
    MANTIS_DB_USER   = os.environ.get('MANTIS_DB_USER', '')
    MANTIS_DB_PASS   = os.environ.get('MANTIS_DB_PASS', '')
    MANTIS_TABLE_PREFIX = os.environ.get('MANTIS_TABLE_PREFIX', 'mantis_')
    MANTIS_UPLOAD_PATH  = os.environ.get('MANTIS_UPLOAD_PATH', '')
