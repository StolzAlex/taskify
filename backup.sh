#!/usr/bin/env bash
# =============================================================================
# backup.sh — Taskify database and uploads backup
#
# Creates a timestamped SQLite backup and a compressed archive of uploaded
# attachments, then prunes files older than KEEP_DAYS from the destination.
#
# Usage:
#   ./backup.sh [OPTIONS]
#
# Options:
#   -d DIR     Destination directory for backup files  (default: ./backups)
#   -k DAYS    Number of days to keep backups          (default: 14)
#   -v         Verbose output
#   -h         Show this help
#
# Cron example (daily at 02:00, append to log):
#   0 2 * * * /opt/taskify/backup.sh -d /var/backups/taskify >> /var/log/taskify/backup.log 2>&1
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults — edit here or override with flags
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKIFY_DIR="${TASKIFY_DIR:-$SCRIPT_DIR}"
DEST_DIR="${TASKIFY_DIR}/backups"
KEEP_DAYS=14
VERBOSE=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
  sed -n '/^# Usage:/,/^# ====/p' "$0" | grep '^#' | sed 's/^# \?//'
  exit 0
}

while getopts ":d:k:vh" opt; do
  case $opt in
    d) DEST_DIR="$OPTARG" ;;
    k) KEEP_DAYS="$OPTARG" ;;
    v) VERBOSE=1 ;;
    h) usage ;;
    :) echo "ERROR: -$OPTARG requires an argument." >&2; exit 1 ;;
    \?) echo "ERROR: Unknown option -$OPTARG." >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
info() { [[ $VERBOSE -eq 1 ]] && log "INFO  $*" || true; }
ok()   { log "OK    $*"; }
err()  { log "ERROR $*" >&2; }

ERRORS=0
fail() { err "$*"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# Locate source paths
# ---------------------------------------------------------------------------

# Database: prefer DATABASE_URL env var, fall back to default SQLite location.
DB_PATH=""
if [[ -n "${DATABASE_URL:-}" ]]; then
  # Strip leading sqlite:/// (absolute) or sqlite:// (relative)
  stripped="${DATABASE_URL#sqlite:///}"
  stripped="${stripped#sqlite://}"
  if [[ "$stripped" != "$DATABASE_URL" && -n "$stripped" ]]; then
    DB_PATH="$stripped"
  fi
fi
if [[ -z "$DB_PATH" ]]; then
  DB_PATH="${TASKIFY_DIR}/instance/taskify.db"
fi

# Uploads: prefer UPLOAD_FOLDER env var, fall back to default.
UPLOADS_PATH="${UPLOAD_FOLDER:-${TASKIFY_DIR}/uploads}"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if ! command -v sqlite3 &>/dev/null; then
  err "sqlite3 is not installed. Install it (e.g. 'apt install sqlite3') and retry."
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  fail "Database not found: $DB_PATH"
fi

# ---------------------------------------------------------------------------
# Create destination
# ---------------------------------------------------------------------------
mkdir -p "$DEST_DIR"
info "Destination: $DEST_DIR"

# ---------------------------------------------------------------------------
# Timestamp for this run
# ---------------------------------------------------------------------------
TS="$(date '+%Y-%m-%dT%H%M')"

# ---------------------------------------------------------------------------
# 1. Database backup (online — no downtime required)
# ---------------------------------------------------------------------------
DB_BACKUP="${DEST_DIR}/taskify-db-${TS}.sqlite3"
info "Backing up database $DB_PATH → $DB_BACKUP"
if sqlite3 "$DB_PATH" ".backup '${DB_BACKUP}'"; then
  DB_SIZE="$(du -sh "$DB_BACKUP" 2>/dev/null | cut -f1)"
  ok "Database backup complete (${DB_SIZE}): $(basename "$DB_BACKUP")"
else
  fail "sqlite3 .backup failed"
fi

# ---------------------------------------------------------------------------
# 2. Uploads backup (compressed tar archive)
# ---------------------------------------------------------------------------
UPLOADS_BACKUP="${DEST_DIR}/taskify-uploads-${TS}.tar.gz"
if [[ -d "$UPLOADS_PATH" ]]; then
  info "Archiving uploads $UPLOADS_PATH → $UPLOADS_BACKUP"
  if tar -czf "$UPLOADS_BACKUP" -C "$(dirname "$UPLOADS_PATH")" "$(basename "$UPLOADS_PATH")" 2>/dev/null; then
    UP_SIZE="$(du -sh "$UPLOADS_BACKUP" 2>/dev/null | cut -f1)"
    ok "Uploads archive complete (${UP_SIZE}): $(basename "$UPLOADS_BACKUP")"
  else
    fail "tar failed for uploads"
  fi
else
  info "Uploads directory not found ($UPLOADS_PATH) — skipping uploads backup."
fi

# ---------------------------------------------------------------------------
# 3. Prune old backups
# ---------------------------------------------------------------------------
info "Pruning backups older than ${KEEP_DAYS} days from $DEST_DIR"
PRUNED=0
while IFS= read -r -d '' f; do
  info "  Removing $(basename "$f")"
  rm -f "$f"
  PRUNED=$((PRUNED + 1))
done < <(find "$DEST_DIR" \
           \( -name 'taskify-db-*.sqlite3' -o -name 'taskify-uploads-*.tar.gz' \) \
           -mtime "+${KEEP_DAYS}" -print0)
[[ $PRUNED -gt 0 ]] && ok "Pruned $PRUNED old backup file(s)." || info "Nothing to prune."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ $ERRORS -eq 0 ]]; then
  log "Backup finished successfully."
  exit 0
else
  log "Backup finished with $ERRORS error(s). Check output above."
  exit 1
fi
