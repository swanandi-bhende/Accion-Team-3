#!/usr/bin/env bash
# Set up the project: a virtual environment, dependencies, a .env file, and Redis.
set -euo pipefail

echo "=> Checking prerequisites..."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Error: Python 3.11 or newer is required."; exit 1; }
docker compose version > /dev/null 2>&1 || { echo "Error: Docker with Compose v2 ('docker compose') is required."; exit 1; }

echo "=> Setting up virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "=> Installing dependencies..."
pip install -r requirements/base.txt -r requirements/fl.txt -r requirements/dev.txt
# Editable install of the packages under src/, so scripts can import them from anywhere.
pip install -e .

echo "=> Configuring environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Review it and fill in the REAL values."
fi

echo "=> Starting Redis..."
docker compose up -d redis

echo "=> Setup complete. Next:"
echo "   source venv/bin/activate"
echo "   docker compose up -d        # all services"
echo "   pytest tests/unit tests/property"
