"""Benchmark for Req 10.3: Graph traversal <= 3 s on a graph of 100,000 nodes.
"""
import time
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

def percentile(data, p):
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return sorted_data[f] * (1.0 - d) + sorted_data[c] * d
from backend.graph.client import InMemoryGraph, Edge

def run_benchmark():
    print("Generating graph of 100,000 nodes...")
    t0 = time.perf_counter()
    g = InMemoryGraph()
    
    # 20,000 contracts, each containing 4 clauses = 100,000 nodes total
    num_contracts = 20000
    clauses_per_contract = 4
    
    for c_idx in range(num_contracts):
        c_id = f"c_{c_idx}"
        g.state.add_vertex(c_id, "contract")
        for k_idx in range(clauses_per_contract):
            k_id = f"{c_id}::k::{k_idx}"
            g.state.add_vertex(k_id, "clause")
            g.state.add_edge(c_id, k_id, "contains")
            if k_idx > 0:
                # clause depends on previous clause
                g.state.add_edge(k_id, f"{c_id}::k::{k_idx-1}", "depends_on")
        
        # Add reference chain across contracts every 10 contracts
        if c_idx > 0 and c_idx % 10 == 0:
            g.state.add_edge(c_id, f"c_{c_idx-10}", "references")
            g.state.add_edge(f"c_{c_idx-10}", c_id, "references")

    gen_time = time.perf_counter() - t0
    print(f"Generated {len(g.state.vertices)} vertices and {len(g.state.edges)} edges in {gen_time:.2f} s")

    # Benchmark path traversal queries (find_paths)
    traversal_latencies = []
    print("Running 50 path traversal operations (max_depth=4)...")
    for i in range(50):
        src = f"c_{i * 200}"
        dst = f"c_{i * 200 + 40}"
        t_start = time.perf_counter()
        paths = g.find_paths(src, dst, max_depth=4)
        t_end = time.perf_counter()
        traversal_latencies.append((t_end - t_start) * 1000)

    p50 = float(percentile(traversal_latencies, 50))
    p95 = float(percentile(traversal_latencies, 95))
    max_lat = float(max(traversal_latencies))
    avg_lat = float(statistics.mean(traversal_latencies))

    print(f"Graph Traversal Latency (Req 10.3 target <= 3000 ms):")
    print(f"  Average: {avg_lat:.2f} ms")
    print(f"  p50:     {p50:.2f} ms")
    print(f"  p95:     {p95:.2f} ms")
    print(f"  Max:     {max_lat:.2f} ms")
    print(f"  Status:  {'PASS' if max_lat < 3000 else 'FAIL'} (Req 10.3 <= 3 s)")

    return {
        "nodes": len(g.state.vertices),
        "edges": len(g.state.edges),
        "p50_ms": p50,
        "p95_ms": p95,
        "max_ms": max_lat,
    }

if __name__ == "__main__":
    run_benchmark()
