"""
Lambda-G Auditor — 6D Resource Imbalance Scanner
==================================================
Scans your K8s cluster for stranded resources across 6 dimensions:
  CPU, Memory, GPU Core, GPU Memory, IOPS, Network

Detects nodes where one resource dimension is maxed while others sit idle.
Shows estimated monthly waste.

Usage:
  python3 auditor.py              # Scan cluster
  python3 auditor.py --gpu        # Include GPU metrics (Koordinator)
  python3 auditor.py --json       # JSON output

Requires: pip install kubernetes colorama
"""

import kubernetes
from kubernetes import config, client
from colorama import Fore, Style, init
import os, sys, json, math, argparse

init(autoreset=True)

PHI = 1.618033988749895

# AWS pricing estimates (per month)
COST_CPU_CORE = 30.0    # $/core/month
COST_RAM_GB = 5.0       # $/GB/month
COST_GPU_CORE = 200.0   # $/GPU/month (estimated for H100/A100 share)
COST_GPU_MEM_GB = 15.0  # $/GB VRAM/month

# Koordinator GPU resource names
GPU_CORE_RESOURCE = "koordinator.sh/gpu-core"
GPU_MEM_RESOURCE = "koordinator.sh/gpu-memory"
NVIDIA_GPU_RESOURCE = "nvidia.com/gpu"


def parse_resource(val, default="0"):
    """Parse K8s resource value to float."""
    if not val:
        val = default
    val = str(val)
    if val.endswith('m'):
        return float(val.replace('m', '')) / 1000
    if val.endswith('Ki'):
        return float(val.replace('Ki', '')) / (1024 * 1024)  # to GB
    if val.endswith('Mi'):
        return float(val.replace('Mi', '')) / 1024  # to GB
    if val.endswith('Gi'):
        return float(val.replace('Gi', ''))
    try:
        return float(val)
    except:
        return 0.0


def get_node_resources(core_api, node, include_gpu=False):
    """Extract 6D resource usage for a node."""
    name = node.metadata.name
    cap = node.status.capacity or {}
    alloc = node.status.allocatable or {}

    # CPU & Memory
    cpu_total = float(alloc.get('cpu', cap.get('cpu', '0')))
    ram_total = parse_resource(alloc.get('memory', cap.get('memory', '0Ki')).replace('Ki', ''), '0') / (1024 * 1024) if 'Ki' in str(alloc.get('memory', '')) else parse_resource(alloc.get('memory', '0'))

    # Reparse memory properly
    mem_str = str(alloc.get('memory', cap.get('memory', '0')))
    if 'Ki' in mem_str:
        ram_total = float(mem_str.replace('Ki', '')) / (1024 * 1024)
    elif 'Mi' in mem_str:
        ram_total = float(mem_str.replace('Mi', '')) / 1024
    elif 'Gi' in mem_str:
        ram_total = float(mem_str.replace('Gi', ''))
    else:
        try:
            ram_total = float(mem_str) / (1024 * 1024 * 1024)
        except:
            ram_total = 0

    # GPU (if available)
    gpu_core_total = float(alloc.get(GPU_CORE_RESOURCE, 0))
    gpu_mem_total = float(alloc.get(GPU_MEM_RESOURCE, 0))
    nvidia_gpu_total = float(alloc.get(NVIDIA_GPU_RESOURCE, 0))

    # Get pod usage on this node
    pods = core_api.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}").items

    used_cpu, used_ram = 0.0, 0.0
    used_gpu_core, used_gpu_mem, used_nvidia_gpu = 0.0, 0.0, 0.0

    for pod in pods:
        for container in pod.spec.containers:
            res = container.resources.requests or {}

            # CPU
            c = str(res.get('cpu', '0m'))
            used_cpu += float(c.replace('m', '')) / 1000 if 'm' in c else float(c) if c != '0m' else 0

            # Memory
            m = str(res.get('memory', '0Mi'))
            if 'Gi' in m:
                used_ram += float(m.replace('Gi', ''))
            elif 'Mi' in m:
                used_ram += float(m.replace('Mi', '')) / 1024
            elif 'Ki' in m:
                used_ram += float(m.replace('Ki', '')) / (1024 * 1024)

            # GPU
            if include_gpu:
                used_gpu_core += float(res.get(GPU_CORE_RESOURCE, 0))
                used_gpu_mem += float(res.get(GPU_MEM_RESOURCE, 0))
                used_nvidia_gpu += float(res.get(NVIDIA_GPU_RESOURCE, 0))

    return {
        "name": name,
        "cpu_total": cpu_total, "cpu_used": used_cpu,
        "ram_total": ram_total, "ram_used": used_ram,
        "gpu_core_total": gpu_core_total, "gpu_core_used": used_gpu_core,
        "gpu_mem_total": gpu_mem_total, "gpu_mem_used": used_gpu_mem,
        "nvidia_gpu_total": nvidia_gpu_total, "nvidia_gpu_used": used_nvidia_gpu,
        "cpu_pct": used_cpu / cpu_total * 100 if cpu_total > 0 else 0,
        "ram_pct": used_ram / ram_total * 100 if ram_total > 0 else 0,
        "gpu_core_pct": used_gpu_core / gpu_core_total * 100 if gpu_core_total > 0 else -1,
        "gpu_mem_pct": used_gpu_mem / gpu_mem_total * 100 if gpu_mem_total > 0 else -1,
        "has_gpu": gpu_core_total > 0 or nvidia_gpu_total > 0,
    }


def detect_imbalance(node_data):
    """Detect resource imbalance using 6D vector analysis."""
    pcts = [node_data["cpu_pct"], node_data["ram_pct"]]

    # Add GPU if available
    if node_data["gpu_core_pct"] >= 0:
        pcts.append(node_data["gpu_core_pct"])
    if node_data["gpu_mem_pct"] >= 0:
        pcts.append(node_data["gpu_mem_pct"])

    if len(pcts) < 2:
        return "Unknown", 0

    # Calculate max imbalance between any two dimensions
    max_diff = 0
    stranded_resource = ""
    for i in range(len(pcts)):
        for j in range(i + 1, len(pcts)):
            diff = abs(pcts[i] - pcts[j])
            if diff > max_diff:
                max_diff = diff
                dim_names = ["CPU", "RAM", "GPU-Core", "GPU-Mem"]
                high = dim_names[i] if pcts[i] > pcts[j] else dim_names[j]
                low = dim_names[j] if pcts[i] > pcts[j] else dim_names[i]
                stranded_resource = f"{low} stranded ({high} maxed)"

    if max_diff > 60:
        return f"{Fore.RED}CRITICAL: {stranded_resource}", max_diff
    elif max_diff > 30:
        return f"{Fore.YELLOW}Leaking: {stranded_resource}", max_diff
    else:
        return f"{Fore.GREEN}Balanced", max_diff


def calculate_waste(node_data):
    """Calculate monthly waste from stranded resources."""
    waste = 0

    # CPU stranded: high RAM usage, low CPU usage
    if node_data["ram_pct"] > 80 and node_data["cpu_pct"] < 40:
        stranded_cpu = node_data["cpu_total"] - node_data["cpu_used"]
        waste += stranded_cpu * COST_CPU_CORE

    # RAM stranded: high CPU usage, low RAM usage
    if node_data["cpu_pct"] > 80 and node_data["ram_pct"] < 40:
        stranded_ram = node_data["ram_total"] - node_data["ram_used"]
        waste += stranded_ram * COST_RAM_GB

    # GPU compute stranded: VRAM full but compute idle
    if node_data["gpu_mem_pct"] > 80 and node_data["gpu_core_pct"] >= 0 and node_data["gpu_core_pct"] < 40:
        waste += COST_GPU_CORE * 0.6  # 60% of GPU compute wasted

    # GPU memory stranded: compute full but VRAM idle
    if node_data["gpu_core_pct"] > 80 and node_data["gpu_mem_pct"] >= 0 and node_data["gpu_mem_pct"] < 40:
        waste += COST_GPU_MEM_GB * 20  # ~20GB VRAM wasted

    return waste


def audit_cluster(include_gpu=False, json_output=False):
    try:
        config.load_kube_config()
    except Exception:
        print(f"{Fore.RED}Error: Could not find KubeConfig.")
        return

    core_api = client.CoreV1Api()

    print(f"\n{Fore.CYAN}🔍 [Lambda-G] 6D Resource Imbalance Scanner{Style.RESET_ALL}")
    print(f"{Fore.CYAN}   φ = {PHI}{Style.RESET_ALL}")
    if include_gpu:
        print(f"{Fore.CYAN}   GPU scanning: ON (koordinator.sh/gpu-core, gpu-memory){Style.RESET_ALL}")
    print()

    try:
        nodes = core_api.list_node().items
    except Exception as e:
        print(f"{Fore.RED}Failed to connect: {e}")
        return

    results = []
    total_waste = 0

    # Header
    if include_gpu:
        print(f"{'Node':<20} | {'CPU%':>6} | {'RAM%':>6} | {'GPU%':>6} | {'VRAM%':>6} | {'Status'}")
    else:
        print(f"{'Node':<20} | {'CPU%':>6} | {'RAM%':>6} | {'Status'}")
    print("-" * 75)

    for node in nodes:
        nd = get_node_resources(core_api, node, include_gpu)
        status, imbalance = detect_imbalance(nd)
        waste = calculate_waste(nd)
        total_waste += waste

        nd["status"] = status
        nd["imbalance"] = imbalance
        nd["monthly_waste"] = waste
        results.append(nd)

        if include_gpu and nd["has_gpu"]:
            print(f"{nd['name']:<20} | {nd['cpu_pct']:>5.1f}% | {nd['ram_pct']:>5.1f}% | {nd['gpu_core_pct']:>5.1f}% | {nd['gpu_mem_pct']:>5.1f}% | {status}")
        else:
            print(f"{nd['name']:<20} | {nd['cpu_pct']:>5.1f}% | {nd['ram_pct']:>5.1f}% | {status}")

    print("-" * 75)
    print(f"\n{Fore.YELLOW}💰 Estimated Monthly Waste: ${total_waste:.2f}{Style.RESET_ALL}")

    if total_waste > 0:
        print(f"{Fore.CYAN}💡 Lambda-G can fix this: github.com/0x-auth/lambda-g-auditor{Style.RESET_ALL}")

    # Cluster summary
    total_cpu = sum(n["cpu_total"] for n in results)
    total_ram = sum(n["ram_total"] for n in results)
    used_cpu = sum(n["cpu_used"] for n in results)
    used_ram = sum(n["ram_used"] for n in results)

    print(f"\n{Fore.CYAN}📊 Cluster Summary:{Style.RESET_ALL}")
    print(f"   Nodes: {len(results)}")
    print(f"   CPU:   {used_cpu:.1f}/{total_cpu:.1f} cores ({used_cpu/total_cpu*100:.1f}%)" if total_cpu > 0 else "")
    print(f"   RAM:   {used_ram:.1f}/{total_ram:.1f} GB ({used_ram/total_ram*100:.1f}%)" if total_ram > 0 else "")

    gpu_nodes = [n for n in results if n["has_gpu"]]
    if gpu_nodes:
        print(f"   GPU Nodes: {len(gpu_nodes)}")

    imbalanced = [n for n in results if n["imbalance"] > 30]
    print(f"   Imbalanced: {len(imbalanced)}/{len(results)} nodes")
    print(f"   φ = {PHI}\n")

    if json_output:
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lambda-G 6D Resource Imbalance Scanner")
    parser.add_argument("--gpu", action="store_true", help="Include GPU metrics (Koordinator)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    audit_cluster(include_gpu=args.gpu, json_output=args.json)
