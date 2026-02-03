#!/bin/bash
export OPENROUTER_API_KEY="your key" # User should set this or export it before running
./.venv/bin/uvicorn main:app --reload --port 8000
