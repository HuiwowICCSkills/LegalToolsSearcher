#!/usr/bin/env sh
# POSIX entry point for LegalToolsSearcher.
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for candidate in "$SKILL_DIR/venv/bin/python" "$(command -v python3 || true)" "$(command -v python || true)"; do
    [ -n "$candidate" ] || continue
    [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 &&
       { "$candidate" -c 'import pypdf' >/dev/null 2>&1 || "$candidate" -c 'import pymupdf' >/dev/null 2>&1; }; then
        exec "$candidate" -X utf8 "$SKILL_DIR/scripts/legal_tools_search.py" "$@"
    fi
done

echo "No Python 3.10+ with pypdf or PyMuPDF was found. Run: python3 -m pip install -r requirements.txt" >&2
exit 1
