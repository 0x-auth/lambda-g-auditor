package pkg

import (
	"encoding/json"
	"fmt"
	"strings"
)

const (
	colorRed    = "\033[31m"
	colorYellow = "\033[33m"
	colorGreen  = "\033[32m"
	colorCyan   = "\033[36m"
	colorBold   = "\033[1m"
	colorReset  = "\033[0m"
)

func statusColor(status string) string {
	if strings.HasPrefix(status, "CRITICAL") {
		return colorRed + status + colorReset
	}
	if strings.HasPrefix(status, "Leaking") {
		return colorYellow + status + colorReset
	}
	return colorGreen + status + colorReset
}

func PrintSummary(s *ClusterSummary, jsonOut bool) {
	if jsonOut {
		b, _ := json.MarshalIndent(s, "", "  ")
		fmt.Println(string(b))
		return
	}

	fmt.Printf("\n%s◊ LAMBDA-G · Cluster Resource Balance Scanner%s\n", colorBold+colorCyan, colorReset)
	fmt.Printf("  φ = %.15f\n", PHI)
	fmt.Printf("  Nodes scanned: %d\n\n", len(s.Nodes))

	// Header
	fmt.Printf("%-28s %7s %7s %7s %8s %s\n",
		"Node", "CPU%", "RAM%", "GPU%", "Score", "Status")
	fmt.Println(strings.Repeat("─", 90))

	for _, n := range s.Nodes {
		gpuStr := "  n/a"
		if n.HasGPU {
			gpuStr = fmt.Sprintf("%6.1f%%", n.GPUPct)
		}

		scoreStr := fmt.Sprintf("%.1f", n.ImbalanceScore)
		if n.ImbalanceScore > 5 {
			scoreStr = colorRed + scoreStr + colorReset
		} else if n.ImbalanceScore > 2 {
			scoreStr = colorYellow + scoreStr + colorReset
		} else {
			scoreStr = colorGreen + scoreStr + colorReset
		}

		fmt.Printf("%-28s %6.1f%% %6.1f%% %s %7s  %s\n",
			n.Name,
			n.CPUPct,
			n.RAMPct,
			gpuStr,
			scoreStr,
			statusColor(n.Status),
		)
	}

	fmt.Println(strings.Repeat("─", 90))
	fmt.Printf("\n  %sCluster Imbalance Score: %.1f/10%s\n", colorBold, s.AvgImbalance*PHI, colorReset)

	if s.TotalWaste > 0 {
		fmt.Printf("  %s💰 Estimated Monthly Waste: $%.0f%s\n", colorRed, s.TotalWaste, colorReset)
	} else {
		fmt.Printf("  %s✓ No obvious resource waste detected%s\n", colorGreen, colorReset)
	}

	// Hotspot warnings
	fmt.Println()
	for _, n := range s.Nodes {
		if strings.HasPrefix(n.Status, "CRITICAL") || strings.HasPrefix(n.Status, "Leaking") {
			fmt.Printf("  %s⚠ %s%s: %s\n", colorYellow, n.Name, colorReset, n.Status)
		}
	}

	fmt.Printf("\n  %sRun with --detailed for pod-level breakdown%s\n", colorCyan, colorReset)
	fmt.Printf("  %sFix imbalances automatically: helm install lambda-g-controller%s\n\n",
		colorCyan, colorReset)
}

func PrintDetailed(s *ClusterSummary) {
	PrintSummary(s, false)

	fmt.Printf("\n%s━━━ DETAILED NODE BREAKDOWN ━━━%s\n\n", colorBold, colorReset)
	for _, n := range s.Nodes {
		fmt.Printf("  %s%s%s\n", colorBold, n.Name, colorReset)
		fmt.Printf("    CPU:  %.2f / %.2f cores (%.1f%%)\n", n.CPUUsed, n.CPUTotal, n.CPUPct)
		fmt.Printf("    RAM:  %.2f / %.2f GB   (%.1f%%)\n", n.RAMUsed, n.RAMTotal, n.RAMPct)
		if n.HasGPU {
			fmt.Printf("    GPU:  %.0f / %.0f units   (%.1f%%)\n", n.GPUUsed, n.GPUTotal, n.GPUPct)
		}
		if n.WasteUSD > 0 {
			fmt.Printf("    %sWaste: ~$%.0f/month%s\n", colorRed, n.WasteUSD, colorReset)
		}
		fmt.Printf("    Score: %.2f/10\n\n", n.ImbalanceScore)
	}
}
