#!/usr/bin/env bash
# Rebuild and restart the Pinscope stack on the production host
# (pinscope.michelebigi.it). Run from anywhere:
#
#   ./scripts/update-pinscope.sh
#
# Optional:
#   SITE=https://pinscope.michelebigi.it ./scripts/update-pinscope.sh
#   ./scripts/update-pinscope.sh --no-pull
#
# Does not touch ./data (projects + component library).

set -euo pipefail

SITE="${SITE:-https://pinscope.michelebigi.it}"
DO_PULL=1
for arg in "$@"; do
  case "$arg" in
    --no-pull) DO_PULL=0 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--no-pull]" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [[ "${ENVIRONMENT:-}" == "production" ]]; then
  die "ENVIRONMENT=production is set. The backend will refuse to start without Clerk. Unset it for this self-hosted instance."
fi

if [[ ! -f docker-compose.yml ]]; then
  die "docker-compose.yml not found in $ROOT — run this from the Pinscope checkout."
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    die "docker compose is not installed"
  fi
}

upsert_env() {
  local key="$1" value="$2" file="$3"
  python3 - "$key" "$value" "$file" <<'PY'
import sys
from pathlib import Path

key, value, path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
text = path.read_text() if path.exists() else ""
lines = text.splitlines()
out = []
found = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("#"):
        out.append(line)
        continue
    if stripped.split("=", 1)[0].strip() == key:
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    if out and out[-1] != "":
        out.append("")
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + ("\n" if out else ""))
PY
}

read_env() {
  local key="$1" file="$2"
  python3 - "$key" "$file" <<'PY'
import sys
from pathlib import Path
key, path = sys.argv[1], Path(sys.argv[2])
if not path.exists():
    sys.exit(0)
for line in path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, _, v = s.partition("=")
    if k.strip() == key:
        print(v)
        break
PY
}

if [[ ! -f .env ]]; then
  if [[ -f backend/.env ]]; then
    log "No ./.env — copying backend/.env"
    cp backend/.env .env
  elif [[ -f backend/.env.example ]]; then
    log "No ./.env — copying backend/.env.example (you must set DEEPSEEK_API_KEY)"
    cp backend/.env.example .env
  else
    die "No .env found. Create one with DEEPSEEK_API_KEY at $ROOT/.env"
  fi
fi

log "Ensuring public URL in .env ($SITE)"
upsert_env NEXT_PUBLIC_API_URL "$SITE" .env
# JSON list — keep it a single line so docker compose / pydantic-settings parse it.
upsert_env CORS_ORIGINS "[\"$SITE\"]" .env

KEY="$(read_env DEEPSEEK_API_KEY .env || true)"
if [[ -z "$KEY" || "$KEY" == "sk-..." ]]; then
  die "Set a real DEEPSEEK_API_KEY in $ROOT/.env before updating."
fi

mkdir -p data

if [[ "$DO_PULL" -eq 1 ]]; then
  if [[ -d .git ]]; then
    log "git pull"
    branch="$(git rev-parse --abbrev-ref HEAD)"
    git pull --ff-only origin "$branch" || git pull --ff-only
  else
    log "Not a git checkout — skipping pull (use --no-pull next time to silence this)"
  fi
else
  log "Skipping git pull (--no-pull)"
fi

log "docker compose up -d --build (data/ is kept)"
compose up -d --build

log "Waiting for backend"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8080/api/library" >/dev/null 2>&1 \
    || curl -fsS "http://127.0.0.1:8080/docs" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "Backend did not become ready on :8080. Last logs:" >&2
  compose logs --tail 80 backend >&2 || true
  exit 1
fi

log "Done. Site should be $SITE (nginx/Caddy still fronts :3000 / :8080)."
compose ps
