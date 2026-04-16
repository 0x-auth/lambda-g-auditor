package main

import (
	"fmt"
	"os"
	"time"

	"github.com/0x-auth/lambda-g-auditor/pkg"
	"k8s.io/client-go/kubernetes"
	_ "k8s.io/client-go/plugin/pkg/client/auth"
	"k8s.io/client-go/tools/clientcmd"
)

const version = "v1.0.0"

func usage() {
	fmt.Printf(`
◊ kubectl lambda-g %s — 6D Cluster Resource Imbalance Scanner

USAGE:
  kubectl lambda-g scan              Scan cluster for resource imbalance
  kubectl lambda-g scan --detailed   Full per-node breakdown
  kubectl lambda-g scan --gpu        Include GPU metrics
  kubectl lambda-g scan --json       JSON output
  kubectl lambda-g watch             Continuous monitoring (60s interval)
  kubectl lambda-g watch --interval 30
  kubectl lambda-g version

WHAT IT FINDS:
  Nodes where one resource is maxed while others sit idle.
  Example: CPU at 90%% but RAM at 15%% — RAM is stranded, money wasted.

  Most clusters waste 10-20%% of compute budget this way.
  GPU clusters waste even more (VRAM full, compute idle).

FIX IMBALANCES AUTOMATICALLY:
  helm install lambda-g-controller oci://registry-1.docker.io/bitsabhi/lambda-g-controller

`, version)
}

func getClient() (*kubernetes.Clientset, error) {
	loadingRules := clientcmd.NewDefaultClientConfigLoadingRules()
	configOverrides := &clientcmd.ConfigOverrides{}
	kubeConfig := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(loadingRules, configOverrides)
	config, err := kubeConfig.ClientConfig()
	if err != nil {
		return nil, fmt.Errorf("failed to load kubeconfig: %w", err)
	}
	return kubernetes.NewForConfig(config)
}

func cmdScan(args []string) {
	detailed := false
	gpu := false
	jsonOut := false

	for _, a := range args {
		switch a {
		case "--detailed":
			detailed = true
		case "--gpu":
			gpu = true
		case "--json":
			jsonOut = true
		}
	}

	clientset, err := getClient()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("  Scanning cluster...")
	summary, err := pkg.ScanCluster(clientset, gpu)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	if detailed {
		pkg.PrintDetailed(summary)
	} else {
		pkg.PrintSummary(summary, jsonOut)
	}
}

func cmdWatch(args []string) {
	interval := 60
	gpu := false

	for i, a := range args {
		if a == "--interval" && i+1 < len(args) {
			fmt.Sscanf(args[i+1], "%d", &interval)
		}
		if a == "--gpu" {
			gpu = true
		}
	}

	clientset, err := getClient()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("  Watching cluster every %ds. Ctrl+C to stop.\n", interval)
	for {
		fmt.Printf("\033[2J\033[H") // clear screen
		summary, err := pkg.ScanCluster(clientset, gpu)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		} else {
			pkg.PrintSummary(summary, false)
		}
		time.Sleep(time.Duration(interval) * time.Second)
	}
}

func main() {
	args := os.Args[1:]

	if len(args) == 0 {
		usage()
		os.Exit(0)
	}

	switch args[0] {
	case "scan":
		cmdScan(args[1:])
	case "watch":
		cmdWatch(args[1:])
	case "version", "--version", "-v":
		fmt.Printf("kubectl-lambda_g %s\n", version)
	case "help", "--help", "-h":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", args[0])
		usage()
		os.Exit(1)
	}
}
