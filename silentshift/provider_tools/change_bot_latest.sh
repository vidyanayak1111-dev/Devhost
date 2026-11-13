#!/bin/sh
cd "$(dirname "$0")"
PY=python3; [ -x ../venv/bin/python ] && PY=../venv/bin/python
exec "$PY" provider.py "$@"
