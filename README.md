# Lambda-G Auditor

**Free 6D resource imbalance scanner for Kubernetes.**

Scans your cluster across 6 dimensions — CPU, RAM, GPU Core, GPU Memory, IOPS, Network — and finds nodes where resources are stranded.

Most clusters waste 10-20% of compute budget this way. GPU clusters waste even more — VRAM full but compute idle, or compute maxed but VRAM unused.

## Quick Start (30 seconds)

```bash
git clone https://github.com/0x-auth/lambda-g-auditor
cd lambda-g-auditor
pip install kubernetes colorama
python3 auditor.py           # CPU + RAM scan
python3 auditor.py --gpu     # Include GPU metrics (Koordinator)
```

That's it. Connects to your current kubectl context and scans every node.

## What It Shows

```
🔍 [Lambda-G] 6D Resource Imbalance Scanner
   φ = 1.618033988749895
   GPU scanning: ON (koordinator.sh/gpu-core, gpu-memory)

Node                 | CPU%   | RAM%   | GPU%   | VRAM%  | Status
---------------------------------------------------------------------------
gpu-node-01          |  92.3% |  18.7% |  10.2% |  95.0% | CRITICAL: CPU stranded (VRAM maxed)
gpu-node-02          |  45.1% |  51.2% |  80.0% |  75.3% | Balanced
cpu-node-03          |  88.9% |  22.4% |    n/a |    n/a | Leaking: RAM stranded (CPU maxed)
cpu-node-04          |  31.6% |  89.1% |    n/a |    n/a | Leaking: CPU stranded (RAM maxed)
---------------------------------------------------------------------------

💰 Estimated Monthly Waste: $4,247.00

📊 Cluster Summary:
   Nodes: 4
   CPU: 12.8/16.0 cores (80.0%)
   RAM: 28.4/64.0 GB (44.4%)
   GPU Nodes: 2
   Imbalanced: 3/4 nodes
```

**"CRITICAL"** = >60% imbalance between any two dimensions.
**"Leaking"** = >30% imbalance. You're paying for idle resources.

Now detects GPU imbalance: VRAM full but compute idle (common in LLM inference), or compute maxed but VRAM unused.

## Run the Benchmark

See how Lambda-G V3 scheduling compares to 4 baseline strategies across 5 scenarios:

```bash
python3 benchmark.py
```

Tests 5 strategies (LeastAlloc, MostAlloc, BalancedAlloc, DominantRes, Lambda-G V3) across:
- Mixed GPU — AI Workload (30 nodes × 120 pods)
- GPU — Inference Heavy (20 nodes × 80 pods)
- GPU — Training Heavy (20 nodes × 60 pods)
- CPU + Few GPUs (25 nodes × 100 pods)
- Scale (60 nodes × 300 pods)

**V3 scoring formula:**

```
score = 0.6 × variance + 0.2 × alignment + 0.1 × headroom - penalties
```

Where `variance` = post-placement balance, `alignment` = cosine similarity between pod request and node free-space vectors, `headroom` = average remaining capacity, and `penalties` = pressure gate + stranding penalty.

**Results:** Lambda-G V3 wins 5/5 scenarios, with 23% fewer stranded nodes and 53% less waste vs BalancedAllocation.

## How It Works

The auditor reads node capacity and pod resource requests from the K8s API. For each node it calculates:

```
CPU usage % = sum(pod CPU requests) / node CPU capacity
RAM usage % = sum(pod RAM requests) / node RAM capacity

If CPU% > 90% AND RAM% < 60% → STRANDED RAM (you're paying for it)
If RAM% > 90% AND CPU% < 60% → STRANDED CPU (you're paying for it)
```

Monthly waste is estimated using average cloud pricing:
- CPU: $30/core/month
- RAM: $5/GB/month

## Requirements

- Python 3.9+
- `kubectl` configured and connected to your cluster
- `pip install kubernetes colorama`

## What's Next?

If the auditor finds stranded resources, Lambda-G can fix it. Lambda-G is a scheduling engine that steers CPU-heavy pods toward RAM-heavy nodes (and vice versa), achieving **symmetric exhaustion** — all resource dimensions drain evenly.

**Want to try the fix?** → [Contact us](mailto:bitsabhi@gmail.com)

## FAQ

**Is this safe to run on production?**
Yes. The auditor is read-only. It only calls `list_node()` and `list_pod_for_all_namespaces()`. It does not modify anything.

**How long does the scan take?**
Under 5 seconds for clusters up to 100 nodes.

**Can I run this on EKS/GKE/AKS?**
Yes. Anywhere `kubectl get nodes` works, the auditor works.

**Is this open source?**
The auditor and benchmark are open source (MIT). The scheduling engine is a separate commercial product.

## License

MIT — use it however you want.

## Author

Abhishek Srivastava — [github.com/0x-auth](https://github.com/0x-auth)

φ = 1.618033988749895
