#!/usr/bin/env bash
# One-line installer, Hermes-style:
#   curl -fsSL https://raw.githubusercontent.com/fakhrads/agentic-core/refs/heads/main/scripts/install.sh | bash
#
# Sets up a venv, installs the `agent` package, drops a shim on PATH, copies
# .env.example -> .env, and (if docker is available) starts redis+postgres.
# Never assumes stdin is a real terminal — piped `curl | bash` runs cannot
# read prompts from stdin, so this script reads confirmations from /dev/tty
# and otherwise just prints what to do next.

set -euo pipefail

REPO_URL="${AGENT_REPO_URL:-https://github.com/fakhrads/agentic-core.git}"
INSTALL_DIR="${AGENT_INSTALL_DIR:-$HOME/.agentic-core}"
BIN_DIR="${AGENT_BIN_DIR:-$HOME/.local/bin}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$1"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; exit 1; }

ask_tty() {
    # Usage: ask_tty "question [y/N] " -> returns 0/1. No-op (default no) if
    # there's no controlling terminal, e.g. under `curl | bash`.
    if [ -r /dev/tty ]; then
        read -r -p "$1" reply < /dev/tty || reply=""
        [ "$reply" = "y" ] || [ "$reply" = "Y" ]
    else
        return 1
    fi
}

command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.12+ first."
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "python3 is $PY_VER, need >=3.12."

if [ -f "pyproject.toml" ] && grep -q '^name = "agent-core"' pyproject.toml 2>/dev/null; then
    INSTALL_DIR="$(pwd)"
    info "Already inside an agentic-core checkout ($INSTALL_DIR) — installing in place."
elif [ -d "$INSTALL_DIR/.git" ]; then
    info "Found existing checkout at $INSTALL_DIR — pulling latest."
    git -C "$INSTALL_DIR" pull --ff-only
else
    command -v git >/dev/null 2>&1 || die "git not found — install git first."
    info "Cloning into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

info "Creating virtualenv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

info "Installing agent-core (pip install -e .)"
pip install --quiet --upgrade pip
pip install --quiet -e .

mkdir -p "$BIN_DIR"
SHIM="$BIN_DIR/agent"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/.venv/bin/agent" "\$@"
EOF
chmod +x "$SHIM"
info "Installed shim at $SHIM"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — add this to your shell rc:
    export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

if [ ! -f ".env" ]; then
    cp .env.example .env
    info "Created .env from .env.example — run 'agent setup' to fill it in."
fi

if command -v docker >/dev/null 2>&1; then
    if ask_tty "Start redis + postgres now via docker compose? [y/N] "; then
        docker compose up -d
    else
        info "Skipping — run 'docker compose up -d' later."
    fi
else
    warn "docker not found — install it to run redis/postgres locally, or point AGENT_REDIS_URL / AGENT_POSTGRES_DSN at existing instances."
fi

info "Install complete."
echo
echo "  Next:"
echo "    agent setup     # interactive configuration wizard"
echo "    agent           # start chatting"
