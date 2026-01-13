#!/usr/bin/env bash
set -e
set -o pipefail
export $(grep -v '^$' scripts/dev.env | xargs)
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
uvicorn app.http:app --host 0.0.0.0 --port 8000 --reload >/tmp/uvicorn.log 2>&1 &
sleep 1
tail -n 60 /tmp/uvicorn.log
