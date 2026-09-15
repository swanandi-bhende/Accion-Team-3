# Local Development Setup Guide

## Prerequisites
- Python 3.10+
- Docker 20.10+ (and docker-compose)
- Git

## Clone and Navigate
```bash
git clone <repository_url>
cd Accion-Verifiable-AI-Execution-Subsystem
```

## Install Dependencies
The provided `setup.sh` script automates dependency installation:
```bash
./setup.sh
```
*Alternatively, manual installation:*
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Configure Environment
Copy the example environment template:
```bash
cp .env.example .env
```
*Note:* The default values in `.env.example` are safe for local development. Real credentials, blockchain RPCs, and Team A/C endpoints must be provided later once integration is unblocked.

## Verify Installation
Run tests to ensure dependencies and environment are sane:
```bash
pytest tests/unit/ --tb=short
pytest tests/property/ --tb=short
```

## Run the Application
Start the infrastructure and services:
```bash
docker-compose up -d
```
Verify the APIs are healthy:
- Inference Engine: `GET http://localhost:8000/health`
- Verification API: `GET http://localhost:8001/health`

## Service Startup Order
To ensure proper resolution, services boot in this order:
`Redis & IPFS` → `Model Registry (Seed)` → `Inference Engine` → `Event Listener` → `Proof Submitter` → `Verification API`

## Troubleshooting
- **Redis connection failed:** Ensure `docker-compose up -d redis` is running and port 6379 is available.
- **TEE Unavailable:** Ensure `TEE_MODE=sgx` is set in `.env` for simulation during local dev.
- **Missing proving keys:** zkML requires initial proving keys; ensure `ZKML_PROVING_KEY_STORE` points to a valid directory.
