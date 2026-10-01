#!/bin/bash
# One-command bootstrap: clone (or reuse) the cpipe repo and install.
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/chryzxc/cpipe/main/bootstrap.sh)
# Pin a branch/tag/commit: HERMES_DELIVERY_REF=v0.4.0 bash <(curl -fsSL ...)
set -euo pipefail

H="${HERMES_HOME:-$HOME/.hermes}"
REF="${HERMES_DELIVERY_REF:-main}"

if [ -L "$H/plugins/cpipe" ]; then
  REPO="$(readlink "$H/plugins/cpipe")"
  echo "existing install detected: $REPO"
  git -C "$REPO" fetch origin "$REF"
  git -C "$REPO" checkout --quiet "$REF" 2>/dev/null || true
  git -C "$REPO" pull --ff-only origin "$REF"
else
  DEFAULT_DIR="$HOME/Projects/cpipe"
  [ -d "$HOME/Projects" ] || DEFAULT_DIR="$H/cpipe"
  REPO="${DELIVERY_WORKFLOW_DIR:-$DEFAULT_DIR}"
  if [ -d "$REPO/.git" ]; then
    echo "repo exists at $REPO — fetching $REF"
    git -C "$REPO" fetch origin "$REF"
    git -C "$REPO" checkout --quiet "$REF" 2>/dev/null || true
    git -C "$REPO" pull --ff-only origin "$REF"
  elif [ -d "$REPO" ] && [ -n "$(ls -A "$REPO" 2>/dev/null)" ]; then
    echo "ERROR: $REPO exists but is not a git repository — remove it or set DELIVERY_WORKFLOW_DIR" >&2
    exit 1
  else
    echo "cloning to $REPO (ref: $REF)"
    git clone --branch "$REF" https://github.com/chryzxc/cpipe.git "$REPO" \
      || git clone https://github.com/chryzxc/cpipe.git "$REPO"
  fi
fi

ACTUAL="$(git -C "$REPO" rev-parse HEAD)"
echo "installing cpipe @ $ACTUAL (ref: $REF)"
RESOLVED="$(git -C "$REPO" rev-parse --verify "${REF}^{commit}" 2>/dev/null || echo "$ACTUAL")"
if [ "$ACTUAL" != "$RESOLVED" ]; then
  echo "ERROR: HEAD ($ACTUAL) does not match resolved ref $REF ($RESOLVED) — refusing to install" >&2
  exit 1
fi

exec "$REPO/install.sh" "$@"
