#!/bin/bash
# No arguments performs a read-only check. Installation/start is explicit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
exec "$PYTHON" "$ROOT/scripts/service.py" "$@"
