#!/usr/bin/env python3
"""
Cluster Scheduling Baseline Comparison
=======================================
Compare 4 built-in Kubernetes scheduling strategies across realistic
6-dimensional workloads (CPU, RAM, GPU-Compute, GPU-Memory, IOPS, Network).

Strategies tested:
  - LeastAllocated   (spread pods across nodes)
  - MostAllocated    (bin-pack onto fewest nodes)
  - BalancedAllocation (minimize per-node variance)
  - DominantResource  (minimize bottleneck dimension)

For Lambda-G scoring results, see the design proposals at:
  https://github.com/kai-scheduler/KAI-Scheduler/pull/1374
"""

import math
import random
from dataclasses import dataclass, field
from typing import List, Callable

PHI = 1.618033988749895
N_DIMS = 6
DIM_NAMES = ['CPU', 'RAM', 'GPU-Comp', 'GPU-Mem', 'IOPS', 'Network']

COST = {
    'cpu': 34.50, 'ram': 4.31, 'gpu_compute': 150,
    'gpu_memory': 50, 'iops': 0.10, 'network': 2.0,
}


@dataclass
class Node:
    name: str
    capacity: List[float]
    used: List[float] = field(default_factory=lambda: [0.0] * N_DIMS)
    pods: int = 0

    def free(self):
        return [max(0, self.capacity[i] - self.used[i]) for i in range(N_DIMS)]

    def free_frac(self):
        return [self.free()[i] / self.capacity[i] if self.capacity[i] > 0 else 0
                for i in range(N_DIMS)]

    def used_frac(self):
        return [self.used[i] / self.capacity[i] if self.capacity[i] > 0 else 0
                for i in range(N_DIMS)]

    def can_fit(self, req):
        return all(self.free()[i] >= req[i] for i in range(N_DIMS))

    def place(self, req):
        for i in range(N_DIMS):
            self.used[i] += req[i]
        self.pods += 1


@dataclass
class Pod:
    name: str
    req: List[float]


# ═══════════════════════════════════════════════════════════════
# SCORING FUNCTIONS (4 built-in K8s strategies)
# ═══════════════════════════════════════════════════════════════

def sc_least_alloc(node, pod):
    if not node.can_fit(pod.req):
        return -1
    ff = node.free_frac()
    active = [ff[i] for i in range(N_DIMS) if node.capacity[i] > 0]
    return (sum(active) / len(active)) * 100 if active else 0


def sc_most_alloc(node, pod):
    if not node.can_fit(pod.req):
        return -1
    uf = node.used_frac()
    active = [uf[i] for i in range(N_DIMS) if node.capacity[i] > 0]
    return (sum(active) / len(active)) * 100 if active else 0


def sc_balanced(node, pod):
    if not node.can_fit(pod.req):
        return -1
    after = []
    for i in range(N_DIMS):
        if node.capacity[i] > 0:
            after.append((node.used[i] + pod.req[i]) / node.capacity[i])
    if not after:
        return 0
    mean = sum(after) / len(after)
    var = sum((x - mean) ** 2 for x in after) / len(after)
    return max(0, (1.0 - var * 4) * 100)


def sc_dominant(node, pod):
    if not node.can_fit(pod.req):
        return -1
    after = []
    for i in range(N_DIMS):
        if node.capacity[i] > 0:
            after.append((node.used[i] + pod.req[i]) / node.capacity[i])
    if not after:
        return 0
    return (1.0 - max(after)) * 100


# ═══════════════════════════════════════════════════════════════
# NODE + POD GENERATORS
# ═══════════════════════════════════════════════════════════════

def make_cpu_node(n):     return Node(n, [16, 32, 0, 0, 50, 10])
def make_ram_node(n):     return Node(n, [8, 128, 0, 0, 50, 10])
def make_gpu_inf(n):      return Node(n, [8, 32, 50, 80, 100, 25])
def make_gpu_train(n):    return Node(n, [32, 128, 100, 80, 200, 100])
def make_balanced(n):     return Node(n, [8, 32, 0, 0, 50, 10])

def gen_llm_serve(i):
    return Pod(f"llm-{i}", [random.uniform(1,3), random.uniform(8,16),
        random.uniform(5,15), random.uniform(30,60), random.uniform(5,15), random.uniform(2,8)])
def gen_batch_inf(i):
    return Pod(f"batch-{i}", [random.uniform(0.5,2), random.uniform(2,8),
        random.uniform(20,40), random.uniform(5,15), random.uniform(10,30), random.uniform(1,5)])
def gen_training(i):
    return Pod(f"train-{i}", [random.uniform(4,16), random.uniform(16,64),
        random.uniform(30,80), random.uniform(20,60), random.uniform(50,150), random.uniform(10,50)])
def gen_preprocess(i):
    return Pod(f"pre-{i}", [random.uniform(2,8), random.uniform(4,16),
        0, 0, random.uniform(20,80), random.uniform(2,10)])
def gen_api(i):
    return Pod(f"api-{i}", [random.uniform(0.2,1), random.uniform(0.5,2),
        0, 0, random.uniform(1,5), random.uniform(1,5)])
def gen_etl(i):
    return Pod(f"etl-{i}", [random.uniform(1,4), random.uniform(8,32),
        0, 0, random.uniform(50,150), random.uniform(5,20)])

def gen_workload(n, mix):
    pods = []
    for i in range(n):
        r = random.random()
        cum = 0
        for prob, gen_fn in mix:
            cum += prob
            if r < cum:
                pods.append(gen_fn(i))
                break
    return pods

MIX_AI = [(0.15, gen_llm_serve), (0.15, gen_batch_inf), (0.10, gen_training),
           (0.25, gen_preprocess), (0.20, gen_api), (0.15, gen_etl)]
MIX_INF = [(0.35, gen_llm_serve), (0.25, gen_batch_inf), (0.05, gen_training),
            (0.15, gen_preprocess), (0.15, gen_api), (0.05, gen_etl)]
MIX_TRAIN = [(0.10, gen_llm_serve), (0.10, gen_batch_inf), (0.35, gen_training),
              (0.20, gen_preprocess), (0.10, gen_api), (0.15, gen_etl)]
MIX_CPU = [(0.05, gen_llm_serve), (0.05, gen_batch_inf), (0.0, gen_training),
            (0.40, gen_preprocess), (0.30, gen_api), (0.20, gen_etl)]

def make_mixed_cluster():
    nodes = []
    for i in range(8):  nodes.append(make_cpu_node(f"cpu-{i}"))
    for i in range(4):  nodes.append(make_ram_node(f"ram-{i}"))
    for i in range(8):  nodes.append(make_gpu_inf(f"gpu-inf-{i}"))
    for i in range(4):  nodes.append(make_gpu_train(f"gpu-train-{i}"))
    for i in range(6):  nodes.append(make_balanced(f"bal-{i}"))
    return nodes

def make_gpu_cluster():
    nodes = []
    for i in range(4):  nodes.append(make_cpu_node(f"cpu-{i}"))
    for i in range(2):  nodes.append(make_ram_node(f"ram-{i}"))
    for i in range(8):  nodes.append(make_gpu_inf(f"gpu-inf-{i}"))
    for i in range(6):  nodes.append(make_gpu_train(f"gpu-train-{i}"))
    return nodes

def make_cpu_plus_gpu():
    nodes = []
    for i in range(10): nodes.append(make_cpu_node(f"cpu-{i}"))
    for i in range(6):  nodes.append(make_ram_node(f"ram-{i}"))
    for i in range(3):  nodes.append(make_gpu_inf(f"gpu-inf-{i}"))
    for i in range(6):  nodes.append(make_balanced(f"bal-{i}"))
    return nodes


SCENARIOS = [
    {"name": "Mixed GPU — AI Workload",
     "cluster": make_mixed_cluster, "mix": MIX_AI, "n": 120},
    {"name": "GPU — Inference Heavy",
     "cluster": make_gpu_cluster, "mix": MIX_INF, "n": 80},
    {"name": "GPU — Training Heavy",
     "cluster": make_gpu_cluster, "mix": MIX_TRAIN, "n": 60},
    {"name": "CPU + Few GPUs",
     "cluster": make_cpu_plus_gpu, "mix": MIX_CPU, "n": 100},
    {"name": "Scale (60n×300p)",
     "cluster": lambda: make_mixed_cluster() + make_mixed_cluster(),
     "mix": MIX_AI, "n": 300},
]


# ═══════════════════════════════════════════════════════════════
# METRICS + SIMULATION
# ═══════════════════════════════════════════════════════════════

def calc_metrics(nodes, pending, total):
    active = [n for n in nodes if n.pods > 0]
    per_node_imb = []
    for n in active:
        uf = n.used_frac()
        dims = [uf[i] for i in range(N_DIMS) if n.capacity[i] > 0]
        if len(dims) < 2:
            continue
        mean = sum(dims) / len(dims)
        per_node_imb.append(sum((x - mean) ** 2 for x in dims) / len(dims))

    avg_imb = sum(per_node_imb) / len(per_node_imb) if per_node_imb else 0

    stranded = 0
    for n in active:
        uf = n.used_frac()
        active_uf = [uf[i] for i in range(N_DIMS) if n.capacity[i] > 0]
        if len(active_uf) >= 2 and max(active_uf) > 0.70 and min(active_uf) < 0.30:
            stranded += 1

    sched_rate = (total - pending) / total if total > 0 else 1
    imb_score = max(0, 100 - avg_imb * 400)
    balance = imb_score * 0.7 + sched_rate * 100 * 0.3

    cost_keys = ['cpu', 'ram', 'gpu_compute', 'gpu_memory', 'iops', 'network']
    waste = 0
    for n in active:
        uf = n.used_frac()
        active_uf = [(i, uf[i]) for i in range(N_DIMS) if n.capacity[i] > 0]
        if len(active_uf) >= 2 and max(x for _, x in active_uf) > 0.70:
            for dim, u in active_uf:
                if u < 0.30:
                    waste += n.free()[dim] * COST[cost_keys[dim]]

    return {
        "balance": round(balance, 1),
        "imbalance": round(avg_imb, 4),
        "stranded": stranded,
        "sched_pct": round(sched_rate * 100, 1),
        "pending": pending,
        "waste": round(waste, 0),
    }


def simulate(nodes, pods, score_fn):
    pending = 0
    for pod in pods:
        scores = [(score_fn(n, pod), i, n) for i, n in enumerate(nodes)]
        scores.sort(key=lambda x: (-x[0], x[1]))
        placed = False
        for sc, _, n in scores:
            if sc > 0 and n.can_fit(pod.req):
                n.place(pod.req)
                placed = True
                break
        if not placed:
            pending += 1
    return calc_metrics(nodes, pending, len(pods))


def eval_strategy(name, score_fn, seed=42):
    """Run all scenarios, return aggregate metrics."""
    total_balance = 0
    total_stranded = 0
    total_waste = 0
    total_pending = 0

    results = {}
    for sc in SCENARIOS:
        random.seed(seed)
        nodes = sc["cluster"]()
        pods = gen_workload(sc["n"], sc["mix"])
        random.shuffle(pods)
        m = simulate(nodes, pods, score_fn)
        results[sc["name"]] = m
        total_balance += m["balance"]
        total_stranded += m["stranded"]
        total_waste += m["waste"]
        total_pending += m["pending"]

    return {
        "results": results,
        "avg_balance": round(total_balance / len(SCENARIOS), 1),
        "total_stranded": total_stranded,
        "total_waste": total_waste,
        "total_pending": total_pending,
    }


def main():
    print(f"""
═══════════════════════════════════════════════════════════════════════════════
  CLUSTER SCHEDULING BASELINE COMPARISON
  6-dimensional workloads: {', '.join(DIM_NAMES)}
═══════════════════════════════════════════════════════════════════════════════
""")

    strategies = [
        ("LeastAllocated",     sc_least_alloc),
        ("MostAllocated",      sc_most_alloc),
        ("BalancedAllocation",  sc_balanced),
        ("DominantResource",    sc_dominant),
    ]
    short = ["LeastAll", "MostAll", "BalAlloc", "DomRes"]

    # ─── Aggregate summary ───
    print(f"  {'Strategy':<25} {'Avg Bal':>8} {'Stranded':>9} {'Waste $':>10} {'Pending':>8}")
    print(f"  {'─' * 62}")

    agg = {}
    for name, fn in strategies:
        agg[name] = eval_strategy(name, fn)
        r = agg[name]
        print(f"  {name:<25} {r['avg_balance']:>8.1f} {r['total_stranded']:>9} ${r['total_waste']:>9.0f} {r['total_pending']:>8}")

    # ─── Per-scenario breakdown ───
    all_results = {}
    for sc in SCENARIOS:
        print(f"\n  ── {sc['name']} ──")
        results = {}
        for sname, sfn in strategies:
            random.seed(42)
            nodes = sc["cluster"]()
            pods = gen_workload(sc["n"], sc["mix"])
            random.shuffle(pods)
            results[sname] = simulate(nodes, pods, sfn)
        all_results[sc["name"]] = results

        print(f"  {'Metric':<18}", end="")
        for s in short:
            print(f" {s:>10}", end="")
        print()
        print(f"  {'─' * 60}")

        for key, label, hb in [
            ('balance', 'Balance', True), ('stranded', 'Stranded', False),
            ('sched_pct', 'Sched %', True), ('waste', 'Waste $', False)]:
            vals = [results[n][key] for n, _ in strategies]
            best_v = max(vals) if hb else min(vals)
            print(f"  {label:<18}", end="")
            for v in vals:
                m = " *" if v == best_v and vals.count(best_v) == 1 else "  "
                if key == 'waste':
                    print(f" ${v:>8.0f}{m}", end="")
                else:
                    print(f" {v:>9}{m}", end="")
            print()

    # ─── Grand summary ───
    print(f"\n{'=' * 75}")
    print(f" GRAND SUMMARY")
    print(f"{'=' * 75}\n")

    print(f"  {'Scenario':<35}", end="")
    for s in short:
        print(f" {s:>9}", end="")
    print(f" {'Winner':>13}")
    print(f"  {'─' * 85}")

    wins = {n: 0 for n, _ in strategies}
    tw = {n: 0 for n, _ in strategies}
    ts = {n: 0 for n, _ in strategies}

    for sn, res in all_results.items():
        scores = {n: res[n]['balance'] for n, _ in strategies}
        w = max(scores, key=scores.get)
        wins[w] += 1
        for n, _ in strategies:
            tw[n] += res[n]['waste']
            ts[n] += res[n]['stranded']
        print(f"  {sn:<35}", end="")
        for n, _ in strategies:
            m = " *" if n == w else "  "
            print(f" {scores[n]:>7.1f}{m}", end="")
        print(f"  {w:>11}")

    print(f"\n  {'─' * 85}")
    print(f"  {'Wins':<35}", end="")
    for n, _ in strategies:
        print(f" {wins[n]:>9}", end="")
    print()
    print(f"  {'Total Stranded':<35}", end="")
    for n, _ in strategies:
        print(f" {ts[n]:>9}", end="")
    print()
    print(f"  {'Total Waste $':<35}", end="")
    for n, _ in strategies:
        print(f" ${tw[n]:>8.0f}", end="")
    print()

    print(f"""
═══════════════════════════════════════════════════════════════════════════════
  NOTE: These are baseline K8s strategies only. None of them account for
  multi-dimensional resource alignment (cosine similarity between pod
  request vectors and node free-space vectors).

  For Lambda-G scoring results, see the design proposals at:
    https://github.com/kai-scheduler/KAI-Scheduler/pull/1374
═══════════════════════════════════════════════════════════════════════════════
""")


if __name__ == "__main__":
    main()
