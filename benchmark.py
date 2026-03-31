#!/usr/bin/env python3
"""
Lambda-G Simulation Benchmark
===============================
Simulates cluster scheduling at realistic scale WITHOUT needing a live cluster.

Tests 3 strategies across multiple scenarios:
- Default K8s (LeastAllocated)
- Lambda-G Simple (2D entropy)
- Lambda-G Full (4D vector alignment)

Scenarios:
- 20 nodes × 200 pods (mixed workload)
- 50 nodes × 500 pods (scale test)
- 10 nodes × 100 pods (CPU-heavy skew)
- 10 nodes × 100 pods (RAM-heavy skew)

Metrics:
- Balance score (variance across nodes)
- Stranded resource %
- Nodes needed before first "Pending" pod
- Estimated monthly $ waste

Run: python3 benchmark_simulation.py
"""

import math
import random
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict

PHI = 1.618033988749895

# ─── AWS Pricing (m5.xlarge baseline: 4 CPU, 16GB RAM) ───
COST_PER_CPU_MONTH = 34.50   # ~$138/mo for 4 CPU
COST_PER_GB_RAM_MONTH = 4.31  # ~$69/mo for 16GB

# ─── Data Structures ───

@dataclass
class Node:
    name: str
    cpu_total: float      # in cores
    ram_total: float      # in GB
    cpu_used: float = 0
    ram_used: float = 0
    pods: int = 0

    @property
    def cpu_free(self): return max(0, self.cpu_total - self.cpu_used)
    @property
    def ram_free(self): return max(0, self.ram_total - self.ram_used)
    @property
    def cpu_pct(self): return self.cpu_used / self.cpu_total * 100 if self.cpu_total > 0 else 0
    @property
    def ram_pct(self): return self.ram_used / self.ram_total * 100 if self.ram_total > 0 else 0
    @property
    def cpu_free_frac(self): return self.cpu_free / self.cpu_total if self.cpu_total > 0 else 0
    @property
    def ram_free_frac(self): return self.ram_free / self.ram_total if self.ram_total > 0 else 0

    def can_fit(self, cpu, ram):
        return self.cpu_free >= cpu and self.ram_free >= ram

    def place(self, cpu, ram):
        self.cpu_used += cpu
        self.ram_used += ram
        self.pods += 1


@dataclass
class Pod:
    name: str
    cpu: float   # in cores
    ram: float   # in GB


# ─── Scoring Functions ───

def default_least_allocated_score(node: Node, pod: Pod) -> float:
    """Kubernetes default LeastAllocated: prefer nodes with most free resources"""
    if not node.can_fit(pod.cpu, pod.ram):
        return -1
    cpu_score = node.cpu_free_frac * 100
    ram_score = node.ram_free_frac * 100
    return (cpu_score + ram_score) / 2


def lambda_g_simple_score(node: Node, pod: Pod) -> float:
    """Lambda-G Simple: 2D entropy reduction + capacity headroom"""
    if not node.can_fit(pod.cpu, pod.ram):
        return -1

    cpu_free = node.cpu_free_frac
    ram_free = node.ram_free_frac
    cpu_req = pod.cpu / node.cpu_total if node.cpu_total > 0 else 1
    ram_req = pod.ram / node.ram_total if node.ram_total > 0 else 1

    # Capacity gate
    if cpu_free < 0.10 or ram_free < 0.10:
        return 0

    initial_entropy = abs(cpu_free - ram_free)
    after_cpu = cpu_free - cpu_req
    after_ram = ram_free - ram_req
    final_entropy = abs(after_cpu - after_ram)

    recovery = initial_entropy - final_entropy
    exhaustion = 1.0 - (after_cpu + after_ram) / 2
    headroom = (cpu_free + ram_free) / 2

    return (recovery * PHI * 50) + (exhaustion * 10) + (headroom * 15)


def lambda_g_full_score(node: Node, pod: Pod) -> float:
    """Lambda-G Full: 4D vector alignment + symmetric exhaustion + entropy penalty"""
    if not node.can_fit(pod.cpu, pod.ram):
        return -1

    node_vec = [node.cpu_free_frac, node.ram_free_frac, 0.5, 0.5]
    pod_vec = [
        pod.cpu / node.cpu_total if node.cpu_total > 0 else 1,
        pod.ram / node.ram_total if node.ram_total > 0 else 1,
        0.05, 0.05
    ]

    # Capacity gate
    if node_vec[0] < 0.10 or node_vec[1] < 0.10:
        return 0

    # Cosine similarity (directional alignment)
    dot = sum(a * b for a, b in zip(pod_vec, node_vec))
    mag_a = math.sqrt(sum(a**2 for a in pod_vec))
    mag_b = math.sqrt(sum(b**2 for b in node_vec))
    alignment = dot / (mag_a * mag_b) if mag_a > 1e-10 and mag_b > 1e-10 else 0

    # Symmetric exhaustion
    after = [max(0, node_vec[i] - pod_vec[i]) for i in range(4)]
    s_before = sum(node_vec)
    s_after = sum(after)

    ent_before = 0
    ent_after = 0
    if s_before > 1e-10:
        ent_before = sum(-((x/s_before) * math.log(x/s_before)) if x/s_before > 1e-10 else 0 for x in node_vec)
    if s_after > 1e-10:
        ent_after = sum(-((x/s_after) * math.log(x/s_after)) if x/s_after > 1e-10 else 0 for x in after)

    recovery = ent_before - ent_after

    mag_before = math.sqrt(sum(x**2 for x in node_vec))
    mag_after = math.sqrt(sum(x**2 for x in after))
    utilization = (mag_before - mag_after) / mag_before if mag_before > 1e-10 else 0

    exhaustion_bonus = PHI * recovery + utilization

    # Entropy leak penalty
    stranded = sum(1 for i in range(4) if after[i] > 0.70 and pod_vec[i] < 0.10)
    penalty = stranded * 0.15

    # Headroom bonus (don't pack one node to 100%)
    headroom = (node_vec[0] + node_vec[1]) / 2

    raw = PHI * alignment + exhaustion_bonus - penalty + headroom * 0.3
    return max(0, raw * 30 + 50)


# ─── Scheduling Simulator ───

def simulate_scheduling(nodes: List[Node], pods: List[Pod], score_fn) -> Dict:
    """Simulate scheduling pods onto nodes using given score function"""
    pending = 0

    for pod in pods:
        scores = [(score_fn(node, pod), i, node) for i, node in enumerate(nodes)]
        scores.sort(key=lambda x: -x[0])

        placed = False
        for score, _, node in scores:
            if score > 0 and node.can_fit(pod.cpu, pod.ram):
                node.place(pod.cpu, pod.ram)
                placed = True
                break

        if not placed:
            pending += 1

    return calculate_metrics(nodes, pending)


def calculate_metrics(nodes: List[Node], pending: int) -> Dict:
    """Calculate cluster metrics"""
    cpu_pcts = [n.cpu_pct for n in nodes]
    ram_pcts = [n.ram_pct for n in nodes]

    cpu_mean = sum(cpu_pcts) / len(cpu_pcts)
    ram_mean = sum(ram_pcts) / len(ram_pcts)

    cpu_var = sum((x - cpu_mean)**2 for x in cpu_pcts) / len(cpu_pcts)
    ram_var = sum((x - ram_mean)**2 for x in ram_pcts) / len(ram_pcts)

    # Stranded: one dimension >70% while other <30%
    stranded = sum(1 for n in nodes
                   if (n.cpu_pct > 70 and n.ram_pct < 30) or
                      (n.ram_pct > 70 and n.cpu_pct < 30))

    # Effective utilization: active nodes (>10% on any resource)
    active_nodes = sum(1 for n in nodes if n.cpu_pct > 10 or n.ram_pct > 10)

    # Balance score
    max_var = 2500
    balance = max(0, 100 - (cpu_var + ram_var) / max_var * 100)

    # Wasted resources (stranded)
    wasted_cpu = sum(n.cpu_free for n in nodes if n.cpu_pct > 70 and n.ram_pct < 30)
    wasted_ram = sum(n.ram_free for n in nodes if n.ram_pct > 70 and n.cpu_pct < 30)

    # Monthly $ waste
    monthly_waste = wasted_cpu * COST_PER_CPU_MONTH + wasted_ram * COST_PER_GB_RAM_MONTH

    # Total utilization
    total_cpu = sum(n.cpu_used for n in nodes)
    total_cap_cpu = sum(n.cpu_total for n in nodes)
    total_ram = sum(n.ram_used for n in nodes)
    total_cap_ram = sum(n.ram_total for n in nodes)

    return {
        "cpu_variance": round(cpu_var, 1),
        "ram_variance": round(ram_var, 1),
        "stranded_nodes": stranded,
        "balance_score": round(balance, 1),
        "active_nodes": active_nodes,
        "pending_pods": pending,
        "total_cpu_pct": round(total_cpu / total_cap_cpu * 100, 1) if total_cap_cpu > 0 else 0,
        "total_ram_pct": round(total_ram / total_cap_ram * 100, 1) if total_cap_ram > 0 else 0,
        "wasted_cpu_cores": round(wasted_cpu, 1),
        "wasted_ram_gb": round(wasted_ram, 1),
        "monthly_waste_usd": round(monthly_waste, 0),
    }


# ─── Pod Generators ───

def gen_mixed_pods(n):
    """Generate mixed workload: 40% CPU-heavy, 30% RAM-heavy, 30% balanced"""
    pods = []
    for i in range(n):
        r = random.random()
        if r < 0.4:  # CPU-heavy
            pods.append(Pod(f"cpu-{i}", cpu=random.uniform(0.5, 2.0), ram=random.uniform(0.1, 0.5)))
        elif r < 0.7:  # RAM-heavy
            pods.append(Pod(f"ram-{i}", cpu=random.uniform(0.1, 0.3), ram=random.uniform(1.0, 4.0)))
        else:  # Balanced
            v = random.uniform(0.3, 1.0)
            pods.append(Pod(f"bal-{i}", cpu=v, ram=v * random.uniform(0.8, 1.2)))
    return pods

def gen_cpu_heavy_pods(n):
    """90% CPU-heavy pods"""
    pods = []
    for i in range(n):
        if random.random() < 0.9:
            pods.append(Pod(f"cpu-{i}", cpu=random.uniform(0.5, 2.5), ram=random.uniform(0.1, 0.3)))
        else:
            pods.append(Pod(f"bal-{i}", cpu=random.uniform(0.3, 0.5), ram=random.uniform(0.5, 1.0)))
    return pods

def gen_ram_heavy_pods(n):
    """90% RAM-heavy pods"""
    pods = []
    for i in range(n):
        if random.random() < 0.9:
            pods.append(Pod(f"ram-{i}", cpu=random.uniform(0.1, 0.3), ram=random.uniform(1.0, 6.0)))
        else:
            pods.append(Pod(f"bal-{i}", cpu=random.uniform(0.3, 0.5), ram=random.uniform(0.5, 1.0)))
    return pods

def gen_nodes(n, cpu=4, ram=16):
    """Generate n identical nodes (m5.xlarge equivalent)"""
    return [Node(f"node-{i:02d}", cpu_total=cpu, ram_total=ram) for i in range(n)]


# ─── Run Scenarios ───

STRATEGIES = [
    ("Default (LeastAllocated)", default_least_allocated_score),
    ("Lambda-G Simple (2D)", lambda_g_simple_score),
    ("Lambda-G Full (4D)", lambda_g_full_score),
]

SCENARIOS = [
    {"name": "Mixed Workload (20n × 200p)", "nodes": 20, "pods": 200, "gen": gen_mixed_pods},
    {"name": "Scale Test (50n × 500p)", "nodes": 50, "pods": 500, "gen": gen_mixed_pods},
    {"name": "CPU-Heavy Skew (10n × 100p)", "nodes": 10, "pods": 100, "gen": gen_cpu_heavy_pods},
    {"name": "RAM-Heavy Skew (10n × 100p)", "nodes": 10, "pods": 100, "gen": gen_ram_heavy_pods},
    {"name": "Dense Packing (10n × 150p)", "nodes": 10, "pods": 150, "gen": gen_mixed_pods},
]


def run_scenario(scenario, seed=42):
    """Run a single scenario across all strategies"""
    results = {}

    for strat_name, score_fn in STRATEGIES:
        random.seed(seed)
        nodes = gen_nodes(scenario["nodes"])
        pods = scenario["gen"](scenario["pods"])
        random.shuffle(pods)  # Randomize arrival order

        metrics = simulate_scheduling(nodes, pods, score_fn)
        results[strat_name] = metrics

    return results


def main():
    random.seed(42)

    print(f"""
◊═══════════════════════════════════════════════════════════════════════◊
  LAMBDA-G SIMULATION BENCHMARK
  φ = {PHI}
  Simulating realistic cluster scheduling at scale
◊═══════════════════════════════════════════════════════════════════════◊
""")

    all_results = {}

    for scenario in SCENARIOS:
        print(f"\n  {'═' * 70}")
        print(f"  SCENARIO: {scenario['name']}")
        print(f"  {'═' * 70}\n")

        results = run_scenario(scenario)
        all_results[scenario['name']] = results

        # Print comparison table
        print(f"  {'Metric':<22}", end="")
        for name, _ in STRATEGIES:
            print(f" {name[:18]:>18}", end="")
        print()
        print(f"  {'─' * 76}")

        metrics_to_show = [
            ('balance_score', 'Balance Score', True),      # Higher = better
            ('cpu_variance', 'CPU Variance', False),       # Lower = better
            ('ram_variance', 'RAM Variance', False),       # Lower = better
            ('stranded_nodes', 'Stranded Nodes', False),   # Lower = better
            ('pending_pods', 'Pending Pods', False),       # Lower = better
            ('active_nodes', 'Active Nodes', False),       # Lower = better (efficiency)
            ('monthly_waste_usd', 'Monthly Waste $', False),  # Lower = better
        ]

        for key, label, higher_better in metrics_to_show:
            values = [results[name][key] for name, _ in STRATEGIES]
            best = max(values) if higher_better else min(values)

            print(f"  {label:<22}", end="")
            for v in values:
                marker = " ★" if v == best and values.count(best) == 1 else "  "
                print(f" {v:>16}{marker}", end="")
            print()

        # Winner
        default_balance = results[STRATEGIES[0][0]]['balance_score']
        simple_balance = results[STRATEGIES[1][0]]['balance_score']
        full_balance = results[STRATEGIES[2][0]]['balance_score']

        simple_delta = simple_balance - default_balance
        full_delta = full_balance - default_balance

        default_waste = results[STRATEGIES[0][0]]['monthly_waste_usd']
        simple_waste = results[STRATEGIES[1][0]]['monthly_waste_usd']
        full_waste = results[STRATEGIES[2][0]]['monthly_waste_usd']

        print(f"\n  Simple vs Default: {simple_delta:+.1f} balance, ${default_waste - simple_waste:+.0f}/mo saved")
        print(f"  Full vs Default:   {full_delta:+.1f} balance, ${default_waste - full_waste:+.0f}/mo saved")

    # ─── GRAND SUMMARY ───
    print(f"""

◊═══════════════════════════════════════════════════════════════════════◊
  GRAND SUMMARY — All Scenarios
◊═══════════════════════════════════════════════════════════════════════◊
""")

    print(f"  {'Scenario':<35} {'Default':>10} {'Simple':>10} {'Full':>10} {'Winner':>10}")
    print(f"  {'─' * 78}")

    simple_wins = 0
    full_wins = 0
    default_wins = 0
    total_default_waste = 0
    total_simple_waste = 0
    total_full_waste = 0

    for scenario_name, results in all_results.items():
        d = results[STRATEGIES[0][0]]['balance_score']
        s = results[STRATEGIES[1][0]]['balance_score']
        f = results[STRATEGIES[2][0]]['balance_score']

        total_default_waste += results[STRATEGIES[0][0]]['monthly_waste_usd']
        total_simple_waste += results[STRATEGIES[1][0]]['monthly_waste_usd']
        total_full_waste += results[STRATEGIES[2][0]]['monthly_waste_usd']

        winner = "Full" if f >= s and f >= d else ("Simple" if s >= d else "Default")
        if winner == "Full": full_wins += 1
        elif winner == "Simple": simple_wins += 1
        else: default_wins += 1

        print(f"  {scenario_name:<35} {d:>10.1f} {s:>10.1f} {f:>10.1f} {winner:>10}")

    print(f"\n  {'─' * 78}")
    print(f"  {'Wins':<35} {default_wins:>10} {simple_wins:>10} {full_wins:>10}")
    print(f"  {'Total $ Waste':<35} ${total_default_waste:>9.0f} ${total_simple_waste:>9.0f} ${total_full_waste:>9.0f}")
    print(f"  {'Savings vs Default':<35} {'—':>10} ${total_default_waste - total_simple_waste:>8.0f} ${total_default_waste - total_full_waste:>8.0f}")

    print(f"""
◊═══════════════════════════════════════════════════════════════════════◊
  φ = {PHI}
  "Symmetric exhaustion > least allocation"
◊═══════════════════════════════════════════════════════════════════════◊
""")


if __name__ == "__main__":
    main()
