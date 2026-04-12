package pkg

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

const PHI = 1.618033988749895

// Cost estimates per month
const (
	CostCPUCore  = 30.0
	CostRAMGB    = 5.0
	CostGPUCore  = 200.0
	CostGPUMemGB = 15.0
)

type NodeData struct {
	Name        string
	CPUTotal    float64
	CPUUsed     float64
	RAMTotal    float64
	RAMUsed     float64
	GPUTotal    float64
	GPUUsed     float64
	HasGPU      bool
	CPUPct      float64
	RAMPct      float64
	GPUPct      float64
	ImbalanceScore float64
	Status      string
	WasteUSD    float64
}

type ClusterSummary struct {
	Nodes      []NodeData
	TotalWaste float64
	AvgImbalance float64
}

func parseMemory(val string) float64 {
	if val == "" {
		return 0
	}
	if strings.HasSuffix(val, "Ki") {
		v, _ := strconv.ParseFloat(strings.TrimSuffix(val, "Ki"), 64)
		return v / (1024 * 1024)
	}
	if strings.HasSuffix(val, "Mi") {
		v, _ := strconv.ParseFloat(strings.TrimSuffix(val, "Mi"), 64)
		return v / 1024
	}
	if strings.HasSuffix(val, "Gi") {
		v, _ := strconv.ParseFloat(strings.TrimSuffix(val, "Gi"), 64)
		return v
	}
	v, _ := strconv.ParseFloat(val, 64)
	return v / (1024 * 1024 * 1024)
}

func parseCPU(val string) float64 {
	if val == "" {
		return 0
	}
	if strings.HasSuffix(val, "m") {
		v, _ := strconv.ParseFloat(strings.TrimSuffix(val, "m"), 64)
		return v / 1000
	}
	v, _ := strconv.ParseFloat(val, 64)
	return v
}

func detectImbalance(n *NodeData) (string, float64) {
	pcts := []float64{n.CPUPct, n.RAMPct}
	names := []string{"CPU", "RAM"}

	if n.HasGPU {
		pcts = append(pcts, n.GPUPct)
		names = append(names, "GPU")
	}

	maxDiff := 0.0
	status := "Balanced"

	for i := 0; i < len(pcts); i++ {
		for j := i + 1; j < len(pcts); j++ {
			diff := math.Abs(pcts[i] - pcts[j])
			if diff > maxDiff {
				maxDiff = diff
				high, low := names[i], names[j]
				if pcts[j] > pcts[i] {
					high, low = names[j], names[i]
				}
				if diff > 60 {
					status = fmt.Sprintf("CRITICAL: %s stranded (%s maxed)", low, high)
				} else if diff > 30 {
					status = fmt.Sprintf("Leaking: %s stranded (%s maxed)", low, high)
				} else {
					status = "Balanced"
				}
			}
		}
	}

	// φ-weighted imbalance score (0-10)
	score := (maxDiff / 100.0) * 10.0 / PHI
	return status, score
}

func calculateWaste(n *NodeData) float64 {
	waste := 0.0
	if n.RAMPct > 70 && n.CPUPct < 30 {
		strandedCPU := n.CPUTotal * (1 - n.CPUPct/100)
		waste += strandedCPU * CostCPUCore
	}
	if n.CPUPct > 70 && n.RAMPct < 30 {
		strandedRAM := n.RAMTotal * (1 - n.RAMPct/100)
		waste += strandedRAM * CostRAMGB
	}
	if n.HasGPU && n.GPUPct < 20 {
		waste += n.GPUTotal * (1 - n.GPUPct/100) * CostGPUCore
	}
	return waste
}

func ScanCluster(clientset *kubernetes.Clientset, includeGPU bool) (*ClusterSummary, error) {
	nodes, err := clientset.CoreV1().Nodes().List(context.TODO(), metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("failed to list nodes: %w", err)
	}

	pods, err := clientset.CoreV1().Pods("").List(context.TODO(), metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("failed to list pods: %w", err)
	}

	// Group pods by node
	podsByNode := make(map[string][]corev1.Pod)
	for _, pod := range pods.Items {
		if pod.Spec.NodeName != "" && pod.Status.Phase == corev1.PodRunning {
			podsByNode[pod.Spec.NodeName] = append(podsByNode[pod.Spec.NodeName], pod)
		}
	}

	summary := &ClusterSummary{}

	for _, node := range nodes.Items {
		alloc := node.Status.Allocatable

		nd := NodeData{Name: node.Name}

		// CPU total
		if cpuQ, ok := alloc[corev1.ResourceCPU]; ok {
			nd.CPUTotal = parseCPU(cpuQ.String())
		}

		// RAM total
		if memQ, ok := alloc[corev1.ResourceMemory]; ok {
			nd.RAMTotal = parseMemory(memQ.String())
		}

		// GPU total
		for resName, qty := range alloc {
			if strings.Contains(string(resName), "nvidia.com/gpu") ||
				strings.Contains(string(resName), "gpu-core") {
				v, _ := strconv.ParseFloat(qty.String(), 64)
				nd.GPUTotal += v
				nd.HasGPU = true
			}
		}

		// Sum pod requests
		for _, pod := range podsByNode[node.Name] {
			for _, c := range pod.Spec.Containers {
				if req := c.Resources.Requests; req != nil {
					if cpu, ok := req[corev1.ResourceCPU]; ok {
						nd.CPUUsed += parseCPU(cpu.String())
					}
					if mem, ok := req[corev1.ResourceMemory]; ok {
						nd.RAMUsed += parseMemory(mem.String())
					}
					if includeGPU {
						for resName, qty := range req {
							if strings.Contains(string(resName), "nvidia.com/gpu") ||
								strings.Contains(string(resName), "gpu-core") {
								v, _ := strconv.ParseFloat(qty.String(), 64)
								nd.GPUUsed += v
							}
						}
					}
				}
			}
		}

		if nd.CPUTotal > 0 {
			nd.CPUPct = nd.CPUUsed / nd.CPUTotal * 100
		}
		if nd.RAMTotal > 0 {
			nd.RAMPct = nd.RAMUsed / nd.RAMTotal * 100
		}
		if nd.GPUTotal > 0 {
			nd.GPUPct = nd.GPUUsed / nd.GPUTotal * 100
		}

		nd.Status, nd.ImbalanceScore = detectImbalance(&nd)
		nd.WasteUSD = calculateWaste(&nd)

		summary.Nodes = append(summary.Nodes, nd)
		summary.TotalWaste += nd.WasteUSD
		summary.AvgImbalance += nd.ImbalanceScore
	}

	if len(summary.Nodes) > 0 {
		summary.AvgImbalance /= float64(len(summary.Nodes))
	}

	return summary, nil
}
