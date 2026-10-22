#!/usr/bin/env bash
set -e

echo "=> Checking prerequisites..."
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required but not installed."
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo "Error: docker is required but not installed."
    exit 1
fi

echo "=> Creating directories..."
mkdir -p logs data tests/unit

echo "=> Setting up virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "=> Installing dependencies..."
pip install -r requirements/base.txt
pip install -r requirements/fl.txt
pip install -r requirements/dev.txt

echo "=> Configuring environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Please review and fill in required values."
fi

echo "=> Starting Redis via Docker Compose (if configured)..."
# Check if docker-compose.yml exists yet, if not warn
if [ -f docker-compose.yml ]; then
    docker-compose up -d redis
else
    echo "Warning: docker-compose.yml not found yet, skipping Redis startup."
fi

echo "=> Running sanity check tests..."
pytest tests/unit/ --tb=short || echo "Warning: Tests failed or no tests found."

echo "=> Setup complete!"
echo "Next steps:"
echo "1. Activate environment: source venv/bin/activate"
echo "2. Edit .env with your credentials"
echo "3. Run 'docker-compose up' to start all services (once available)"
