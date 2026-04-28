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

DEADLINE_US = 10000.0  # 30ms deadline in microseconds

SCENARIO_ORDER = [
    "S0_baseline",
    "S1_cpu",
    "S2_cache",
    "S3_stream",
    "S4_io",
    "S5_worst_case",
]

SCENARIO_LABELS = {
    "S0_baseline": "Baseline",
    "S1_cpu": "CPU",
    "S2_cache": "Cache",
    "S3_stream": "Stream",
    "S4_io": "I/O",
    "S5_worst_case": "Worst Case",
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

    # Execution time — matches both "Execution time:" (stress_workload)
    # and "Execution Time (μs):" (speedometer_li); unit suffix is optional
    m = re.search(r"Execution [Tt]ime[^:]*:\s*\n\s*Mean:\s+([0-9.]+)", text)
    if m:
        stats["exec_mean_us"] = float(m.group(1))

    m = re.search(r"Execution [Tt]ime[^:]*:.*?Min:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["exec_min_us"] = float(m.group(1))

    m = re.search(r"Execution [Tt]ime[^:]*:.*?Max:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["exec_max_us"] = float(m.group(1))

    # Jitter — matches both "Jitter:" and "Jitter (μs):"
    m = re.search(r"Jitter[^:]*:\s*\n\s*Mean:\s+([0-9.]+)", text)
    if m:
        stats["jitter_mean_us"] = float(m.group(1))

    m = re.search(r"Jitter[^:]*:.*?Max:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["jitter_max_us"] = float(m.group(1))

    # Response time — matches both "Response time:" and "Response Time (μs):"
    m = re.search(r"Response [Tt]ime[^:]*:\s*\n\s*Mean:\s+([0-9.]+)", text)
    if m:
        stats["resp_mean_us"] = float(m.group(1))

    m = re.search(r"Response [Tt]ime[^:]*:.*?Min:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["resp_min_us"] = float(m.group(1))

    m = re.search(r"Response [Tt]ime[^:]*:.*?Max:\s+([0-9.]+)", text, re.DOTALL)
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


# Regex to strip ANSI escape codes and hypervisor serial mux prefixes
# (e.g. "\x1b[37mvm-hi   | " added by L4/Fiasco.OC serial output)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_VM_PREFIX_RE = re.compile(r"^vm-\S+\s*\|\s*")
_KERNEL_PREFIX_RE = re.compile(r"^\[[\s0-9.]+\][^,]*")


def _clean_serial_line(line):
    """Remove ANSI codes and VM serial mux prefix from a line."""
    line = _ANSI_RE.sub("", line)
    line = _VM_PREFIX_RE.sub("", line)
    line = _KERNEL_PREFIX_RE.sub("", line)
    return line


def _parse_hms_to_seconds(hms):
    """Parse HH:MM:SS and return seconds from start of day."""
    try:
        hh, mm, ss = hms.split(":")
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    except (ValueError, AttributeError):
        return None


def _parse_int_field(value):
    """Parse vmstat numeric fields as int, allowing float-like tokens."""
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def parse_cycles_csv(filepath):
    """Parse a run_XX_cycles.csv file into lists of per-cycle data.

    Handles raw lines that may contain ANSI escape codes and hypervisor
    serial mux prefixes (e.g. from hi VM QEMU serial output).
    """
    rows = []
    try:
        with open(filepath) as f:
            # Clean every line before feeding to DictReader
            cleaned = (_clean_serial_line(line) for line in f)
            reader = csv.DictReader(cleaned)
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


def parse_vmstat(filepath):
    """Parse vmstat CSV into rows with normalized numeric fields.

    Supports CSV files generated from both LI and speedometer outputs,
    strips serial artifacts, handles repeated vmstat headers, and extracts
    timestamp from the trailing HH:MM:SS field when available.
    """
    rows = []
    columns = None
    last_ts = None
    synthetic_ts = 0.0

    try:
        with open(filepath) as f:
            cleaned_lines = (_clean_serial_line(line) for line in f)
            reader = csv.reader(cleaned_lines)

            for raw_row in reader:
                row = [cell.strip() for cell in raw_row if cell is not None]
                row = [cell for cell in row if cell != ""]
                if not row:
                    continue

                first = row[0].lower()

                # Skip vmstat decoration header lines
                if first == "procs":
                    continue

                # Detect/re-detect vmstat header line
                if "r" in row and "us" in row and "sy" in row and "id" in row:
                    columns = row
                    continue

                if columns is None:
                    continue

                values = row[:]

                # vmstat with "-t" yields date + time in two fields while
                # header may only include one timestamp column (UTC).
                if len(values) == len(columns) + 1 and columns[-1].upper() == "UTC":
                    values = values[:-2] + [f"{values[-2]} {values[-1]}"]

                if len(values) < len(columns):
                    continue

                values = values[: len(columns)]

                entry = {}
                timestamp_s = None
                row_valid = True

                for col, value in zip(columns, values):
                    key = col.strip()
                    if not key:
                        continue

                    if key.upper() in ("UTC", "TIMESTAMP"):
                        time_token = value.split()[-1] if value else ""
                        timestamp_s = _parse_hms_to_seconds(time_token)
                        entry["timestamp_raw"] = value
                        continue

                    parsed = _parse_int_field(value)
                    if parsed is None:
                        row_valid = False
                        break
                    entry[key] = parsed

                if not row_valid:
                    continue

                if timestamp_s is None:
                    timestamp_s = synthetic_ts
                elif last_ts is not None and timestamp_s < last_ts:
                    # Handle wrap-around defensively.
                    timestamp_s = float(last_ts + 1)

                entry["timestamp_s"] = float(timestamp_s)
                rows.append(entry)
                last_ts = entry["timestamp_s"]
                synthetic_ts += 1.0
    except FileNotFoundError:
        pass

    return rows


def load_timeseries(results_dir):
    """Load per-cycle and vmstat data for every run.

    Returns {scenario: [{cycles, vmstat_li, vmstat_speedometer, run_num}, ...]}.
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
            vmstat_file = scenario_dir / f"run_{run_num}_vmstat.csv"
            speedometer_vmstat_file = scenario_dir / f"run_{run_num}_speedometer_vmstat.csv"
            if not run_file.exists():
                break
            cycles = parse_cycles_csv(cycles_file) if cycles_file.exists() else []
            vmstat_li = parse_vmstat(vmstat_file) if vmstat_file.exists() else []
            vmstat_speedometer = (
                parse_vmstat(speedometer_vmstat_file)
                if speedometer_vmstat_file.exists()
                else []
            )
            ts_data[scenario].append({
                "cycles": cycles,
                "vmstat_li": vmstat_li,
                "vmstat_speedometer": vmstat_speedometer,
                "run_num": run_num,
            })
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

     # ── Plot 6: Mean response time comparison (bar chart) ───────────────────────
    resp_mean_avgs = []
    resp_mean_stds = []
    for s in scenarios:
        vals = [r["resp_mean_us"] for r in results[s] if "resp_mean_us" in r]
        avg = sum(vals) / len(vals) if vals else 0
        std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5 if len(vals) > 1 else 0
        resp_mean_avgs.append(avg)
        resp_mean_stds.append(std)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(labels, resp_mean_avgs, yerr=resp_mean_stds, capsize=5,
           color=colors[: len(scenarios)], alpha=0.7, edgecolor="black")
    ax.set_ylabel("Mean Response Time (µs)")
    ax.set_title("Mean Response Time per Scenario (avg ± std across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, "response_time_mean_bar.png"), dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_dir}/response_time_mean_bar.png")

    

def generate_timeseries_plots(ts_data, output_dir):
    """Generate per-run time-series plots (response time + vmstat metrics)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping time-series plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    scenario_colors = {
        "S0_baseline": "#4CAF50", "S1_cpu": "#2196F3", "S2_cache": "#FF9800",
        "S3_stream": "#9C27B0", "S4_io": "#F44336", "S5_worst_case": "#B71C1C",
    }

    for scenario in SCENARIO_ORDER:
        if scenario not in ts_data or not ts_data[scenario]:
            continue

        label = SCENARIO_LABELS.get(scenario, scenario)

        for run_data in ts_data[scenario]:
            cycles = run_data["cycles"]
            vmstat = run_data["vmstat_li"]
            speedometer_vmstat = run_data["vmstat_speedometer"]
            run_num = run_data["run_num"]
            if not cycles:
                continue
            has_vmstat = bool(vmstat or speedometer_vmstat)
            nrows = 3 if has_vmstat else 1
            fig, axes = plt.subplots(nrows, 1, figsize=(14, 4 * nrows), sharex=True)
            if nrows == 1:
                axes = [axes]

            # ── Top: Response time timeline ────────────────────────────
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
            ax_rt.set_title(f"{label} — Run {run_num}")
            ax_rt.legend(loc="upper right", fontsize=8)
            ax_rt.grid(alpha=0.3)

            if has_vmstat:
                vm_ts = [r["timestamp_s"] for r in vmstat]
                spm_ts = [r["timestamp_s"] for r in speedometer_vmstat]

                # ── Middle: CPU busy from vmstat (LI + speedometer) ─────
                ax_cpu = axes[1]
                if vmstat:
                    cpu_busy = [100 - r.get("id", 0) for r in vmstat]
                    ax_cpu.plot(
                        vm_ts,
                        cpu_busy,
                        color="#F44336",
                        linewidth=1.2,
                        label="LI CPU busy% (100-id)",
                    )
                if speedometer_vmstat:
                    spm_busy = [100 - r.get("id", 0) for r in speedometer_vmstat]
                    ax_cpu.plot(
                        spm_ts,
                        spm_busy,
                        color="#2196F3",
                        linewidth=1.2,
                        linestyle="--",
                        label="Speedometer CPU busy% (100-id)",
                    )
                ax_cpu.set_ylabel("CPU %")
                ax_cpu.set_ylim(0, 105)
                ax_cpu.legend(loc="upper right", fontsize=8)
                ax_cpu.grid(alpha=0.3)

                # Mark deadline misses
                for mt in misses_ts:
                    ax_cpu.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                # ── Bottom: System metrics (in, cs), LI solid, speedometer dashed
                ax_sys = axes[2]
                interrupts = [r.get("in", 0) for r in vmstat]
                ctx_switches = [r.get("cs", 0) for r in vmstat]
                spm_interrupts = [r.get("in", 0) for r in speedometer_vmstat]
                spm_ctx_switches = [r.get("cs", 0) for r in speedometer_vmstat]

                # Extract optional block I/O metrics if available (bi, bo)
                bi = [r.get("bi", 0) for r in vmstat]
                bo = [r.get("bo", 0) for r in vmstat]
                spm_bi = [r.get("bi", 0) for r in speedometer_vmstat]
                spm_bo = [r.get("bo", 0) for r in speedometer_vmstat]

                if vmstat:
                    ax_sys.plot(vm_ts, interrupts, linewidth=1, color="#9C27B0",
                                label="LI interrupts/s (in)")
                if speedometer_vmstat:
                    ax_sys.plot(spm_ts, spm_interrupts, linewidth=1, color="#9C27B0",
                                linestyle="--", label="Speedometer interrupts/s (in)")
                ax_sys.set_ylabel("Interrupts/s", color="#9C27B0")
                ax_sys.tick_params(axis="y", labelcolor="#9C27B0")

                ax_cs = ax_sys.twinx()
                if vmstat:
                    ax_cs.plot(vm_ts, ctx_switches, linewidth=1, color="#2196F3",
                               label="LI ctx switches/s (cs)")
                if speedometer_vmstat:
                    ax_cs.plot(spm_ts, spm_ctx_switches, linewidth=1, color="#2196F3",
                               linestyle="--", label="Speedometer ctx switches/s (cs)")
                ax_cs.set_ylabel("Ctx Switches/s", color="#2196F3")
                ax_cs.tick_params(axis="y", labelcolor="#2196F3")

                ax_bi = ax_sys.twinx()
                if vmstat:
                    ax_bi.plot(vm_ts, bi, linewidth=1, color="#FF9800", label="LI block in/s (bi)")
                if speedometer_vmstat:
                    ax_bi.plot(spm_ts, spm_bi, linewidth=1, color="#FF9800", linestyle="--",
                               label="Speedometer block in/s (bi)")
                ax_bi.set_ylabel("Block In/s", color="#FF9800")
                ax_bi.tick_params(axis="y", labelcolor="#FF9800")
                ax_bi.spines["right"].set_position(("outward", 30))

                ax_bo = ax_sys.twinx()
                if vmstat:
                    ax_bo.plot(vm_ts, bo, linewidth=1, color="#4CAF50", label="LI block out/s (bo)")
                if speedometer_vmstat:
                    ax_bo.plot(spm_ts, spm_bo, linewidth=1, color="#4CAF50", linestyle="--",
                               label="Speedometer block out/s (bo)")
                ax_bo.set_ylabel("Block Out/s", color="#4CAF50")
                ax_bo.tick_params(axis="y", labelcolor="#4CAF50")
                ax_bo.spines["right"].set_position(("outward", 60))

                for mt in misses_ts:
                    ax_sys.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                lines1, labs1 = ax_sys.get_legend_handles_labels()
                lines2, labs2 = ax_cs.get_legend_handles_labels()
                lines3, labs3 = ax_bi.get_legend_handles_labels()
                lines4, labs4 = ax_bo.get_legend_handles_labels()
                ax_sys.legend(lines1 + lines2 + lines3 + lines4, labs1 + labs2 + labs3 + labs4,
                              loc="upper right", fontsize=8)

                ax_sys.set_xlabel("Time (s)")
                ax_sys.grid(alpha=0.3)
            else:
                axes[0].set_xlabel("Time (s)")

            fig.tight_layout()
            fname = os.path.join(output_dir, f"timeseries_{scenario}_run{run_num}.png")
            fig.savefig(fname, dpi=150)
            plt.close(fig)
            print(f"  Saved: {fname}")

    # ── Combined comparison: response time overlay across scenarios ────
    fig, ax = plt.subplots(figsize=(14, 6))
    plotted = False
    for scenario in SCENARIO_ORDER:
        if scenario not in ts_data or not ts_data[scenario]:
            continue
        for run_data in ts_data[scenario]:
            c = run_data["cycles"]
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
