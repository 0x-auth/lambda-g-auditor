import kubernetes
from kubernetes import config, client
from colorama import Fore, Style, init
import os

init(autoreset=True)

def audit_cluster():
    try:
        # Explicitly loading config to avoid LocationValueError
        config.load_kube_config()
    except Exception:
        print(f"{Fore.RED}Error: Could not find KubeConfig. Try running 'minikube update-context' first.")
        return

    core_api = client.CoreV1Api()
    
    print(f"{Fore.CYAN}🔍 [Lambda-G] Scanning for Entropic Leaks...{Style.RESET_ALL}")
    
    try:
        nodes = core_api.list_node().items
    except Exception as e:
        print(f"{Fore.RED}Failed to connect to cluster: {e}")
        return

    total_wasted_ram = 0
    total_wasted_cpu = 0
    
    print(f"\n{'Node Name':<20} | {'CPU %':<8} | {'RAM %':<8} | {'Status'}")
    print("-" * 70)

    for node in nodes:
        name = node.metadata.name
        cap = node.status.capacity
        
        n_cpu = float(cap['cpu'])
        n_ram = float(cap['memory'].replace('Ki', '')) / (1024**2) 
        
        pods = core_api.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}").items
        
        used_cpu, used_ram = 0.0, 0.0
        for pod in pods:
            for container in pod.spec.containers:
                res = container.resources.requests or {}
                c = res.get('cpu', '100m')
                used_cpu += float(c.replace('m', '')) / 1000 if 'm' in c else float(c)
                
                m = res.get('memory', '128Mi')
                if 'Gi' in m: used_ram += float(m.replace('Gi', ''))
                elif 'Mi' in m: used_ram += float(m.replace('Mi', '')) / 1024

        cpu_p, ram_p = used_cpu / n_cpu, used_ram / n_ram
        
        status = f"{Fore.GREEN}Balanced"
        # The Lambda-G Logic: If distance between resources > 30%, it's leaking money
        if abs(cpu_p - ram_p) > 0.3:
            status = f"{Fore.YELLOW}Leaking (Mismatch)"
        
        if cpu_p > 0.9 and ram_p < 0.6:
            waste = n_ram - used_ram
            total_wasted_ram += waste
            status = f"{Fore.RED}Stranded RAM ({waste:.1f}GB)"

        print(f"{name:<20} | {cpu_p:>7.1%} | {ram_p:>7.1%} | {status}")

    loss = (total_wasted_cpu * 30) + (total_wasted_ram * 5)
    print("-" * 70)
    print(f"\n{Fore.YELLOW}💰 Potential Monthly Recovery: ${loss:.2f}{Style.RESET_ALL}")

if __name__ == "__main__":
    audit_cluster()
