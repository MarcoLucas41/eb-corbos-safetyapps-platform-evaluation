#!/usr/bin/env python3
"""
Scenario 2 Analysis Script — Inter-VM Communication Latency and Reliability

Parses run_XX.txt files produced by run_scenario2.sh, prints a summary
table, and generates plots for each hypothesis.

Usage:
    python3 experiments/analyze_scenario2.py
    python3 experiments/analyze_scenario2.py -r experiments/results_scenario2
"""

import os
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# ─── Constants ────────────────────────────────────────────────────────────────

# Baseline P99 multiplier for the degradation threshold criterion
DEGRADATION_THRESHOLD_FACTOR = 3.0

CONDITION_ORDER = [
    "C0_baseline",
    "C1_n1",
    "C2_n2",
    "C3_n4",
    "C4_n6",
    "C5_n8",
]

CONDITION_LABELS = {
    "C0_baseline": "N=0",
    "C1_n1":       "N=1",
    "C2_n2":       "N=2",
    "C3_n4":       "N=4",
    "C4_n6":       "N=6",
    "C5_n8":       "N=8",
}

N_VALUES = {
    "C0_baseline": 0,
    "C1_n1":       1,
    "C2_n2":       2,
    "C3_n4":       4,
    "C4_n6":       6,
    "C5_n8":       8,
}

# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_run_file(filepath):
    """Extract statistics from a single shm_client run output file."""
    text = Path(filepath).read_text()
    stats = {}

    m = re.search(r"Messages sent:\s+(\d+)", text)
    if m:
        stats["n_sent"] = int(m.group(1))

    m = re.search(r"Messages lost:\s+(\d+)\s+\(([0-9.]+)%\)", text)
    if m:
        stats["n_lost"] = int(m.group(1))
        stats["loss_pct"] = float(m.group(2))

    # RTL block parsing — rely on the fixed output format of shm_client
    m = re.search(r"Mean:\s+([0-9.]+)\s+Std dev:\s+([0-9.]+)", text)
    if m:
        stats["rtl_mean_us"]   = float(m.group(1))
        stats["rtl_stddev_us"] = float(m.group(2))

    m = re.search(r"Min:\s+([0-9.]+)\s+Max:\s+([0-9.]+)", text)
    if m:
        stats["rtl_min_us"] = float(m.group(1))
        stats["rtl_max_us"] = float(m.group(2))

    m = re.search(r"P50:\s+([0-9.]+)\s+P95:\s+([0-9.]+)", text)
    if m:
        stats["rtl_p50_us"] = float(m.group(1))
        stats["rtl_p95_us"] = float(m.group(2))

    m = re.search(r"P99:\s+([0-9.]+)\s+P99\.9:\s+([0-9.]+)", text)
    if m:
        stats["rtl_p99_us"]  = float(m.group(1))
        stats["rtl_p999_us"] = float(m.group(2))

    m = re.search(r"Throughput:\s+([0-9.]+)\s+msg/s", text)
    if m:
        stats["throughput"] = float(m.group(1))

    return stats if stats else None


def load_results(results_dir):
    results = defaultdict(list)
    for condition in CONDITION_ORDER:
        cond_dir = Path(results_dir) / condition
        if not cond_dir.is_dir():
            continue
        for run_file in sorted(cond_dir.glob("run_*.txt")):
            s = parse_run_file(run_file)
            if s:
                results[condition].append(s)
    return results

# ─── Summary Table ────────────────────────────────────────────────────────────

def print_summary_table(results):
    print("\n" + "=" * 110)
    print("SCENARIO 2 — SUMMARY TABLE")
    print("=" * 110)
    header = (
        f"{'Condition':<14} {'N':>3} {'Runs':>4} "
        f"{'Lost%':>6} "
        f"{'MeanRTL':>9} {'StdDev':>8} "
        f"{'P99':>9} {'P99.9':>9} "
        f"{'MaxRTL':>9} "
        f"{'Thput':>7}"
    )
    print(header)
    print("-" * 110)

    baseline_p99 = None

    for cond in CONDITION_ORDER:
        if cond not in results:
            continue

        runs = results[cond]
        n    = len(runs)
        label = CONDITION_LABELS.get(cond, cond)
        n_val = N_VALUES.get(cond, "?")

        total_sent = sum(r.get("n_sent", 0) for r in runs)
        total_lost = sum(r.get("n_lost", 0) for r in runs)
        loss_pct   = (total_lost / total_sent * 100) if total_sent > 0 else 0.0

        mean_vals   = [r["rtl_mean_us"]   for r in runs if "rtl_mean_us"   in r]
        stddev_vals = [r["rtl_stddev_us"] for r in runs if "rtl_stddev_us" in r]
        p99_vals    = [r["rtl_p99_us"]    for r in runs if "rtl_p99_us"    in r]
        p999_vals   = [r["rtl_p999_us"]   for r in runs if "rtl_p999_us"   in r]
        max_vals    = [r["rtl_max_us"]    for r in runs if "rtl_max_us"    in r]
        tput_vals   = [r["throughput"]    for r in runs if "throughput"     in r]

        avg_mean   = sum(mean_vals)   / len(mean_vals)   if mean_vals   else 0
        avg_stddev = sum(stddev_vals) / len(stddev_vals) if stddev_vals else 0
        avg_p99    = sum(p99_vals)    / len(p99_vals)    if p99_vals    else 0
        avg_p999   = sum(p999_vals)   / len(p999_vals)   if p999_vals   else 0
        worst_max  = max(max_vals)                       if max_vals    else 0
        avg_tput   = sum(tput_vals)   / len(tput_vals)   if tput_vals   else 0

        if cond == "C0_baseline":
            baseline_p99 = avg_p99

        # Flag conditions that exceed the degradation threshold
        flag = ""
        if baseline_p99 and avg_p99 > baseline_p99 * DEGRADATION_THRESHOLD_FACTOR:
            flag = " !"

        print(
            f"{label:<14} {n_val:>3} {n:>4} "
            f"{loss_pct:>5.2f}% "
            f"{avg_mean:>9.1f} {avg_stddev:>8.1f} "
            f"{avg_p99:>9.1f} {avg_p999:>9.1f} "
            f"{worst_max:>9.1f} "
            f"{avg_tput:>7.0f}"
            f"{flag}"
        )

    print("=" * 110)
    print("All RTL values in µs.  Throughput in msg/s.")
    if baseline_p99:
        threshold = baseline_p99 * DEGRADATION_THRESHOLD_FACTOR
        print(f"! = P99 RTL exceeds {DEGRADATION_THRESHOLD_FACTOR}× baseline P99 "
              f"({baseline_p99:.1f} µs → threshold {threshold:.1f} µs)")

# ─── Plotting ─────────────────────────────────────────────────────────────────

def generate_plots(results, output_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed — skipping plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    conditions = [c for c in CONDITION_ORDER if c in results]
    labels     = [CONDITION_LABELS[c] for c in conditions]
    n_vals     = [N_VALUES[c] for c in conditions]

    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#B71C1C"]

    # ── Plot 1: Mean RTL ± std vs N ──────────────────────────────────────
    means  = []; stds = []
    for c in conditions:
        vals = [r["rtl_mean_us"] for r in results[c] if "rtl_mean_us" in r]
        avg = sum(vals) / len(vals) if vals else 0
        std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5 if len(vals) > 1 else 0
        means.append(avg); stds.append(std)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=stds, fmt="o-", capsize=5,
                color="#2196F3", linewidth=2, markersize=7)
    ax.set_xlabel("Background Workers (N)")
    ax.set_ylabel("Mean RTL (µs)")
    ax.set_title("Mean Round-Trip Latency vs Load Level")
    ax.set_xticks(n_vals); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "rtl_mean_vs_n.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/rtl_mean_vs_n.png")

    # ── Plot 2: Percentile lines (P50 / P99 / P99.9) vs N ────────────────
    p50s = []; p99s = []; p999s = []
    for c in conditions:
        def avg_key(key):
            vals = [r[key] for r in results[c] if key in r]
            return sum(vals) / len(vals) if vals else 0
        p50s.append(avg_key("rtl_p50_us"))
        p99s.append(avg_key("rtl_p99_us"))
        p999s.append(avg_key("rtl_p999_us"))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, p50s,  "o-", label="P50",   color="#4CAF50", linewidth=2)
    ax.plot(n_vals, p99s,  "s-", label="P99",   color="#FF9800", linewidth=2)
    ax.plot(n_vals, p999s, "^-", label="P99.9", color="#F44336", linewidth=2)
    ax.set_xlabel("Background Workers (N)")
    ax.set_ylabel("RTL (µs)")
    ax.set_title("RTL Percentiles vs Load Level (H2: tail grows faster than median)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "rtl_percentiles_vs_n.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/rtl_percentiles_vs_n.png")

    # ── Plot 3: Box plots of per-run mean RTL per condition ───────────────
    box_data = [
        [r["rtl_mean_us"] for r in results[c] if "rtl_mean_us" in r]
        for c in conditions
    ]
    fig, ax = plt.subplots(figsize=(9, 5))
    bp = ax.boxplot(box_data, labels=labels, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors[:len(conditions)]):
        patch.set_facecolor(color); patch.set_alpha(0.7)
    ax.set_ylabel("Per-Run Mean RTL (µs)")
    ax.set_title("RTL Distribution per Condition (run-to-run variance)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "rtl_boxplot.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/rtl_boxplot.png")

    # ── Plot 4: Message loss rate per condition ───────────────────────────
    loss_rates = []
    for c in conditions:
        sent = sum(r.get("n_sent", 0) for r in results[c])
        lost = sum(r.get("n_lost", 0) for r in results[c])
        loss_rates.append((lost / sent * 100) if sent > 0 else 0)

    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in loss_rates]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(labels, loss_rates, color=bar_colors, alpha=0.8, edgecolor="black")
    ax.set_ylabel("Message Loss Rate (%)")
    ax.set_title("Message Loss Rate per Condition (H3: should be 0%)")
    ax.grid(axis="y", alpha=0.3)
    if max(loss_rates) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No message loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "loss_rate.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/loss_rate.png")

    # ── Plot 5: RTL std dev vs N ──────────────────────────────────────────
    stddevs = []
    for c in conditions:
        vals = [r["rtl_stddev_us"] for r in results[c] if "rtl_stddev_us" in r]
        stddevs.append(sum(vals) / len(vals) if vals else 0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(labels, stddevs, color=colors[:len(conditions)], alpha=0.8, edgecolor="black")
    ax.set_ylabel("Mean RTL Std Dev (µs)")
    ax.set_title("RTL Jitter (Std Dev) per Condition — channel predictability")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "rtl_stddev.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/rtl_stddev.png")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyze Scenario 2 (Inter-VM Communication Latency) results"
    )
    parser.add_argument(
        "-r", "--results-dir",
        default=os.path.join(os.path.dirname(__file__), "results_scenario2"),
        help="Path to results directory (default: experiments/results_scenario2)",
    )
    args = parser.parse_args()

    results_dir = args.results_dir
    plots_dir   = os.path.join(results_dir, "plots")

    print("Scenario 2 Analysis — Inter-VM Communication Latency")
    print(f"Results directory: {results_dir}")
    print()

    results = load_results(results_dir)
    if not results:
        print("ERROR: No results found.  Run experiments first.")
        print(f"Expected: {results_dir}/<condition>/run_XX.txt")
        sys.exit(1)

    total_runs = sum(len(v) for v in results.values())
    print(f"Loaded {total_runs} runs across {len(results)} conditions.")

    print_summary_table(results)

    print("\nGenerating plots...")
    generate_plots(results, plots_dir)

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
