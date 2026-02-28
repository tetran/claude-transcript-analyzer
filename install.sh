#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing claude-transcript-analyzer hooks from: $REPO_DIR"

# Python で settings.json を安全にマージ
python3 "$REPO_DIR/install/merge_settings.py" "$REPO_DIR"

echo "Done. Please restart Claude Code to activate the hooks."
