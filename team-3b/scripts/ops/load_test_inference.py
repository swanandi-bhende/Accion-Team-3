"""Load-test the inference endpoint with concurrent requests and report latency.

Fires ``--concurrency`` POST requests at ``/inference``, alternating the seeded
``toy_linear`` model with an unknown one, then prints status counts and latency.

Every request is signed by the worker of ``--task-id``, whose private key is read from the
environment variable named by ``--worker-key-env``. The task must exist on chain with that worker.
"""

import argparse
import asyncio
import base64
import os
import time
from typing import Any

import numpy as np
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from httpx import AsyncClient

from common.tensors import encode_tensor


def _load_input(index: int) -> np.ndarray:
    """A distinct 1x4 float32 input per request, matching the seeded toy models."""
    return np.asarray(np.random.default_rng(seed=index).normal(size=(1, 4)), dtype=np.float32)

# This script is meant to be run against the live docker-compose stack
# python scripts/ops/load_test_inference.py --url http://localhost:8000

async def signed_payload(
    client: AsyncClient, url: str, task_id: str, worker: LocalAccount, model_id: str, payload_bytes: bytes
) -> dict[str, Any]:
    """An /inference body with a fresh challenge from the service, signed by the task's worker."""
    challenge = (
        await client.post(f"{url}/challenges", json={"task_id": task_id, "wallet_address": worker.address})
    ).json()
    signature = worker.sign_message(encode_defunct(text=challenge["message"])).signature
    return {
        "task_id": task_id,
        "model_id": model_id,
        "input_data": base64.b64encode(payload_bytes).decode("utf-8"),
        "authorization": {
            "wallet_address": worker.address,
            "nonce": challenge["nonce"],
            "expires_at": challenge["expires_at"],
            "signature": "0x" + bytes(signature).hex(),
        },
    }


async def fire_request(
    client: AsyncClient, url: str, task_id: str, worker: LocalAccount, model_id: str, payload_bytes: bytes, req_id: int
) -> tuple[int, int | str, float]:
    """Send one signed inference request and time it. The timing includes the challenge round-trip.

    Returns:
        ``(req_id, status_code, seconds)``. If the request raised, the second
        item is the exception message instead of a status code.
    """
    start_time = time.time()
    try:
        payload = await signed_payload(client, url, task_id, worker, model_id, payload_bytes)
        response = await client.post(f"{url}/inference", json=payload, timeout=35.0)
        duration = time.time() - start_time
        return req_id, response.status_code, duration
    except Exception as e:
        duration = time.time() - start_time
        return req_id, str(e), duration

async def main() -> None:
    """Parse ``--url`` and ``--concurrency``, run the load test, and print a summary."""
    parser =argparse.ArgumentParser(description="Load Test Inference Engine")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="API Base URL")
    parser.add_argument("--concurrency", type=int, default=50, help="Number of concurrent requests")
    parser.add_argument("--task-id", required=True, help="Team A task id whose worker signs the requests")
    parser.add_argument("--worker-key-env", default="WORKER_PRIVATE_KEY", help="Env var holding the worker's private key")
    args = parser.parse_args()
    key = os.environ.get(args.worker_key_env)
    if not key:
        parser.error(f"set {args.worker_key_env} to the task worker's private key")
    worker = Account.from_key(key)

    print(f"Starting Load Test against {args.url} with {args.concurrency} concurrent requests.")

    # We assume 'toy_linear' is seeded by scripts/ops/seed_model_registry.py
    # Some requests will use a valid model, some an invalid one to test isolation

    async with AsyncClient() as client:
        tasks = []
        for i in range(args.concurrency):
            model_id = "toy_linear" if i % 2 == 0 else "invalid_model_for_404"
            tasks.append(fire_request(client, args.url, args.task_id, worker, model_id, encode_tensor(_load_input(i)), i))

        print("Firing requests...")
        results = await asyncio.gather(*tasks)

    successes = sum(1 for r in results if r[1] == 200)
    not_founds = sum(1 for r in results if r[1] == 404)
    timeouts = sum(1 for r in results if r[1] == 408)
    others = len(results) - successes - not_founds - timeouts

    max_latency = max(r[2] for r in results)
    avg_latency = sum(r[2] for r in results) / len(results)

    print("\n=== Load Test Results ===")
    print(f"Total Requests: {len(results)}")
    print(f"200 OK:         {successes}")
    print(f"404 Not Found:  {not_founds}")
    print(f"408 Timeout:    {timeouts}")
    print(f"Other/Errors:   {others}")
    print(f"Max Latency:    {max_latency:.2f}s")
    print(f"Avg Latency:    {avg_latency:.2f}s")

    # Requirement 1.1: strict 30s timeout
    if max_latency > 31.0: # small buffer for network overhead
        print("WARNING: Max latency exceeded 30s strict timeout bound!")
    else:
        print("SUCCESS: Latency remained within the 30s bound.")

if __name__ == "__main__":
    asyncio.run(main())
