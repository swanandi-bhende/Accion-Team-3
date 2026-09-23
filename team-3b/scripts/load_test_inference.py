import asyncio
import time
import base64
import argparse
from httpx import AsyncClient

# This script is meant to be run against the live docker-compose stack
# python scripts/load_test_inference.py --url http://localhost:8000

async def fire_request(client: AsyncClient, url: str, model_id: str, payload_bytes: bytes, req_id: int):
    payload = {
        "model_id": model_id,
        "input_data": base64.b64encode(payload_bytes).decode('utf-8'),
        "task_id": f"load_test_{req_id}"
    }
    
    start_time = time.time()
    try:
        response = await client.post(f"{url}/inference", json=payload, timeout=35.0)
        duration = time.time() - start_time
        return req_id, response.status_code, duration
    except Exception as e:
        duration = time.time() - start_time
        return req_id, str(e), duration

async def main():
    parser = argparse.ArgumentParser(description="Load Test Inference Engine")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="API Base URL")
    parser.add_argument("--concurrency", type=int, default=50, help="Number of concurrent requests")
    args = parser.parse_args()
    
    print(f"Starting Load Test against {args.url} with {args.concurrency} concurrent requests.")
    
    # We assume 'toy_linear' is seeded by scripts/seed_model_registry.py
    # Some requests will use a valid model, some an invalid one to test isolation
    
    async with AsyncClient() as client:
        tasks = []
        for i in range(args.concurrency):
            model_id = "toy_linear" if i % 2 == 0 else "invalid_model_for_404"
            tasks.append(fire_request(client, args.url, model_id, f"load_test_input_{i}".encode('utf-8'), i))
            
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
