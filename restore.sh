#!/usr/bin/env bash
# =============================================================================
# restore.sh — Taskify database and uploads restore
#
# Lists available backups and restores the selected one. Before overwriting
# anything it creates a safety snapshot of the current state.
#
# Usage:
#   ./restore.sh [OPTIONS]
#
# Options:
#   -d DIR        Directory containing backup files  (default: ./backups)
#   -t TIMESTAMP  Timestamp to restore, e.g. 2026-03-05T0200, or "latest"
#   -s SERVICE    systemd service name to stop/start (default: taskify)
#                 Pass an empty string (-s '') to skip service management.
#   -y            Skip confirmation prompt
#   -n            Dry run — show what would happen without doing it
#   -v            Verbose output
#   -h            Show this help
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKIFY_DIR="${TASKIFY_DIR:-$SCRIPT_DIR}"
BACKUP_DIR="${TASKIFY_DIR}/backups"
TIMESTAMP=""
SERVICE_NAME="taskify"
YES=0
DRY_RUN=0
VERBOSE=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
  sed -n '/^# Usage:/,/^# ====/p' "$0" | grep '^#' | sed 's/^# \?//'
  exit 0
}

while getopts ":d:t:s:ynvh" opt; do
  case $opt in
    d) BACKUP_DIR="$OPTARG" ;;
    t) TIMESTAMP="$OPTARG" ;;
    s) SERVICE_NAME="$OPTARG" ;;
    y) YES=1 ;;
    n) DRY_RUN=1 ;;
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
die()  { err "$*"; exit 1; }
run()  {
  if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY   $*"
  else
    info "RUN   $*"
    eval "$*"
  fi
}

# ---------------------------------------------------------------------------
# Locate target paths (same logic as backup.sh)
# ---------------------------------------------------------------------------
DB_PATH=""
if [[ -n "${DATABASE_URL:-}" ]]; then
  stripped="${DATABASE_URL#sqlite:///}"
  stripped="${stripped#sqlite://}"
  if [[ "$stripped" != "$DATABASE_URL" && -n "$stripped" ]]; then
    DB_PATH="$stripped"
  fi
fi
[[ -z "$DB_PATH" ]] && DB_PATH="${TASKIFY_DIR}/instance/taskify.db"

UPLOADS_PATH="${UPLOAD_FOLDER:-${TASKIFY_DIR}/uploads}"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
[[ -d "$BACKUP_DIR" ]] || die "Backup directory not found: $BACKUP_DIR"
command -v sqlite3 &>/dev/null || die "sqlite3 is not installed."

# ---------------------------------------------------------------------------
# Discover available timestamps
# ---------------------------------------------------------------------------
mapfile -t DB_FILES < <(
  find "$BACKUP_DIR" -maxdepth 1 -name 'taskify-db-*.sqlite3' \
    | sort -r
)

[[ ${#DB_FILES[@]} -gt 0 ]] || die "No database backups found in $BACKUP_DIR"

# Extract timestamps from filenames
declare -a TIMESTAMPS=()
for f in "${DB_FILES[@]}"; do
  ts="$(basename "$f" | sed 's/taskify-db-\(.*\)\.sqlite3/\1/')"
  TIMESTAMPS+=("$ts")
done

# ---------------------------------------------------------------------------
# Resolve which timestamp to use
# ---------------------------------------------------------------------------
if [[ -z "$TIMESTAMP" ]]; then
  echo
  echo "Available backups (newest first):"
  echo
  for i in "${!TIMESTAMPS[@]}"; do
    ts="${TIMESTAMPS[$i]}"
    db_file="${BACKUP_DIR}/taskify-db-${ts}.sqlite3"
    up_file="${BACKUP_DIR}/taskify-uploads-${ts}.tar.gz"
    db_size="$(du -sh "$db_file" 2>/dev/null | cut -f1)"
    up_info="no uploads archive"
    [[ -f "$up_file" ]] && up_info="uploads $(du -sh "$up_file" 2>/dev/null | cut -f1)"
    printf "  %2d)  %s   db %s, %s\n" "$((i+1))" "$ts" "$db_size" "$up_info"
  done
  echo

  if [[ $YES -eq 1 ]]; then
    # -y without -t → pick latest
    TIMESTAMP="${TIMESTAMPS[0]}"
    log "Auto-selected latest backup: $TIMESTAMP"
  else
    read -rp "Enter number to restore (or q to quit): " CHOICE
    [[ "$CHOICE" == "q" || "$CHOICE" == "Q" ]] && { echo "Aborted."; exit 0; }
    if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || \
       [[ "$CHOICE" -lt 1 || "$CHOICE" -gt "${#TIMESTAMPS[@]}" ]]; then
      die "Invalid selection: $CHOICE"
    fi
    TIMESTAMP="${TIMESTAMPS[$((CHOICE-1))]}"
  fi
elif [[ "$TIMESTAMP" == "latest" ]]; then
  TIMESTAMP="${TIMESTAMPS[0]}"
  log "Resolved 'latest' to: $TIMESTAMP"
fi

# ---------------------------------------------------------------------------
# Locate the selected backup files
# ---------------------------------------------------------------------------
DB_BACKUP="${BACKUP_DIR}/taskify-db-${TIMESTAMP}.sqlite3"
UPLOADS_BACKUP="${BACKUP_DIR}/taskify-uploads-${TIMESTAMP}.tar.gz"

[[ -f "$DB_BACKUP" ]] || die "Database backup not found: $DB_BACKUP"

RESTORE_UPLOADS=0
if [[ -f "$UPLOADS_BACKUP" ]]; then
  RESTORE_UPLOADS=1
else
  log "No uploads archive found for $TIMESTAMP — uploads will not be restored."
fi

# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------
echo
echo "  Restore backup : $TIMESTAMP"
echo "  Database       : $DB_BACKUP → $DB_PATH"
if [[ $RESTORE_UPLOADS -eq 1 ]]; then
  echo "  Uploads        : $UPLOADS_BACKUP → $UPLOADS_PATH"
else
  echo "  Uploads        : (no archive — skipping)"
fi
[[ -n "$SERVICE_NAME" ]] && echo "  Service        : $SERVICE_NAME (will be stopped and restarted)"
[[ $DRY_RUN -eq 1 ]]     && echo "  Mode           : DRY RUN — no changes will be made"
echo

if [[ $YES -eq 0 && $DRY_RUN -eq 0 ]]; then
  read -rp "Proceed? [y/N] " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# ---------------------------------------------------------------------------
# Stop service
# ---------------------------------------------------------------------------
SERVICE_WAS_RUNNING=0
if [[ -n "$SERVICE_NAME" ]] && command -v systemctl &>/dev/null; then
  if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    SERVICE_WAS_RUNNING=1
    log "Stopping $SERVICE_NAME…"
    run "systemctl stop '$SERVICE_NAME'"
  else
    info "Service $SERVICE_NAME is not running — skipping stop."
  fi
else
  [[ -n "$SERVICE_NAME" ]] && \
    log "systemctl not available — stop $SERVICE_NAME manually if it is running."
fi

# ---------------------------------------------------------------------------
# Safety snapshot of current state
# ---------------------------------------------------------------------------
if [[ $DRY_RUN -eq 0 ]]; then
  SNAP_TS="$(date '+%Y-%m-%dT%H%M')"
  SNAP_DIR="${BACKUP_DIR}/pre-restore-${SNAP_TS}"
  mkdir -p "$SNAP_DIR"

  if [[ -f "$DB_PATH" ]]; then
    info "Saving current database to $SNAP_DIR"
    sqlite3 "$DB_PATH" ".backup '${SNAP_DIR}/taskify-db-${SNAP_TS}.sqlite3'" \
      && ok "Safety snapshot: $(basename "$SNAP_DIR")/taskify-db-${SNAP_TS}.sqlite3"
  fi

  if [[ -d "$UPLOADS_PATH" ]]; then
    info "Saving current uploads to $SNAP_DIR"
    tar -czf "${SNAP_DIR}/taskify-uploads-${SNAP_TS}.tar.gz" \
        -C "$(dirname "$UPLOADS_PATH")" "$(basename "$UPLOADS_PATH")" 2>/dev/null \
      && ok "Safety snapshot: $(basename "$SNAP_DIR")/taskify-uploads-${SNAP_TS}.tar.gz"
  fi
fi

# ---------------------------------------------------------------------------
# Restore database
# ---------------------------------------------------------------------------
log "Restoring database…"
run "mkdir -p '$(dirname "$DB_PATH")'"
run "cp '$DB_BACKUP' '$DB_PATH'"
ok "Database restored from $(basename "$DB_BACKUP")"

# ---------------------------------------------------------------------------
# Restore uploads
# ---------------------------------------------------------------------------
if [[ $RESTORE_UPLOADS -eq 1 ]]; then
  log "Restoring uploads…"
  run "rm -rf '$UPLOADS_PATH'"
  run "mkdir -p '$(dirname "$UPLOADS_PATH")'"
  run "tar -xzf '$UPLOADS_BACKUP' -C '$(dirname "$UPLOADS_PATH")'"
  ok "Uploads restored from $(basename "$UPLOADS_BACKUP")"
fi

# ---------------------------------------------------------------------------
# Restart service
# ---------------------------------------------------------------------------
if [[ $SERVICE_WAS_RUNNING -eq 1 ]]; then
  log "Starting $SERVICE_NAME…"
  run "systemctl start '$SERVICE_NAME'"
  ok "$SERVICE_NAME restarted."
elif [[ -n "$SERVICE_NAME" ]]; then
  log "Service was not running before restore — not starting it automatically."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
if [[ $DRY_RUN -eq 1 ]]; then
  log "Dry run complete. No changes were made."
else
  log "Restore complete."
  [[ -n "${SNAP_DIR:-}" ]] && log "Pre-restore snapshot saved to: $SNAP_DIR"
fi
