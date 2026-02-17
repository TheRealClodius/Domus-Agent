#!/usr/bin/env bash
# Kill any existing uvicorn on port 8000, then start fresh.
lsof -ti:8000 | xargs kill -9 2>/dev/null
source .venv/bin/activate
uvicorn main:app --reload --port 8000
