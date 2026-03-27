#!/usr/bin/env python3
"""
FFI Experiment Analysis Script
Parses experiment results and generates plots and summary tables.

Usage:
    python3 experiments/analyze_results.py                          # Default results dir
    python3 experiments/analyze_results.py -r experiments/results   # Custom results dir
"""

import os
import re
import sys
import csv
import argparse
from pathlib import Path
from collections import defaultdict

# ─── Constants ────────────────────────────────────────────────────────────────

DEADLINE_US = 30000.0  # 30ms deadline in microseconds

SCENARIO_ORDER = [
    "S0_baseline",
    "S1_cpu",
    "S2_cache",
    "S3_stream",
    "S4_io",
    "S5_cache_stream",
    "S6_worst_case",
]

SCENARIO_LABELS = {
    "S0_baseline": "Baseline",
    "S1_cpu": "CPU",
    "S2_cache": "Cache",
    "S3_stream": "Stream",
    "S4_io": "I/O",
    "S5_cache_stream": "Cache+Stream",
    "S6_worst_case": "Worst Case",
}

# ─── Parsing ──────────────────────────────────────────────────────────────────


def parse_run_file(filepath):
    """Parse a single run output file and extract statistics."""
    text = Path(filepath).read_text()

    stats = {}

    # Total cycles
    m = re.search(r"Total cycles:\s+(\d+)", text)
    if m:
        stats["total_cycles"] = int(m.group(1))

    # Deadline misses
    m = re.search(r"Deadline misses:\s+(\d+)\s+\(([0-9.]+)%\)", text)
    if m:
        stats["deadline_misses"] = int(m.group(1))
        stats["deadline_miss_pct"] = float(m.group(2))

    # Execution time
    m = re.search(r"Execution time:\s*\n\s*Mean:\s+([0-9.]+)\s+us", text)
    if m:
        stats["exec_mean_us"] = float(m.group(1))

    m = re.search(r"Execution time:.*?Min:\s+([0-9.]+)\s+us", text, re.DOTALL)
    if m:
        stats["exec_min_us"] = float(m.group(1))

    m = re.search(r"Execution time:.*?Max:\s+([0-9.]+)\s+us", text, re.DOTALL)
    if m:
        stats["exec_max_us"] = float(m.group(1))

    # Jitter
    m = re.search(r"Jitter:\s*\n\s*Mean:\s+([0-9.]+)\s+us", text)
    if m:
        stats["jitter_mean_us"] = float(m.group(1))

    m = re.search(r"Jitter:.*?Max:\s+([0-9.]+)\s+us", text, re.DOTALL)
    if m:
        stats["jitter_max_us"] = float(m.group(1))

    # Response time
    m = re.search(r"Response time:\s*\n\s*Mean:\s+([0-9.]+)\s+us", text)
    if m:
        stats["resp_mean_us"] = float(m.group(1))

    m = re.search(r"Response time:.*?Min:\s+([0-9.]+)\s+us", text, re.DOTALL)
    if m:
        stats["resp_min_us"] = float(m.group(1))

    m = re.search(r"Response time:.*?Max:\s+([0-9.]+)\s+us", text, re.DOTALL)
    if m:
        stats["resp_max_us"] = float(m.group(1))

    return stats if stats else None


def load_results(results_dir):
    """Load all run files from the results directory."""
    results = defaultdict(list)

    for scenario in SCENARIO_ORDER:
        scenario_dir = Path(results_dir) / scenario
        if not scenario_dir.is_dir():
            continue

        run_files = sorted(scenario_dir.glob("run_*.txt"))
        for run_file in run_files:
            stats = parse_run_file(run_file)
            if stats:
                results[scenario].append(stats)

    return results


# ─── Summary Table ────────────────────────────────────────────────────────────


def print_summary_table(results):
    """Print a summary table of all scenarios."""
    print("\n" + "=" * 120)
    print("EXPERIMENT SUMMARY TABLE")
    print("=" * 120)

    header = (
        f"{'Scenario':<16} {'Runs':>4} {'Cycles':>7} "
        f"{'Miss%':>7} "
        f"{'Exec Mean':>10} {'Exec Max':>10} "
        f"{'Jit Mean':>10} {'Jit Max':>10} "
        f"{'Resp Mean':>10} {'Resp Max':>10} "
        f"{'Margin%':>8}"
    )
    print(header)
    print("-" * 120)

    for scenario in SCENARIO_ORDER:
        if scenario not in results:
            continue

        runs = results[scenario]
        n = len(runs)
        label = SCENARIO_LABELS.get(scenario, scenario)

        # Aggregate across runs
        total_cycles = sum(r.get("total_cycles", 0) for r in runs)
        total_misses = sum(r.get("deadline_misses", 0) for r in runs)
        miss_pct = (total_misses / total_cycles * 100) if total_cycles > 0 else 0.0

        exec_means = [r["exec_mean_us"] for r in runs if "exec_mean_us" in r]
        exec_maxes = [r["exec_max_us"] for r in runs if "exec_max_us" in r]
        jitter_means = [r["jitter_mean_us"] for r in runs if "jitter_mean_us" in r]
        jitter_maxes = [r["jitter_max_us"] for r in runs if "jitter_max_us" in r]
        resp_means = [r["resp_mean_us"] for r in runs if "resp_mean_us" in r]
        resp_maxes = [r["resp_max_us"] for r in runs if "resp_max_us" in r]

        avg_exec_mean = sum(exec_means) / len(exec_means) if exec_means else 0
        worst_exec_max = max(exec_maxes) if exec_maxes else 0
        avg_jitter_mean = sum(jitter_means) / len(jitter_means) if jitter_means else 0
        worst_jitter_max = max(jitter_maxes) if jitter_maxes else 0
        avg_resp_mean = sum(resp_means) / len(resp_means) if resp_means else 0
        worst_resp_max = max(resp_maxes) if resp_maxes else 0

        # Safety margin: use actual worst-case response time if available,
        # otherwise fall back to jitter + execution approximation
        if resp_maxes:
            worst_response_us = worst_resp_max
        else:
            worst_response_us = worst_jitter_max + worst_exec_max
        safety_margin = ((DEADLINE_US - worst_response_us) / DEADLINE_US * 100)

        print(
            f"{label:<16} {n:>4} {total_cycles:>7} "
            f"{miss_pct:>6.2f}% "
            f"{avg_exec_mean:>9.1f}µ {worst_exec_max:>9.1f}µ "
            f"{avg_jitter_mean:>9.1f}µ {worst_jitter_max:>9.1f}µ "
            f"{avg_resp_mean:>9.1f}µ {worst_resp_max:>9.1f}µ "
            f"{safety_margin:>7.1f}%"
        )

    print("=" * 120)
    print(f"Deadline: {DEADLINE_US:.0f} µs ({DEADLINE_US/1000:.0f} ms)")
    print(
        "Margin% = (deadline - worst_response) / deadline × 100  "
        "(negative = deadline exceeded)"
    )


# ─── Per-Cycle & Monitoring Parsing ───────────────────────────────────────────


def parse_cycles_csv(filepath):
    """Parse a run_XX_cycles.csv file into lists of per-cycle data."""
    rows = []
    try:
        with open(filepath) as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "cycle": int(row["cycle"]),
                    "timestamp_s": float(row["timestamp_s"]),
                    "execution_time_us": float(row["execution_time_us"]),
                    "jitter_us": float(row["jitter_us"]),
                    "response_time_us": float(row["response_time_us"]),
                    "deadline_miss": int(row["deadline_miss"]),
                })
    except (FileNotFoundError, KeyError, ValueError):
        pass
    return rows


def parse_vmstat(run_file):
    """Extract vmstat rows from a run_XX.txt file (between VMSTAT markers)."""
    rows = []
    try:
        text = Path(run_file).read_text()
        m = re.search(r"===VMSTAT_START===\n(.+?)===VMSTAT_END===", text, re.DOTALL)
        if not m:
            return rows

        lines = m.group(1).strip().splitlines()
        # vmstat header: first two lines (labels), then data rows
        # Find the header line with column names
        header_idx = None
        for i, line in enumerate(lines):
            if "us" in line and "sy" in line and "id" in line:
                header_idx = i
                break
        if header_idx is None:
            return rows

        cols = lines[header_idx].split()
        second = 0
        for line in lines[header_idx + 1:]:
            vals = line.split()
            if len(vals) != len(cols):
                continue
            try:
                entry = {c: int(v) for c, v in zip(cols, vals)}
                entry["timestamp_s"] = float(second)
                rows.append(entry)
                second += 1
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return rows


def load_timeseries(results_dir):
    """Load per-cycle and vmstat data for every run.

    Returns {scenario: [(cycles_rows, vmstat_rows), ...]} — one tuple per run.
    """
    ts_data = defaultdict(list)
    for scenario in SCENARIO_ORDER:
        scenario_dir = Path(results_dir) / scenario
        if not scenario_dir.is_dir():
            continue
        run_idx = 1
        while True:
            run_num = f"{run_idx:02d}"
            run_file = scenario_dir / f"run_{run_num}.txt"
            cycles_file = scenario_dir / f"run_{run_num}_cycles.csv"
            if not run_file.exists():
                break
            cycles = parse_cycles_csv(cycles_file) if cycles_file.exists() else []
            vmstat = parse_vmstat(run_file)
            ts_data[scenario].append((cycles, vmstat))
            run_idx += 1
    return ts_data


# ─── Plotting ─────────────────────────────────────────────────────────────────


def generate_plots(results, output_dir):
    """Generate all plots and save to output directory."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Collect data in scenario order
    scenarios = [s for s in SCENARIO_ORDER if s in results]
    labels = [SCENARIO_LABELS.get(s, s) for s in scenarios]

    # ── Plot 1: Box plot of max jitter per scenario ──────────────────────

    jitter_max_data = []
    for s in scenarios:
        jitter_max_data.append(
            [r["jitter_max_us"] for r in results[s] if "jitter_max_us" in r]
        )

    fig, ax = plt.subplots(figsize=(10, 6))
    bp = ax.boxplot(jitter_max_data, labels=labels, patch_artist=True)

    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#FF5722", "#B71C1C"]
    for patch, color in zip(bp["boxes"], colors[: len(scenarios)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_ylabel("Max Jitter (µs)")
    ax.set_title("Maximum Wake-up Jitter per Scenario (across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "jitter_max_boxplot.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/jitter_max_boxplot.png")

    # ── Plot 2: Bar chart of mean execution time ─────────────────────────

    exec_mean_avgs = []
    exec_mean_stds = []
    for s in scenarios:
        vals = [r["exec_mean_us"] for r in results[s] if "exec_mean_us" in r]
        avg = sum(vals) / len(vals) if vals else 0
        std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5 if len(vals) > 1 else 0
        exec_mean_avgs.append(avg)
        exec_mean_stds.append(std)

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(labels, exec_mean_avgs, yerr=exec_mean_stds, capsize=5,
                  color=colors[: len(scenarios)], alpha=0.7, edgecolor="black")
    ax.set_ylabel("Mean Execution Time (µs)")
    ax.set_title("Mean Execution Time per Scenario (avg ± std across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "exec_time_bar.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/exec_time_bar.png")

    # ── Plot 3: Deadline miss rate bar chart ──────────────────────────────

    miss_rates = []
    for s in scenarios:
        total_cycles = sum(r.get("total_cycles", 0) for r in results[s])
        total_misses = sum(r.get("deadline_misses", 0) for r in results[s])
        rate = (total_misses / total_cycles * 100) if total_cycles > 0 else 0
        miss_rates.append(rate)

    fig, ax = plt.subplots(figsize=(10, 6))
    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in miss_rates]
    ax.bar(labels, miss_rates, color=bar_colors, alpha=0.7, edgecolor="black")
    ax.set_ylabel("Deadline Miss Rate (%)")
    ax.set_title(f"Deadline Miss Rate per Scenario (deadline = {DEADLINE_US/1000:.0f} ms)")
    ax.grid(axis="y", alpha=0.3)

    # If all zeros, set y-axis to show some range
    if max(miss_rates) == 0:
        ax.set_ylim(0, 1)
        ax.text(
            0.5, 0.5, "No deadline misses in any scenario",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=14, color="green", fontweight="bold",
        )

    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "deadline_miss_rate.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/deadline_miss_rate.png")

    # ── Plot 4: Safety margin bar chart ───────────────────────────────────

    margins = []
    for s in scenarios:
        runs = results[s]
        resp_maxes = [r["resp_max_us"] for r in runs if "resp_max_us" in r]
        if resp_maxes:
            worst_response = max(resp_maxes)
        else:
            exec_maxes = [r["exec_max_us"] for r in runs if "exec_max_us" in r]
            jitter_maxes = [r["jitter_max_us"] for r in runs if "jitter_max_us" in r]
            worst_response = (max(jitter_maxes) if jitter_maxes else 0) + (
                max(exec_maxes) if exec_maxes else 0
            )
        margin = (DEADLINE_US - worst_response) / DEADLINE_US * 100
        margins.append(margin)

    fig, ax = plt.subplots(figsize=(10, 6))
    margin_colors = ["#4CAF50" if m > 0 else "#F44336" for m in margins]
    ax.bar(labels, margins, color=margin_colors, alpha=0.7, edgecolor="black")
    ax.axhline(y=0, color="red", linestyle="--", linewidth=1, label="Deadline boundary")
    ax.set_ylabel("Safety Margin (%)")
    ax.set_title("Safety Margin per Scenario (positive = within deadline)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "safety_margin.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/safety_margin.png")

    # ── Plot 5: Mean jitter comparison (bar chart) ───────────────────────

    jitter_mean_avgs = []
    jitter_mean_stds = []
    for s in scenarios:
        vals = [r["jitter_mean_us"] for r in results[s] if "jitter_mean_us" in r]
        avg = sum(vals) / len(vals) if vals else 0
        std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5 if len(vals) > 1 else 0
        jitter_mean_avgs.append(avg)
        jitter_mean_stds.append(std)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(labels, jitter_mean_avgs, yerr=jitter_mean_stds, capsize=5,
           color=colors[: len(scenarios)], alpha=0.7, edgecolor="black")
    ax.set_ylabel("Mean Jitter (µs)")
    ax.set_title("Mean Wake-up Jitter per Scenario (avg ± std across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "jitter_mean_bar.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/jitter_mean_bar.png")


def generate_timeseries_plots(ts_data, output_dir):
    """Generate per-scenario time-series plots (response time + resource usage)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping time-series plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    for scenario in SCENARIO_ORDER:
        if scenario not in ts_data or not ts_data[scenario]:
            continue

        label = SCENARIO_LABELS.get(scenario, scenario)

        # Use the first run that has per-cycle data
        cycles, vmstat = None, None
        for c, v in ts_data[scenario]:
            if c:
                cycles, vmstat = c, v
                break

        if not cycles:
            continue

        has_vmstat = bool(vmstat)
        nrows = 2 if has_vmstat else 1
        fig, axes = plt.subplots(nrows, 1, figsize=(14, 5 * nrows), sharex=True)
        if nrows == 1:
            axes = [axes]

        # ── Top: Response time timeline ──────────────────────────────────
        ax_rt = axes[0]
        ts = [r["timestamp_s"] for r in cycles]
        rt = [r["response_time_us"] for r in cycles]
        misses_ts = [r["timestamp_s"] for r in cycles if r["deadline_miss"]]
        misses_rt = [r["response_time_us"] for r in cycles if r["deadline_miss"]]

        ax_rt.plot(ts, rt, linewidth=0.8, color="#2196F3", label="Response time")
        ax_rt.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                      label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
        if misses_ts:
            ax_rt.scatter(misses_ts, misses_rt, color="red", s=30, zorder=5,
                          label=f"Deadline miss ({len(misses_ts)})")
        ax_rt.set_ylabel("Response Time (µs)")
        ax_rt.set_title(f"{label} — Response Time & Resource Usage (run 1)")
        ax_rt.legend(loc="upper right", fontsize=8)
        ax_rt.grid(alpha=0.3)

        # ── Bottom: vmstat resource usage ────────────────────────────────
        if has_vmstat:
            ax_res = axes[1]
            vm_ts = [r["timestamp_s"] for r in vmstat]
            cpu_us = [r.get("us", 0) + r.get("sy", 0) for r in vmstat]
            io_bi = [r.get("bi", 0) for r in vmstat]
            io_bo = [r.get("bo", 0) for r in vmstat]
            cs = [r.get("cs", 0) for r in vmstat]

            ax_res.plot(vm_ts, cpu_us, linewidth=1, color="#FF9800", label="CPU% (us+sy)")
            ax_res.set_ylabel("CPU %", color="#FF9800")
            ax_res.tick_params(axis="y", labelcolor="#FF9800")
            ax_res.set_ylim(0, 105)

            ax_io = ax_res.twinx()
            ax_io.plot(vm_ts, io_bi, linewidth=0.8, color="#9C27B0", alpha=0.7,
                       label="Block In")
            ax_io.plot(vm_ts, io_bo, linewidth=0.8, color="#4CAF50", alpha=0.7,
                       label="Block Out")
            ax_io.set_ylabel("Block I/O (blocks/s)")

            # Mark deadline misses on the resource plot too
            for mt in misses_ts:
                ax_res.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

            # Combined legend
            lines1, labels1 = ax_res.get_legend_handles_labels()
            lines2, labels2 = ax_io.get_legend_handles_labels()
            ax_res.legend(lines1 + lines2, labels1 + labels2,
                          loc="upper right", fontsize=8)

            ax_res.set_xlabel("Time (s)")
            ax_res.grid(alpha=0.3)
        else:
            axes[0].set_xlabel("Time (s)")

        fig.tight_layout()
        fname = os.path.join(output_dir, f"timeseries_{scenario}.png")
        fig.savefig(fname, dpi=150)
        plt.close(fig)
        print(f"  Saved: {fname}")

    # ── Combined comparison: response time envelope across scenarios ──
    fig, ax = plt.subplots(figsize=(14, 6))
    scenario_colors = {
        "S0_baseline": "#4CAF50", "S1_cpu": "#2196F3", "S2_cache": "#FF9800",
        "S3_stream": "#9C27B0", "S4_io": "#F44336", "S5_cache_stream": "#FF5722",
        "S6_worst_case": "#B71C1C",
    }
    plotted = False
    for scenario in SCENARIO_ORDER:
        if scenario not in ts_data or not ts_data[scenario]:
            continue
        for c, _ in ts_data[scenario]:
            if c:
                ts = [r["timestamp_s"] for r in c]
                rt = [r["response_time_us"] for r in c]
                ax.plot(ts, rt, linewidth=0.8, alpha=0.7,
                        color=scenario_colors.get(scenario, "gray"),
                        label=SCENARIO_LABELS.get(scenario, scenario))
                plotted = True
                break  # first run only

    if plotted:
        ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                   label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Response Time (µs)")
        ax.set_title("Response Time Comparison Across Scenarios")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fname = os.path.join(output_dir, "timeseries_comparison.png")
        fig.savefig(fname, dpi=150)
        print(f"  Saved: {fname}")
    plt.close(fig)


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Analyze FFI experiment results")
    parser.add_argument(
        "-r", "--results-dir",
        default=os.path.join(os.path.dirname(__file__), "results"),
        help="Path to results directory (default: experiments/results)",
    )
    args = parser.parse_args()

    results_dir = args.results_dir
    plots_dir = os.path.join(results_dir, "plots")

    print("FFI Experiment Analysis")
    print(f"Results directory: {results_dir}")
    print()

    # Load data
    results = load_results(results_dir)

    if not results:
        print("ERROR: No results found. Run experiments first.")
        print(f"Expected directory structure: {results_dir}/<scenario>/run_XX.txt")
        sys.exit(1)

    total_runs = sum(len(runs) for runs in results.values())
    print(f"Loaded {total_runs} runs across {len(results)} scenarios.")

    # Summary table
    print_summary_table(results)

    # Load time-series data
    ts_data = load_timeseries(results_dir)
    ts_runs = sum(len(runs) for runs in ts_data.values())
    print(f"Loaded time-series data for {ts_runs} runs.")

    # Plots
    print("\nGenerating aggregate plots...")
    generate_plots(results, plots_dir)

    if ts_data:
        print("\nGenerating time-series plots...")
        generate_timeseries_plots(ts_data, plots_dir)

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
