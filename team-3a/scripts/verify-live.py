"""Reachability + round-trip checks for the live Weaviate and NebulaGraph backends.

Reads connection settings from .env (via backend.config), so it works unchanged
against docker-compose or remote endpoints.
"""
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import settings


def check_http(url: str, name: str) -> bool:
    print(f"Checking {name} at {url}...")
    try:
        with urlopen(url, timeout=5) as response:
            if response.status == 200:
                print(f"{name} is reachable.")
                return True
    except Exception:
        pass
    print(f"ERROR: {name} is not reachable at {url}.")
    return False


def check_tcp(host: str, port: int, name: str) -> bool:
    print(f"Checking {name} at {host}:{port}...")
    try:
        with socket.create_connection((host, port), timeout=5):
            print(f"{name} is reachable.")
            return True
    except OSError:
        print(f"ERROR: {name} is not reachable at {host}:{port}.")
        return False


def main() -> None:
    if not check_http(f"{settings.weaviate_url.rstrip('/')}/v1/meta", "Weaviate"):
        print("Please ensure Weaviate is running or set WEAVIATE_URL.")
        raise SystemExit(1)
    if not check_tcp(settings.nebula_host, settings.nebula_port, "NebulaGraph graphd"):
        print("Please ensure NebulaGraph is running or set NEBULA_HOST/NEBULA_PORT.")
        raise SystemExit(1)

    print("Running Weaviate round-trip...")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_weaviate.py")], check=True)
    print("Running NebulaGraph round-trip...")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_nebula.py")], check=True)

    print("Live verification passed.")


if __name__ == "__main__":
    main()
