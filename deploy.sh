#!/usr/bin/env bash
# Deploy Taskify to aws-wt-mantis
set -euo pipefail

HOST="aws-wt-mantis"
REMOTE_DIR="/opt/taskify"
VENV="$REMOTE_DIR/.venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
step() { echo -e "\n${CYAN}${BOLD}→ $*${NC}"; }
ok()   { echo -e "${GREEN}✓ $*${NC}"; }
die()  { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

# ── 1. Sync code ─────────────────────────────────────────────────────────────
step "Syncing code to $HOST:$REMOTE_DIR …"
rsync -az --delete \
  --exclude='.env' \
  --exclude='.venv/' \
  --exclude='instance/' \
  --exclude='uploads/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.git/' \
  --exclude='deploy.sh' \
  /home/alex/projects/taskify/ \
  "$HOST:$REMOTE_DIR/"
ok "Code synced."

# ── 2. Remote setup ───────────────────────────────────────────────────────────
step "Running remote setup …"
ssh "$HOST" bash <<'REMOTE'
set -euo pipefail
REMOTE_DIR="/opt/taskify"
VENV="$REMOTE_DIR/.venv"

echo "  Cleaning stale HTML files in project root …"
rm -f "$REMOTE_DIR"/*.html

echo "  Updating Python packages …"
"$VENV/bin/pip" install -q --upgrade -r "$REMOTE_DIR/requirements.txt"

# ── Ollama ────────────────────────────────────────────────────────────────────
# ── Swap (Ollama braucht mind. 1.1 GB) ───────────────────────────────────────
if [ ! -f /swapfile ]; then
  echo "  Adding 2 GB swapfile (not enough RAM for LLM otherwise) …"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
fi

if ! command -v ollama &>/dev/null; then
  echo "  Installing Ollama …"
  curl -fsSL https://ollama.com/install.sh | sudo sh
  echo "  Ollama installed."
else
  echo "  Ollama already installed: $(ollama --version)"
fi

echo "  Enabling + starting Ollama service …"
sudo systemctl enable --now ollama

# Wait up to 15 s for Ollama to be ready
for i in $(seq 1 15); do
  curl -sf http://localhost:11434/ &>/dev/null && break
  sleep 1
done
curl -sf http://localhost:11434/ &>/dev/null || { echo "  ERROR: Ollama did not start"; exit 1; }

echo "  Pulling gemma3:1b (runs locally from cache if already present) …"
ollama pull gemma3:1b

# ── .env AI block ─────────────────────────────────────────────────────────────
echo "  Patching .env with AI settings …"
ENV_FILE="$REMOTE_DIR/.env"

# Remove any existing AI block so we can rewrite it cleanly
sed -i '/^# ── AI/d; /^AI_ENABLED=/d; /^AI_BASE_URL=/d; /^AI_MODEL=/d; /^AI_TIMEOUT=/d' "$ENV_FILE"

cat >> "$ENV_FILE" <<'ENV'

# ── AI (Ollama) ───────────────────────────────────────────────────────────────
AI_ENABLED=true
AI_BASE_URL=http://localhost:11434
AI_MODEL=gemma3:1b
AI_TIMEOUT=60
ENV
echo "  .env updated."

# ── Gunicorn timeout (AI needs > 30 s) ───────────────────────────────────────
SERVICE=/etc/systemd/system/taskify.service
if ! grep -q '\-\-timeout' "$SERVICE"; then
  echo "  Adding --timeout 120 to gunicorn …"
  sudo sed -i 's|app:app|app:app \\\n    --timeout 120|' "$SERVICE"
  sudo systemctl daemon-reload
fi

echo "  Restarting taskify …"
sudo systemctl restart taskify
sleep 2
sudo systemctl is-active taskify || { journalctl -u taskify -n 20 --no-pager; exit 1; }
REMOTE

ok "Remote setup complete."

# ── 3. Smoke test ─────────────────────────────────────────────────────────────
step "Smoke test …"
HTTP=$(ssh "$HOST" "curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/login")
[[ "$HTTP" == "200" ]] || die "App returned HTTP $HTTP — check 'journalctl -u taskify -n 30' on server."
ok "App is up (HTTP 200 on /login)."

echo -e "\n${BOLD}${GREEN}Deploy complete.${NC}"
echo "  AI model : gemma3:1b"
echo "  App URL  : http://aws-wt-mantis:8000  (or via reverse proxy)"
