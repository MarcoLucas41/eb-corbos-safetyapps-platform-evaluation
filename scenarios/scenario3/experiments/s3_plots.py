#!/usr/bin/env python3
"""
Scenario 3 Analysis Script — Network Communication Determinism

Parses run_XX.txt files produced by run_scenario3.sh, prints a summary
table, and generates plots for each hypothesis (H0–H4).

Usage:
    python3 experiments/analyze_scenario3.py
    python3 experiments/analyze_scenario3.py -r experiments/results_scenario3
"""

import os
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# ─── Constants ────────────────────────────────────────────────────────────────

# P99 OWL must stay within this factor of baseline for the channel to be
# considered "predictable" (from the design document acceptance criteria).
PREDICTABILITY_FACTOR = 5.0

# IAJ stddev must stay within this factor of baseline stddev.
IAJ_STDDEV_FACTOR = 3.0

PRIMARY_ORDER = [
    "C0_baseline",
    "C1_16mbps",
    "C2_32mbps",
    "C3_64mbps",
    "C4_128mbps",
    "C5_256mbps",
]

PRIMARY_LABELS = {
    "C0_baseline": "N=0",
    "C1_16mbps":   "N=16",
    "C2_32mbps":   "N=32",
    "C3_64mbps":   "N=64",
    "C4_128mbps":  "N=128",
    "C5_256mbps":  "N=256",
}

N_MBPS = {
    "C0_baseline": 0,
    "C1_16mbps":   16,
    "C2_32mbps":   32,
    "C3_64mbps":   64,
    "C4_128mbps":  128,
    "C5_256mbps":  256,
}

NETEM_ORDER = [
    "D1_delay_1ms",
    "D2_delay_5ms_jitter",
    "L1_loss_0p5",
    "L2_loss_2p0",
]

NETEM_LABELS = {
    "D1_delay_1ms":          "D1 +1ms",
    "D2_delay_5ms_jitter":   "D2 +5ms±1ms",
    "L1_loss_0p5":           "L1 0.5%",
    "L2_loss_2p0":           "L2 2.0%",
}

# ─── Parser ───────────────────────────────────────────────────────────────────

def parse_run_file(path):
    """Extract statistics from a single net_receiver run output file."""
    text = Path(path).read_text(errors="replace")
    s = {}

    m = re.search(r"Packets expected:\s+(\d+)", text)
    if m: s["n_expected"] = int(m.group(1))

    m = re.search(r"Packets received:\s+(\d+)", text)
    if m: s["n_received"] = int(m.group(1))

    m = re.search(r"Packets lost:\s+(\d+)\s+\(([0-9.]+)%\)", text)
    if m:
        s["n_lost"]    = int(m.group(1))
        s["loss_pct"]  = float(m.group(2))

    # OWL block — appears before IAJ block in the output
    owl_block = re.search(r"OWL \(us\):(.*?)(?=IAJ \(us\)|={5})", text, re.DOTALL)
    if owl_block:
        b = owl_block.group(1)
        m = re.search(r"Mean:\s+([0-9.]+)\s+Std dev:\s+([0-9.]+)", b)
        if m:
            s["owl_mean_us"]   = float(m.group(1))
            s["owl_stddev_us"] = float(m.group(2))
        m = re.search(r"Min:\s+([0-9.]+)\s+Max:\s+([0-9.]+)", b)
        if m:
            s["owl_min_us"] = float(m.group(1))
            s["owl_max_us"] = float(m.group(2))
        m = re.search(r"P50:\s+([0-9.]+)\s+P95:\s+([0-9.]+)", b)
        if m:
            s["owl_p50_us"] = float(m.group(1))
            s["owl_p95_us"] = float(m.group(2))
        m = re.search(r"P99:\s+([0-9.]+)\s+P99\.9:\s+([0-9.]+)", b)
        if m:
            s["owl_p99_us"]  = float(m.group(1))
            s["owl_p999_us"] = float(m.group(2))

    # IAJ block
    iaj_block = re.search(r"IAJ \(us\):(.*?)(?=={5})", text, re.DOTALL)
    if iaj_block:
        b = iaj_block.group(1)
        m = re.search(r"Mean:\s+([0-9.]+)\s+Std dev:\s+([0-9.]+)", b)
        if m:
            s["iaj_mean_us"]   = float(m.group(1))
            s["iaj_stddev_us"] = float(m.group(2))
        m = re.search(r"P99:\s+([0-9.]+)\s+P99\.9:\s+([0-9.]+)", b)
        if m:
            s["iaj_p99_us"]  = float(m.group(1))
            s["iaj_p999_us"] = float(m.group(2))

    return s if s else None


def load_results(results_dir, condition_list):
    results = defaultdict(list)
    for cond in condition_list:
        cond_dir = Path(results_dir) / cond
        if not cond_dir.is_dir():
            continue
        for run_file in sorted(cond_dir.glob("run_*.txt")):
            s = parse_run_file(run_file)
            if s:
                results[cond].append(s)
    return results


def avg(lst):
    return sum(lst) / len(lst) if lst else 0.0


def vals(runs, key):
    return [r[key] for r in runs if key in r]

# ─── Summary Table ────────────────────────────────────────────────────────────

def print_primary_table(results, baseline_p99, baseline_iaj_std):
    print("\n" + "=" * 120)
    print("SCENARIO 3 — PRIMARY CONDITIONS SUMMARY (OWL = one-way latency, IAJ = inter-arrival jitter)")
    print("=" * 120)
    hdr = (f"{'Condition':<14} {'N(Mbps)':>8} {'Runs':>4} "
           f"{'Lost%':>6} "
           f"{'OWLmean':>9} {'OWLstd':>8} "
           f"{'OWLP99':>9} {'OWLP99.9':>9} "
           f"{'IAJmean':>9} {'IAJstd':>8} "
           f"{'IAJP99':>9}")
    print(hdr)
    print("-" * 120)

    for cond in PRIMARY_ORDER:
        if cond not in results:
            continue
        runs = results[cond]
        n    = len(runs)
        lbl  = PRIMARY_LABELS.get(cond, cond)
        mbps = N_MBPS.get(cond, "?")

        sent = sum(r.get("n_expected", 0) for r in runs)
        lost = sum(r.get("n_lost",     0) for r in runs)
        loss_pct = (lost / sent * 100) if sent > 0 else 0.0

        owl_mean  = avg(vals(runs, "owl_mean_us"))
        owl_std   = avg(vals(runs, "owl_stddev_us"))
        owl_p99   = avg(vals(runs, "owl_p99_us"))
        owl_p999  = avg(vals(runs, "owl_p999_us"))
        iaj_mean  = avg(vals(runs, "iaj_mean_us"))
        iaj_std   = avg(vals(runs, "iaj_stddev_us"))
        iaj_p99   = avg(vals(runs, "iaj_p99_us"))

        flags = ""
        if baseline_p99   and owl_p99  > baseline_p99   * PREDICTABILITY_FACTOR:
            flags += " !OWL"
        if baseline_iaj_std and iaj_std > baseline_iaj_std * IAJ_STDDEV_FACTOR:
            flags += " !IAJ"

        print(f"{lbl:<14} {mbps:>8} {n:>4} "
              f"{loss_pct:>5.2f}% "
              f"{owl_mean:>9.1f} {owl_std:>8.1f} "
              f"{owl_p99:>9.1f} {owl_p999:>9.1f} "
              f"{iaj_mean:>9.1f} {iaj_std:>8.1f} "
              f"{iaj_p99:>9.1f}"
              f"{flags}")

    print("=" * 120)
    print("All latency values in µs.")
    if baseline_p99:
        thr = baseline_p99 * PREDICTABILITY_FACTOR
        print(f"!OWL = P99 OWL exceeds {PREDICTABILITY_FACTOR}× baseline "
              f"({baseline_p99:.1f} µs → threshold {thr:.1f} µs)")
    if baseline_iaj_std:
        thr = baseline_iaj_std * IAJ_STDDEV_FACTOR
        print(f"!IAJ = IAJ stddev exceeds {IAJ_STDDEV_FACTOR}× baseline "
              f"({baseline_iaj_std:.1f} µs → threshold {thr:.1f} µs)")


def print_netem_table(results, baseline_owl_mean):
    print("\n" + "=" * 80)
    print("SCENARIO 3 — NETEM CONDITIONS SUMMARY (H4: impairment fidelity)")
    print("=" * 80)
    print(f"{'Condition':<22} {'Runs':>4} {'Lost%':>6} "
          f"{'OWLmean':>9} {'ΔOWL':>8} {'OWLstd':>8} {'Lost/run':>9}")
    print("-" * 80)

    for cond in NETEM_ORDER:
        if cond not in results:
            continue
        runs = results[cond]
        n    = len(runs)
        lbl  = NETEM_LABELS.get(cond, cond)

        sent = sum(r.get("n_expected", 0) for r in runs)
        lost = sum(r.get("n_lost",     0) for r in runs)
        loss_pct = (lost / sent * 100) if sent > 0 else 0.0

        owl_mean = avg(vals(runs, "owl_mean_us"))
        owl_std  = avg(vals(runs, "owl_stddev_us"))
        delta    = owl_mean - baseline_owl_mean if baseline_owl_mean else 0.0
        lost_run = (lost / n) if n > 0 else 0.0

        print(f"{lbl:<22} {n:>4} {loss_pct:>5.2f}% "
              f"{owl_mean:>9.1f} {delta:>+8.1f} {owl_std:>8.1f} {lost_run:>9.1f}")

    print("=" * 80)
    if baseline_owl_mean:
        print(f"Baseline OWL mean: {baseline_owl_mean:.1f} µs  "
              f"ΔOWL = condition mean − baseline mean")

# ─── Plots ────────────────────────────────────────────────────────────────────

def generate_plots(primary_results, netem_results, output_dir, baseline_owl_mean):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed — skipping plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    conditions = [c for c in PRIMARY_ORDER if c in primary_results]
    labels     = [PRIMARY_LABELS[c] for c in conditions]
    n_vals     = [N_MBPS[c] for c in conditions]

    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#B71C1C"]

    # ── Plot 1: Mean OWL ± std vs N ─────────────────────────────────────
    means = []; stds = []
    for c in conditions:
        m_vals = vals(primary_results[c], "owl_mean_us")
        mn = avg(m_vals)
        sd = (sum((v - mn) ** 2 for v in m_vals) / len(m_vals)) ** 0.5 \
             if len(m_vals) > 1 else 0.0
        means.append(mn); stds.append(sd)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=stds, fmt="o-", capsize=5,
                color="#2196F3", linewidth=2, markersize=7)
    ax.set_xlabel("Background Flood Rate (Mbps)")
    ax.set_ylabel("Mean OWL (µs)")
    ax.set_title("Mean One-Way Latency vs Background Flood Rate (H1)")
    ax.set_xticks(n_vals); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "owl_mean_vs_n.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/owl_mean_vs_n.png")

    # ── Plot 2: OWL percentile lines vs N ───────────────────────────────
    p50s = [avg(vals(primary_results[c], "owl_p50_us"))  for c in conditions]
    p99s = [avg(vals(primary_results[c], "owl_p99_us"))  for c in conditions]
    p999s= [avg(vals(primary_results[c], "owl_p999_us")) for c in conditions]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, p50s,  "o-", label="P50",   color="#4CAF50", linewidth=2)
    ax.plot(n_vals, p99s,  "s-", label="P99",   color="#FF9800", linewidth=2)
    ax.plot(n_vals, p999s, "^-", label="P99.9", color="#F44336", linewidth=2)
    ax.set_xlabel("Background Flood Rate (Mbps)")
    ax.set_ylabel("OWL (µs)")
    ax.set_title("OWL Tail Growth vs Load (H2: tail grows faster than mean)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "owl_percentiles_vs_n.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/owl_percentiles_vs_n.png")

    # ── Plot 3: IAJ stddev vs N ──────────────────────────────────────────
    iaj_stds = [avg(vals(primary_results[c], "iaj_stddev_us")) for c in conditions]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, iaj_stds, "D-", color="#9C27B0", linewidth=2, markersize=7)
    ax.set_xlabel("Background Flood Rate (Mbps)")
    ax.set_ylabel("IAJ Std Dev (µs)")
    ax.set_title("Inter-Arrival Jitter vs Background Flood Rate")
    ax.set_xticks(n_vals); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "iaj_stddev_vs_n.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/iaj_stddev_vs_n.png")

    # ── Plot 4: Box plots of per-run mean OWL per primary condition ──────
    box_data = [vals(primary_results[c], "owl_mean_us") for c in conditions]
    fig, ax = plt.subplots(figsize=(9, 5))
    bp = ax.boxplot(box_data, labels=labels, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors[:len(conditions)]):
        patch.set_facecolor(color); patch.set_alpha(0.7)
    ax.set_ylabel("Per-Run Mean OWL (µs)")
    ax.set_title("OWL Distribution per Condition (run-to-run variance)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "owl_boxplot.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/owl_boxplot.png")

    # ── Plot 5: Packet loss rate per primary condition ───────────────────
    loss_rates = []
    for c in conditions:
        sent = sum(r.get("n_expected", 0) for r in primary_results[c])
        lost = sum(r.get("n_lost",     0) for r in primary_results[c])
        loss_rates.append((lost / sent * 100) if sent > 0 else 0.0)

    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in loss_rates]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(labels, loss_rates, color=bar_colors, alpha=0.8, edgecolor="black")
    ax.set_ylabel("Packet Loss Rate (%)")
    ax.set_title("Packet Loss Rate per Condition (H3: should be 0%)")
    ax.grid(axis="y", alpha=0.3)
    if max(loss_rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No packet loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "loss_rate.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/loss_rate.png")

    # ── Plot 6: OWL mean per netem condition (H4) ────────────────────────
    if netem_results and baseline_owl_mean:
        netem_conds  = [c for c in NETEM_ORDER if c in netem_results]
        netem_lbl    = ["Baseline"] + [NETEM_LABELS[c] for c in netem_conds]
        netem_means  = [baseline_owl_mean] + \
                       [avg(vals(netem_results[c], "owl_mean_us")) for c in netem_conds]

        fig, ax = plt.subplots(figsize=(9, 5))
        bar_c = ["#4CAF50"] + ["#2196F3"] * len(netem_conds)
        ax.bar(netem_lbl, netem_means, color=bar_c, alpha=0.8, edgecolor="black")
        ax.set_ylabel("Mean OWL (µs)")
        ax.set_title("Mean OWL per Impairment Condition (H4: netem fidelity)")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(output_dir, "owl_netem_conditions.png"), dpi=150)
        plt.close(fig)
        print(f"  Saved: {output_dir}/owl_netem_conditions.png")

    # ── Plot 7: P99.9 / mean ratio vs N (tail growth index) ─────────────
    ratios = []
    for c in conditions:
        m = avg(vals(primary_results[c], "owl_mean_us"))
        p = avg(vals(primary_results[c], "owl_p999_us"))
        ratios.append(p / m if m > 0 else 0.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, ratios, "o-", color="#F44336", linewidth=2, markersize=7)
    ax.set_xlabel("Background Flood Rate (Mbps)")
    ax.set_ylabel("P99.9 OWL / Mean OWL")
    ax.set_title("Tail Growth Index vs Load — ratio > 1 indicates heavy tail (H2)")
    ax.set_xticks(n_vals); ax.grid(axis="y", alpha=0.3)
    ax.axhline(1.0, color="grey", linestyle="--", linewidth=1)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "owl_tail_growth.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/owl_tail_growth.png")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyze Scenario 3 (Network Communication Determinism) results"
    )
    parser.add_argument(
        "-r", "--results-dir",
        default=os.path.join(os.path.dirname(__file__), "results_scenario3"),
        help="Path to results directory (default: experiments/results_scenario3)",
    )
    args = parser.parse_args()

    results_dir = args.results_dir
    plots_dir   = os.path.join(results_dir, "plots")

    print("Scenario 3 Analysis — Network Communication Determinism")
    print(f"Results directory: {results_dir}")
    print()

    primary_results = load_results(results_dir, PRIMARY_ORDER)
    netem_results   = load_results(results_dir, NETEM_ORDER)

    if not primary_results and not netem_results:
        print("ERROR: No results found.  Run experiments first.")
        print(f"Expected: {results_dir}/<condition>/run_XX.txt")
        sys.exit(1)

    total = sum(len(v) for v in {**primary_results, **netem_results}.values())
    print(f"Loaded {total} runs across "
          f"{len(primary_results)} primary + {len(netem_results)} netem conditions.\n")

    # Baseline values for threshold checks
    baseline_p99      = avg(vals(primary_results.get("C0_baseline", []), "owl_p99_us"))
    baseline_iaj_std  = avg(vals(primary_results.get("C0_baseline", []), "iaj_stddev_us"))
    baseline_owl_mean = avg(vals(primary_results.get("C0_baseline", []), "owl_mean_us"))

    if primary_results:
        print_primary_table(primary_results, baseline_p99, baseline_iaj_std)
    if netem_results:
        print_netem_table(netem_results, baseline_owl_mean)

    print("\nGenerating plots...")
    generate_plots(primary_results, netem_results, plots_dir, baseline_owl_mean)

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
