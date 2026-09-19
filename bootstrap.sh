#!/bin/bash
# One-command bootstrap: clone (or reuse) the delivery-workflow repo and install.
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/chryzxc/hermes-delivery-workflow/main/bootstrap.sh)
set -euo pipefail

H="${HERMES_HOME:-$HOME/.hermes}"

if [ -L "$H/plugins/delivery-workflow" ]; then
  REPO="$(readlink "$H/plugins/delivery-workflow")"
  echo "existing install detected: $REPO"
  git -C "$REPO" pull --ff-only
else
  DEFAULT_DIR="$HOME/Projects/hermes-delivery-workflow"
  [ -d "$HOME/Projects" ] || DEFAULT_DIR="$H/delivery-workflow"
  REPO="${DELIVERY_WORKFLOW_DIR:-$DEFAULT_DIR}"
  if [ -d "$REPO/.git" ]; then
    echo "repo exists at $REPO — pulling"
    git -C "$REPO" pull --ff-only
  elif [ -d "$REPO" ] && [ -n "$(ls -A "$REPO" 2>/dev/null)" ]; then
    echo "ERROR: $REPO exists but is not a git repository — remove it or set DELIVERY_WORKFLOW_DIR" >&2
    exit 1
  else
    echo "cloning to $REPO"
    git clone https://github.com/chryzxc/hermes-delivery-workflow.git "$REPO"
  fi
fi

exec "$REPO/install.sh" "$@"
