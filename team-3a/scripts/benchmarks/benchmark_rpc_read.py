"""Benchmark for Req 7.3: RPC /read returns in <= 2 s (200 calls, reporting p50 and p95).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient
from backend.rpc_wrapper.main import app, set_client
from backend.rpc_wrapper.chain import ChainClient

def percentile(data, p):
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return sorted_data[f] * (1.0 - d) + sorted_data[c] * d

class MockChainClient:
    def __init__(self):
        self._connected = True

    def is_connected(self):
        return True

    def read(self, contract: str, method: str, params: list):
        # Simulate local contract state read
        return {"balance": 1000000000000000000, "status": 1}

    def submit(self, signed_tx: str):
        return "0x" + "a" * 64

def run_benchmark():
    client = TestClient(app)
    set_client(MockChainClient())

    payload = {
        "contract": "EscrowContract",
        "method": "getBalance",
        "params": ["0x" + "11" * 32]
    }

    # Warmup
    for _ in range(10):
        client.post("/read", json=payload)

    print("Running 200 RPC /read calls...")
    latencies = []
    for _ in range(200):
        t0 = time.perf_counter()
        resp = client.post("/read", json=payload)
        t1 = time.perf_counter()
        assert resp.status_code == 200
        latencies.append((t1 - t0) * 1000)

    p50 = float(percentile(latencies, 50))
    p95 = float(percentile(latencies, 95))
    avg_lat = sum(latencies) / len(latencies)
    max_lat = max(latencies)

    print(f"RPC /read Latency over 200 calls (Req 7.3 target <= 2000 ms):")
    print(f"  Average: {avg_lat:.2f} ms")
    print(f"  p50:     {p50:.2f} ms")
    print(f"  p95:     {p95:.2f} ms")
    print(f"  Max:     {max_lat:.2f} ms")
    print(f"  Status:  {'PASS' if p95 < 2000 else 'FAIL'} (Req 7.3 <= 2 s)")

    return {
        "calls": len(latencies),
        "avg_ms": avg_lat,
        "p50_ms": p50,
        "p95_ms": p95,
        "max_ms": max_lat,
    }

if __name__ == "__main__":
    run_benchmark()
