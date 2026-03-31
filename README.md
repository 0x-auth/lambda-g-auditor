# Lambda-G Auditor

**Free tool to find how much your K8s cluster is wasting on stranded resources.**

Most Kubernetes clusters waste 10-20% of compute budget on nodes where one resource is maxed out while another sits idle. This tool shows you exactly where.

## Quick Start (30 seconds)

```bash
git clone https://github.com/0x-auth/lambda-g-auditor
cd lambda-g-auditor
pip install kubernetes colorama
python3 auditor.py
```

That's it. Connects to your current kubectl context and scans every node.

## What It Shows

```
🔍 [Lambda-G] Scanning for Entropic Leaks...

Node Name            | CPU %    | RAM %    | Status
----------------------------------------------------------------------
node-01              |   92.3%  |   18.7%  | Leaking (Mismatch)
node-02              |   45.1%  |   51.2%  | Balanced
node-03              |   88.9%  |   22.4%  | Leaking (Mismatch)
node-04              |   31.6%  |   89.1%  | Leaking (Mismatch)
----------------------------------------------------------------------

💰 Potential Monthly Recovery: $2,847.00
```

**"Leaking"** = one resource dimension is >90% used while another is <60%. You're paying for the idle resource. Nobody can use it because the other dimension is full.

## Run the Benchmark

See how Lambda-G scheduling compares to default K8s across 5 scenarios:

```bash
python3 benchmark.py
```

Tests 3 strategies (Default, Lambda-G Simple, Lambda-G Full) across:
- Mixed workload (20 nodes × 200 pods)
- Scale test (50 nodes × 500 pods)
- CPU-heavy skew
- RAM-heavy skew
- Dense packing

Lambda-G wins all 5 scenarios with zero stranded nodes in 4/5.

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
