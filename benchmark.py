#!/usr/bin/env python3
"""
Lambda-G V3 — Hybrid Scoring
=============================
Core insight: BalancedAllocation is good at "make this node even."
Cosine alignment is good at "send this pod to the RIGHT node."
Combine them.

V3 formula:
  variance_score = how balanced the node will be after placement
  alignment_score = how well the pod's shape matches the node's gap
  pressure_gate = hard penalty near exhaustion

  score = w_var × variance_score + w_align × alignment_score - pressure

We iterate over weight combinations to find what actually wins.
"""

import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Tuple

PHI = 1.618033988749895
N_DIMS = 6
DIM_NAMES = ['CPU', 'RAM', 'GPU-Comp', 'GPU-Mem', 'IOPS', 'Network']

COST = {
    'cpu': 34.50, 'ram': 4.31, 'gpu_compute': 150,
    'gpu_memory': 50, 'iops': 0.10, 'network': 2.0,
}


def cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a > 1e-10 and mag_b > 1e-10 else 0


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
# SCORING FUNCTIONS
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


def make_lambda_g_v3(w_var, w_align, w_headroom):
    """Factory: creates a V3 scorer with given weights."""

    def sc_lambda_g_v3(node, pod):
        if not node.can_fit(pod.req):
            return -1

        active_dims = [i for i in range(N_DIMS) if node.capacity[i] > 0]
        if not active_dims:
            return 0

        # ─── Component 1: Post-placement variance (from BalancedAllocation) ───
        after_frac = []
        for i in active_dims:
            after_frac.append((node.used[i] + pod.req[i]) / node.capacity[i])
        mean = sum(after_frac) / len(after_frac)
        var = sum((x - mean) ** 2 for x in after_frac) / len(after_frac)
        variance_score = max(0, (1.0 - var * 4) * 100)

        # ─── Component 2: Cosine alignment (directional match) ───
        node_free = node.free_frac()
        pod_frac = [
            pod.req[i] / node.capacity[i] if node.capacity[i] > 0 else 0
            for i in range(N_DIMS)
        ]
        nf = [node_free[i] for i in active_dims]
        pf = [pod_frac[i] for i in active_dims]
        alignment = cosine_sim(nf, pf)
        alignment_score = alignment * 100

        # ─── Component 3: Headroom (don't over-pack one node) ───
        headroom = sum(nf) / len(nf)
        headroom_score = headroom * 100

        # ─── Component 4: Pressure gate (hard penalty near exhaustion) ───
        pressure = 0
        for i in active_dims:
            used_after = (node.used[i] + pod.req[i]) / node.capacity[i]
            if used_after > 0.92:
                pressure += (used_after - 0.92) * 500  # sharp cliff
            elif used_after > 0.85:
                pressure += (used_after - 0.85) * 50   # gentle slope

        # ─── Component 5: Stranding penalty ───
        # If placement would create a stranded dimension (one > 80%, another < 20%)
        strand_penalty = 0
        for i in range(len(active_dims)):
            for j in range(i + 1, len(active_dims)):
                ui = after_frac[i]
                uj = after_frac[j]
                if (ui > 0.80 and uj < 0.20) or (uj > 0.80 and ui < 0.20):
                    strand_penalty += 15

        raw = (w_var * variance_score +
               w_align * alignment_score +
               w_headroom * headroom_score -
               pressure - strand_penalty)

        return max(0, min(100, raw))

    return sc_lambda_g_v3


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
    wins = 0  # not used here, just aggregates

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
◊═══════════════════════════════════════════════════════════════════════════════◊
  LAMBDA-G V3 — WEIGHT SEARCH + FULL BENCHMARK
  φ = {PHI}
◊═══════════════════════════════════════════════════════════════════════════════◊
""")

    # ─── Phase 1: Grid search over weight combinations ───
    print("=" * 75)
    print(" PHASE 1: Weight Grid Search")
    print(" Finding optimal (w_var, w_align, w_headroom) combination")
    print("=" * 75)

    # Also evaluate baselines
    baselines = [
        ("LeastAllocated", sc_least_alloc),
        ("MostAllocated", sc_most_alloc),
        ("BalancedAllocation", sc_balanced),
        ("DominantResource", sc_dominant),
    ]

    baseline_results = {}
    for name, fn in baselines:
        baseline_results[name] = eval_strategy(name, fn)

    print(f"\n  Baselines:")
    print(f"  {'Strategy':<25} {'Avg Bal':>8} {'Stranded':>9} {'Waste $':>10} {'Pending':>8}")
    print(f"  {'─' * 62}")
    for name in baseline_results:
        r = baseline_results[name]
        print(f"  {name:<25} {r['avg_balance']:>8.1f} {r['total_stranded']:>9} ${r['total_waste']:>9.0f} {r['total_pending']:>8}")

    bal_balance = baseline_results["BalancedAllocation"]["avg_balance"]
    bal_stranded = baseline_results["BalancedAllocation"]["total_stranded"]

    # Grid search
    print(f"\n  Searching weight space...")
    print(f"  Target: beat BalancedAllocation (avg_balance={bal_balance}, stranded={bal_stranded})")
    print()

    best = None
    best_score = -1
    all_candidates = []

    for w_var_10 in range(3, 9):          # 0.3 to 0.8
        for w_align_10 in range(1, 6):    # 0.1 to 0.5
            for w_head_10 in range(0, 4):  # 0.0 to 0.3
                w_var = w_var_10 / 10.0
                w_align = w_align_10 / 10.0
                w_head = w_head_10 / 10.0

                # Normalize
                total_w = w_var + w_align + w_head
                if total_w < 0.01:
                    continue

                fn = make_lambda_g_v3(w_var, w_align, w_head)
                r = eval_strategy("test", fn)

                # Score: balance matters most, then stranded, then waste
                composite = (r['avg_balance'] * 2
                             - r['total_stranded'] * 0.5
                             - r['total_waste'] / 10000)

                all_candidates.append((w_var, w_align, w_head, r, composite))

                if composite > best_score:
                    best_score = composite
                    best = (w_var, w_align, w_head, r)

    # Sort by composite score, show top 10
    all_candidates.sort(key=lambda x: -x[4])

    print(f"  {'Rank':<5} {'w_var':>6} {'w_aln':>6} {'w_hd':>6} {'Bal':>7} {'Str':>5} {'Waste':>9} {'Pend':>6} {'Comp':>8}")
    print(f"  {'─' * 65}")
    for rank, (wv, wa, wh, r, comp) in enumerate(all_candidates[:10], 1):
        print(f"  {rank:<5} {wv:>6.1f} {wa:>6.1f} {wh:>6.1f} {r['avg_balance']:>7.1f} {r['total_stranded']:>5} ${r['total_waste']:>8.0f} {r['total_pending']:>6} {comp:>8.1f}")

    w_v, w_a, w_h, best_r = best
    print(f"\n  ★ Best weights: w_var={w_v}, w_align={w_a}, w_headroom={w_h}")
    print(f"    Avg Balance: {best_r['avg_balance']} (BalancedAlloc: {bal_balance})")
    print(f"    Stranded: {best_r['total_stranded']} (BalancedAlloc: {bal_stranded})")

    beats_bal = best_r['avg_balance'] > bal_balance
    print(f"\n    {'✅ BEATS' if beats_bal else '❌ LOSES TO'} BalancedAllocation on avg balance")

    # ─── Phase 2: Full benchmark with best weights ───
    print(f"\n{'=' * 75}")
    print(f" PHASE 2: Full Benchmark — Best V3 vs All Strategies")
    print(f"{'=' * 75}")

    strategies = [
        ("LeastAllocated",     sc_least_alloc),
        ("MostAllocated",      sc_most_alloc),
        ("BalancedAllocation", sc_balanced),
        ("DominantResource",   sc_dominant),
        (f"Lambda-G V3 ({w_v}/{w_a}/{w_h})", make_lambda_g_v3(w_v, w_a, w_h)),
    ]
    short = ["LeastAll", "MostAll", "BalAlloc", "DomRes", "LG-V3"]

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
        print(f"  {'─' * 70}")

        for key, label, hb in [
            ('balance', 'Balance', True), ('stranded', 'Stranded', False),
            ('sched_pct', 'Sched %', True), ('waste', 'Waste $', False)]:
            vals = [results[n][key] for n, _ in strategies]
            best_v = max(vals) if hb else min(vals)
            print(f"  {label:<18}", end="")
            for v in vals:
                m = " ★" if v == best_v and vals.count(best_v) == 1 else "  "
                if key == 'waste':
                    print(f" ${v:>8.0f}{m}", end="")
                else:
                    print(f" {v:>9}{m}", end="")
            print()

    # Grand summary
    print(f"\n{'═' * 85}")
    print(f" GRAND SUMMARY")
    print(f"{'═' * 85}\n")

    print(f"  {'Scenario':<35}", end="")
    for s in short:
        print(f" {s:>9}", end="")
    print(f" {'Winner':>13}")
    print(f"  {'─' * 95}")

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
            m = " ★" if n == w else "  "
            print(f" {scores[n]:>7.1f}{m}", end="")
        print(f"  {w.split('(')[0].strip():>11}")

    print(f"\n  {'─' * 95}")
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

    lg_name = strategies[4][0]
    ba_name = strategies[2][0]

    print(f"""
◊═══════════════════════════════════════════════════════════════════════════════◊
  VERDICT
  Lambda-G V3 wins: {wins[lg_name]}/5
  BalancedAllocation wins: {wins[ba_name]}/5
  Lambda-G V3 total stranded: {ts[lg_name]} (BalAlloc: {ts[ba_name]})
  Lambda-G V3 total waste: ${tw[lg_name]:.0f} (BalAlloc: ${tw[ba_name]:.0f})
  Best weights: w_var={w_v}  w_align={w_a}  w_headroom={w_h}
  φ = {PHI}
◊═══════════════════════════════════════════════════════════════════════════════◊
""")


if __name__ == "__main__":
    main()
