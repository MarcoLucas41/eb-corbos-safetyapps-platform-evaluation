#!/usr/bin/env python3
"""
====================================================
DISCLAIMER: THIS SCRIPT USES OUTDATED TERMS:

(OLD)               (NEW)

EXECUTION TIME = TASK TIME

RESPONSE TIME = EXECUTION TIME

THEREFORE, EXECUTION TIME = TASK TIME + JITTER
====================================================

Scenario 1 Analysis — Temporal Task Isolation

Parses run_XX.txt files produced by s1_orchestrator.sh,
prints summary tables, runs formal statistical tests, and generates plots for
every hypothesis in the experiment design.

Usage
-----
# Configuration A only:
  python3 s1_plots.py -r results/2026-05-18_14:00

# Filter to one stressor (recommended for N-trend analysis):
  python3 s1_plots.py -r results/2026-05-18_14:00 --stressor cache

# Configuration A + B cross-variant (H5):
  python3 s1_plots.py -r results/... -r2 results_li/...

Condition directory naming (from s1_orchestrator.sh):
  {stressor}_{n{workers}}   e.g.  baseline_n0  cache_n4  worst-case_n32


"""

import os
import re
import sys
import csv
import argparse
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

DEADLINE_US = 5000.0  # 5 ms deadline in microseconds

# Canonical stressor order (matches experiment matrix S0-S5)
STRESSOR_ORDER = ["baseline", "cpu", "cache", "stream", "io", "worst-case"]
STRESSOR_LABELS = {
    "baseline":   "Baseline",
    "cpu":        "CPU",
    "cache":      "Cache",
    "stream":     "Memory BW",
    "io":         "I/O",
    "worst-case": "Worst-case",
}

STRESSOR_PALETTE = {
    "baseline":   "#607D8B",
    "cpu":        "#2196F3",
    "cache":      "#FF9800",
    "stream":     "#4CAF50",
    "io":         "#9C27B0",
    "worst-case": "#F44336",
}
FALLBACK_COLOR  = "#333333"
VARIANT_A_COLOR = "#F44336"
VARIANT_B_COLOR = "#2196F3"

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
        stats["deadline_misses"]   = int(m.group(1))
        stats["deadline_miss_pct"] = float(m.group(2))

    # Execution time — matches both "Execution time:" and "Execution Time (us):"
    m = re.search(r"Execution [Tt]ime[^:]*:\s*\n\s*Mean:\s+([0-9.]+)", text)
    if m:
        stats["exec_mean_us"] = float(m.group(1))

    m = re.search(r"Execution [Tt]ime[^:]*:.*?Min:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["exec_min_us"] = float(m.group(1))

    m = re.search(r"Execution [Tt]ime[^:]*:.*?Max:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["exec_max_us"] = float(m.group(1))

    # Jitter — matches both "Jitter:" and "Jitter (us):"
    m = re.search(r"Jitter[^:]*:\s*\n\s*Mean:\s+([0-9.]+)", text)
    if m:
        stats["jitter_mean_us"] = float(m.group(1))

    m = re.search(r"Jitter[^:]*:.*?Max:\s+([0-9.]+)", text, re.DOTALL)
    if m:
        stats["jitter_max_us"] = float(m.group(1))

    # Response time — matches both "Response time:" and "Response Time (us):"
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


def discover_conditions(results_dir):
    """
    Scan results_dir for condition subdirectories and parse their run files.

    Expected naming:  {stressor}_{n{workers}}
    e.g.  baseline_n0  cache_n8  worst-case_n32

    Returns
    -------
    dict[name] = {
        'stressor':  str,
        'n_workers': int,
        'runs':      list[dict],
    }
    """
    conditions = {}
    for d in sorted(Path(results_dir).iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name == "plots":
            continue
        name = d.name
        sep  = name.rfind("_")
        if sep != -1 and re.fullmatch(r"n\d+", name[sep + 1:]):
            # Standard format: {stressor}_n{workers}  e.g. cache_n4
            stressor  = name[:sep]
            n_workers = int(name[sep + 1:][1:])
        else:
            # Bare stressor name with no worker suffix  e.g. "baseline"
            stressor  = name
            n_workers = 0

        runs = [parse_run_file(f) for f in sorted(d.glob("run_*.txt"))]
        runs = [r for r in runs if r]
        if runs:
            conditions[name] = {
                "stressor":  stressor,
                "n_workers": n_workers,
                "runs":      runs,
            }

    return conditions

# ─── Condition helpers ────────────────────────────────────────────────────────


def stressor_types(conditions):
    """Sorted list of unique non-baseline stressor names present in conditions."""
    return sorted({v["stressor"] for v in conditions.values()
                   if v["stressor"] != "baseline"})


def for_stressor(conditions, stressor, include_baseline=True):
    """
    Return conditions for one stressor type, sorted by n_workers.
    Prepends baseline_n0 as the N=0 anchor unless stressor == 'baseline'.
    """
    sel = {k: v for k, v in conditions.items() if v["stressor"] == stressor}
    if include_baseline and stressor != "baseline":
        baseline_items = {k: v for k, v in conditions.items()
                          if v["stressor"] == "baseline"}
        if baseline_items:
            sel = {**baseline_items, **sel}
    return dict(sorted(sel.items(), key=lambda x: x[1]["n_workers"]))

# ─── Aggregation helpers ──────────────────────────────────────────────────────


def _vals(runs, key):
    return [r[key] for r in runs if key in r]


def _avg(runs, key):
    v = _vals(runs, key)
    return sum(v) / len(v) if v else 0.0


def _median(runs, key):
    v = sorted(_vals(runs, key))
    if not v:
        return 0.0
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def _std_across_runs(runs, key):
    """Cross-run standard deviation (used as error bars on mean plots)."""
    v = _vals(runs, key)
    if len(v) < 2:
        return 0.0
    mu = sum(v) / len(v)
    return (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5


def _sorted_sc(conditions):
    """Return (n_vals_list, sorted_cond_list) ordered by n_workers."""
    sc = sorted(conditions.values(), key=lambda x: x["n_workers"])
    return [c["n_workers"] for c in sc], sc

# ─── Bootstrap CI helpers ───────────────────────────────────────────────────

N_BOOT    = 10_000   # bootstrap resamples (constant for all CI functions)
CONF_LEVEL = 0.99    # 99% CI — stricter false-positive control for SDV evaluation


def _bootstrap_ci_single(vals, n_boot=N_BOOT, ci=CONF_LEVEL):
    """Bootstrap CI on the median of a single sample (percentile method).

    Returns (ci_lower, ci_upper, point_median).
    Uses stdlib random.choices — no numpy/scipy required.
    """
    import random as _rng
    n = len(vals)
    if n < 2:
        pt = vals[0] if n == 1 else float("nan")
        return float("nan"), float("nan"), pt
    boot = []
    for _ in range(n_boot):
        s = sorted(_rng.choices(vals, k=n))
        m = n // 2
        boot.append(s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0)
    boot.sort()
    alpha = (1.0 - ci) / 2.0
    lo = boot[max(0, int(n_boot * alpha))]
    hi = boot[min(n_boot - 1, int(n_boot * (1.0 - alpha)))]
    s  = sorted(vals)
    m  = n // 2
    pt = s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0
    return lo, hi, pt


def _bootstrap_ci_difference(a_vals, b_vals, n_boot=N_BOOT, ci=CONF_LEVEL):
    """Bootstrap CI on mean(b) - mean(a) for two independent samples.

    Returns (ci_lower, ci_upper, point_estimate).
    CI excludes zero from below (lo > 0) ⇒ b significantly greater than a.
    """
    import random as _rng
    na, nb = len(a_vals), len(b_vals)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    boot = []
    for _ in range(n_boot):
        sa = _rng.choices(a_vals, k=na)
        sb = _rng.choices(b_vals, k=nb)
        boot.append(sum(sb) / nb - sum(sa) / na)
    boot.sort()
    alpha = (1.0 - ci) / 2.0
    lo = boot[max(0, int(n_boot * alpha))]
    hi = boot[min(n_boot - 1, int(n_boot * (1.0 - alpha)))]
    pt = sum(b_vals) / nb - sum(a_vals) / na
    return lo, hi, pt


def _bootstrap_ci_delta_diff(a_stress, a_base, b_stress, b_base,
                              n_boot=N_BOOT, ci=CONF_LEVEL):
    """Bootstrap CI on (ΔWCET_A − ΔWCET_B) from four independent samples.

    ΔWCET_X = mean(stress_X) − mean(base_X).
    Returns (ci_lower, ci_upper, point_estimate).
    CI > 0 means Config A is more affected by the stressor than Config B (H3).
    """
    import random as _rng
    na_s, na_b = len(a_stress), len(a_base)
    nb_s, nb_b = len(b_stress), len(b_base)
    if min(na_s, na_b, nb_s, nb_b) < 2:
        return float("nan"), float("nan"), float("nan")
    boot = []
    for _ in range(n_boot):
        sas = _rng.choices(a_stress, k=na_s)
        sab = _rng.choices(a_base,   k=na_b)
        sbs = _rng.choices(b_stress, k=nb_s)
        sbb = _rng.choices(b_base,   k=nb_b)
        d_a = sum(sas) / na_s - sum(sab) / na_b
        d_b = sum(sbs) / nb_s - sum(sbb) / nb_b
        boot.append(d_a - d_b)
    boot.sort()
    alpha = (1.0 - ci) / 2.0
    lo = boot[max(0, int(n_boot * alpha))]
    hi = boot[min(n_boot - 1, int(n_boot * (1.0 - alpha)))]
    pt = (sum(a_stress) / na_s - sum(a_base) / na_b) - \
         (sum(b_stress) / nb_s - sum(b_base) / nb_b)
    return lo, hi, pt

# ─── Summary Table ────────────────────────────────────────────────────────────


def print_summary_table(conditions, label=""):
    """Print a summary table of all conditions, sorted by stressor type then N."""
    W = 132
    print("\n" + "=" * W)
    print(f"  SCENARIO 1 SUMMARY TABLE{('  -- ' + label) if label else ''}")
    print("=" * W)
    print(
        f"  {'Condition':<22} {'Stressor':<12} {'N':>5} {'Runs':>4} {'Cycles':>7} "
        f"{'Miss%':>7} "
        f"{'Exec Mean':>10} {'Exec Max':>10} "
        f"{'Jit Mean':>10} {'Jit Max':>10} "
        f"{'Resp Mean':>10} {'Resp Max':>10} "
        f"{'Margin%':>8}"
    )
    print("-" * W)

    def _sort_key(item):
        st = item[1]["stressor"]
        return (STRESSOR_ORDER.index(st) if st in STRESSOR_ORDER else 99,
                item[1]["n_workers"])

    for name, cond in sorted(conditions.items(), key=_sort_key):
        runs = cond["runs"]
        n    = len(runs)

        total_cycles = sum(r.get("total_cycles", 0)    for r in runs)
        total_misses = sum(r.get("deadline_misses", 0) for r in runs)
        miss_pct     = (total_misses / total_cycles * 100) if total_cycles > 0 else 0.0

        exec_means    = _vals(runs, "exec_mean_us")
        exec_maxes    = _vals(runs, "exec_max_us")
        jitter_means  = _vals(runs, "jitter_mean_us")
        jitter_maxes  = _vals(runs, "jitter_max_us")
        resp_means    = _vals(runs, "resp_mean_us")
        resp_maxes    = _vals(runs, "resp_max_us")

        avg_exec_mean    = sum(exec_means)   / len(exec_means)   if exec_means   else 0
        worst_exec_max   = max(exec_maxes)                       if exec_maxes   else 0
        avg_jitter_mean  = sum(jitter_means) / len(jitter_means) if jitter_means else 0
        worst_jitter_max = max(jitter_maxes)                     if jitter_maxes else 0
        avg_resp_mean    = sum(resp_means)   / len(resp_means)   if resp_means   else 0
        worst_resp_max   = max(resp_maxes)                       if resp_maxes   else 0

        worst_response_us = (worst_resp_max if resp_maxes
                             else worst_jitter_max + worst_exec_max)
        safety_margin = (DEADLINE_US - worst_response_us) / DEADLINE_US * 100

        lbl = STRESSOR_LABELS.get(cond["stressor"], cond["stressor"])
        print(
            f"  {name:<22} {lbl:<12} {cond['n_workers']:>5} {n:>4} {total_cycles:>7} "
            f"{miss_pct:>6.2f}% "
            f"{avg_exec_mean:>9.1f}us {worst_exec_max:>9.1f}us "
            f"{avg_jitter_mean:>9.1f}us {worst_jitter_max:>9.1f}us "
            f"{avg_resp_mean:>9.1f}us {worst_resp_max:>9.1f}us "
            f"{safety_margin:>7.1f}%"
        )

    print("=" * W)
    print(f"  Deadline: {DEADLINE_US:.0f} us ({DEADLINE_US/1000:.0f} ms).  "
          "Margin% = (deadline - worst_response) / deadline x 100  "
          "(negative = exceeded)")


# ─── Per-Cycle & Monitoring Parsing ───────────────────────────────────────────


# Regex to strip ANSI escape codes and hypervisor serial mux prefixes
# (e.g. "\x1b[37mvm-hi   | " added by L4/Fiasco.OC serial output)
_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_VM_PREFIX_RE = re.compile(r"^vm-\S+\s*\|\s*")


def _clean_serial_line(line):
    """Remove ANSI codes and VM serial mux prefix from a line."""
    line = _ANSI_RE.sub("", line)
    line = _VM_PREFIX_RE.sub("", line)
    return line


def parse_cycles_csv(filepath):
    """Parse a run_XX_cycles.csv file into lists of per-cycle data.

    Handles raw lines that may contain ANSI escape codes and hypervisor
    serial mux prefixes (e.g. from hi VM QEMU serial output).
    """
    rows = []
    try:
        with open(filepath) as f:
            cleaned = (_clean_serial_line(line) for line in f)
            reader  = csv.DictReader(cleaned)
            for row in reader:
                rows.append({
                    "cycle":             int(row["cycle"]),
                    "timestamp_s":       float(row["timestamp_s"]),
                    "execution_time_us": float(row["execution_time_us"]),
                    "jitter_us":         float(row["jitter_us"]),
                    "response_time_us":  float(row["response_time_us"]),
                    "deadline_miss":     int(row["deadline_miss"]),
                })
    except (FileNotFoundError, KeyError, ValueError):
        pass
    return rows


def parse_vmstat(filepath):
    """Parse a vmstat output file into per-second rows.

    Handles repeated headers that vmstat prints every ~20 lines.
    Returns a list of dicts with vmstat columns plus 'timestamp_s'.
    """
    rows   = []
    cols   = None
    second = 0
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Detect column header line (contains us, sy, id)
                if "us" in line and "sy" in line and "id" in line:
                    cols = [c.strip() for c in re.split(r"[,\s]+", line) if c.strip()]
                    continue
                # Skip decoration lines (e.g. "procs ---memory--- ...")
                if cols is None or not line[0].isdigit():
                    continue
                vals = [v.strip() for v in re.split(r"[,\s]+", line) if v.strip()]
                if len(vals) != len(cols):
                    continue
                try:
                    entry = {c: int(v) for c, v in zip(cols, vals)}
                    entry["timestamp_s"] = float(second)
                    rows.append(entry)
                    second += 1
                except ValueError:
                    print(f"WARNING: Skipping malformed vmstat line: {line}")
                    continue
    except FileNotFoundError:
        print(f"WARNING: vmstat file not found: {filepath}")
        pass
    return rows


def load_timeseries(results_dir):
    """Load per-cycle and vmstat data for every run in every condition.

    Returns {condition_name: [(cycles_rows, vmstat_rows, speedom_vmstat_rows, run_num), ...]}.
    Speedometer vmstat (run_XX_speedometer_vmstat.csv) is loaded when present;
    an empty list is stored for runs where the file is absent.
    """
    ts_data = {}
    for d in sorted(Path(results_dir).iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name == "plots":
            continue
        name = d.name
        sep  = name.rfind("_")
        # Accept both "baseline" (no suffix) and "cache_n4" (standard) formats
        if sep != -1 and re.fullmatch(r"n\d+", name[sep + 1:]):
            pass  # standard format
        elif sep != -1:
            continue  # has an underscore but not _n\d+ — skip
        # else: bare name like "baseline" — accept with n_workers=0

        run_entries = []
        run_idx = 1
        while True:
            run_num   = f"{run_idx:02d}"
            run_file  = d / f"run_{run_num}.txt"
            if not run_file.exists():
                break
            cycles_file    = d / f"run_{run_num}.csv"
            vmstat_file    = d / f"run_{run_num}_vmstat.csv"
            speedom_file   = d / f"run_{run_num}_speedometer_vmstat.csv"
            cycles         = parse_cycles_csv(cycles_file) if cycles_file.exists() else []
            vmstat         = parse_vmstat(vmstat_file)     if vmstat_file.exists() else []
            speedom_vmstat = parse_vmstat(speedom_file)    if speedom_file.exists() else []
            run_entries.append((cycles, vmstat, speedom_vmstat, run_num))
            run_idx += 1

        if run_entries:
            ts_data[name] = run_entries

    return ts_data


# ─── Plotting ─────────────────────────────────────────────────────────────────


def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"  Saved: {path}")


def generate_plots(conditions, output_dir):
    """Generate all aggregate plots and save to output_dir.

    Runs from each stressor type are pooled across all N levels so that
    one bar/box corresponds to one scenario (S0-S5) as per the design.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping plots.")
        print("Install with: pip3 install matplotlib")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Pool all runs per stressor type across N levels
    stressor_runs = {}
    for cond in conditions.values():
        stressor_runs.setdefault(cond["stressor"], []).extend(cond["runs"])

    ordered = [st for st in STRESSOR_ORDER if st in stressor_runs]
    labels  = [STRESSOR_LABELS.get(st, st) for st in ordered]
    colors  = [STRESSOR_PALETTE.get(st, FALLBACK_COLOR) for st in ordered]

    # ── Plot 1: Box plot of max jitter per scenario ──────────────────────
    jitter_max_data = [_vals(stressor_runs[st], "jitter_max_us") for st in ordered]

    fig, ax = plt.subplots(figsize=(10, 6))
    bp = ax.boxplot(jitter_max_data, tick_labels=labels, patch_artist=True)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_ylabel("Max Jitter (us)")
    ax.set_title("Maximum Wake-up Jitter per Scenario (across all runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(output_dir, "jitter_max_boxplot.png"))
    plt.close(fig)

    # ── Plot 2: Bar chart of mean execution time ─────────────────────────
    exec_mean_avgs = [_avg(stressor_runs[st], "exec_mean_us")             for st in ordered]
    exec_mean_stds = [_std_across_runs(stressor_runs[st], "exec_mean_us") for st in ordered]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(labels, exec_mean_avgs, yerr=exec_mean_stds, capsize=5,
           color=colors, alpha=0.7, edgecolor="black")
    ax.set_ylabel("Mean Execution Time (us)")
    ax.set_title("Mean Execution Time per Scenario (avg +/- std across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(output_dir, "exec_time_bar.png"))
    plt.close(fig)

    # ── Plot 3: Deadline miss rate bar chart ──────────────────────────────
    miss_rates = []
    for st in ordered:
        runs         = stressor_runs[st]
        total_cycles = sum(r.get("total_cycles", 0)    for r in runs)
        total_misses = sum(r.get("deadline_misses", 0) for r in runs)
        miss_rates.append(total_misses / total_cycles * 100 if total_cycles else 0.0)

    fig, ax = plt.subplots(figsize=(10, 6))
    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in miss_rates]
    ax.bar(labels, miss_rates, color=bar_colors, alpha=0.7, edgecolor="black")
    ax.set_ylabel("Deadline Miss Rate (%)")
    ax.set_title(f"Deadline Miss Rate per Scenario (deadline = {DEADLINE_US/1000:.0f} ms)")
    ax.grid(axis="y", alpha=0.3)
    if max(miss_rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No deadline misses in any scenario",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=14, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(output_dir, "deadline_miss_rate.png"))
    plt.close(fig)

    # ── Plot 4: Safety margin bar chart ───────────────────────────────────
    margins = []
    for st in ordered:
        runs         = stressor_runs[st]
        resp_maxes   = _vals(runs, "resp_max_us")
        jitter_maxes = _vals(runs, "jitter_max_us")
        exec_maxes   = _vals(runs, "exec_max_us")
        if resp_maxes:
            worst_response = max(resp_maxes)
        else:
            worst_response = max(jitter_maxes, default=0) + max(exec_maxes, default=0)
        margins.append((DEADLINE_US - worst_response) / DEADLINE_US * 100)

    fig, ax = plt.subplots(figsize=(10, 6))
    margin_colors = ["#4CAF50" if m > 0 else "#F44336" for m in margins]
    ax.bar(labels, margins, color=margin_colors, alpha=0.7, edgecolor="black")
    ax.axhline(y=0, color="red", linestyle="--", linewidth=1, label="Deadline boundary")
    ax.set_ylabel("Safety Margin (%)")
    ax.set_title("Safety Margin per Scenario (positive = within deadline)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    _save(fig, os.path.join(output_dir, "safety_margin.png"))
    plt.close(fig)

    # ── Plot 5: Mean jitter bar chart ─────────────────────────────────────
    jitter_mean_avgs = [_avg(stressor_runs[st], "jitter_mean_us")             for st in ordered]
    jitter_mean_stds = [_std_across_runs(stressor_runs[st], "jitter_mean_us") for st in ordered]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(labels, jitter_mean_avgs, yerr=jitter_mean_stds, capsize=5,
           color=colors, alpha=0.7, edgecolor="black")
    ax.set_ylabel("Mean Jitter (us)")
    ax.set_title("Mean Wake-up Jitter per Scenario (avg +/- std across runs)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(output_dir, "jitter_mean_bar.png"))
    plt.close(fig)

    # ── Plot 6: Box plot of max response time per scenario ────────────────
    resp_max_data = [_vals(stressor_runs[st], "resp_max_us") for st in ordered]
    if any(resp_max_data):
        fig, ax = plt.subplots(figsize=(10, 6))
        bp = ax.boxplot(resp_max_data, tick_labels=labels, patch_artist=True)
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                   label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
        ax.set_ylabel("Max Execution Time (us)")
        ax.set_title("Maximum Execution Time per Scenario (across all runs)")
        ax.grid(axis="y", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        _save(fig, os.path.join(output_dir, "response_time_max_boxplot.png"))
        plt.close(fig)

    # ── Plot 7: Bar chart of mean response time ──────────────────────────
    resp_mean_avgs = [_avg(stressor_runs[st], "resp_mean_us")             for st in ordered]
    resp_mean_stds = [_std_across_runs(stressor_runs[st], "resp_mean_us") for st in ordered]

    if any(v > 0 for v in resp_mean_avgs):
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.bar(labels, resp_mean_avgs, yerr=resp_mean_stds, capsize=5,
               color=colors, alpha=0.7, edgecolor="black")
        ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                   label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
        ax.set_ylabel("Mean Execution Time (us)")
        ax.set_title("Mean Execution Time per Scenario (avg +/- std across runs)")
        ax.grid(axis="y", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        _save(fig, os.path.join(output_dir, "response_time_mean_bar.png"))
        plt.close(fig)


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

    for cond_name, run_entries in sorted(ts_data.items()):
        sep      = cond_name.rfind("_")
        stressor = cond_name[:sep] if sep != -1 else cond_name
        lbl      = STRESSOR_LABELS.get(stressor, stressor)
        color    = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

        for cycles, vmstat, speedom_vmstat, run_num in run_entries:
            if not cycles:
                continue

            has_vmstat  = bool(vmstat)
            has_speedom = bool(speedom_vmstat)
            nrows = 1 + (2 if has_vmstat else 0) + (2 if has_speedom else 0)
            fig, axes = plt.subplots(nrows, 1, figsize=(14, 4 * nrows), sharex=True)
            if nrows == 1:
                axes = [axes]

            # ── Top: Response time timeline ────────────────────────────
            ax_rt     = axes[0]
            ts_vals   = [r["timestamp_s"]      for r in cycles]
            rt_vals   = [r["response_time_us"] for r in cycles]
            misses_ts = [r["timestamp_s"]      for r in cycles if r["deadline_miss"]]
            misses_rt = [r["response_time_us"] for r in cycles if r["deadline_miss"]]

            ax_rt.plot(ts_vals, rt_vals, linewidth=0.8, color=color,
                       label="Response time")
            ax_rt.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                          label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
            if misses_ts:
                ax_rt.scatter(misses_ts, misses_rt, color="red", s=30, zorder=5,
                              label=f"Deadline miss ({len(misses_ts)})")
            ax_rt.set_ylabel("Execution Time (us)")
            ax_rt.set_title(f"{cond_name} -- Run {run_num}")
            ax_rt.legend(loc="upper right", fontsize=8)
            ax_rt.grid(alpha=0.3)

            if has_vmstat:
                vm_ts = [r["timestamp_s"] for r in vmstat]

                # ── Middle: CPU breakdown (us, sy, id) ─────────────────
                ax_cpu  = axes[1]
                cpu_us  = [r.get("us", 0) for r in vmstat]
                cpu_sy  = [r.get("sy", 0) for r in vmstat]
                cpu_id  = [r.get("id", 0) for r in vmstat]
                ax_cpu.stackplot(vm_ts, cpu_us, cpu_sy, cpu_id,
                                 labels=["User (us)", "System (sy)", "Idle (id)"],
                                 colors=["#FF9800", "#F44336", "#E0E0E0"], alpha=0.8)
                ax_cpu.set_ylabel("vm-li CPU %")
                ax_cpu.set_ylim(0, 105)
                ax_cpu.legend(loc="upper right", fontsize=8)
                ax_cpu.grid(alpha=0.3)
                for mt in misses_ts:
                    ax_cpu.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                # ── Bottom: System metrics (in, cs) ───────────────────
                ax_sys       = axes[2]
                interrupts   = [r.get("in", 0) for r in vmstat]
                ctx_switches = [r.get("cs", 0) for r in vmstat]

                ax_sys.plot(vm_ts, interrupts, linewidth=1, color="#9C27B0",
                            label="Interrupts/s (in)")
                ax_sys.set_ylabel("Interrupts/s", color="#9C27B0")
                ax_sys.tick_params(axis="y", labelcolor="#9C27B0")

                ax_cs = ax_sys.twinx()
                ax_cs.plot(vm_ts, ctx_switches, linewidth=1, color="#2196F3",
                           label="Ctx Switches/s (cs)")
                ax_cs.set_ylabel("Ctx Switches/s", color="#2196F3")
                ax_cs.tick_params(axis="y", labelcolor="#2196F3")

                for mt in misses_ts:
                    ax_sys.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                lines1, labs1 = ax_sys.get_legend_handles_labels()
                lines2, labs2 = ax_cs.get_legend_handles_labels()
                ax_sys.legend(lines1 + lines2, labs1 + labs2,
                              loc="upper right", fontsize=8)
                if not has_speedom:
                    ax_sys.set_xlabel("Time (s)")
                ax_sys.grid(alpha=0.3)
            elif not has_speedom:
                axes[0].set_xlabel("Time (s)")

            if has_speedom:
                row_offset = 3 if has_vmstat else 1
                sp_ts      = [r["timestamp_s"] for r in speedom_vmstat]

                # ── vm-hi CPU breakdown ────────────────────────────────
                ax_sp_cpu = axes[row_offset]
                sp_us     = [r.get("us", 0) for r in speedom_vmstat]
                sp_sy     = [r.get("sy", 0) for r in speedom_vmstat]
                sp_id     = [r.get("id", 0) for r in speedom_vmstat]
                ax_sp_cpu.stackplot(sp_ts, sp_us, sp_sy, sp_id,
                                    labels=["User (us)", "System (sy)", "Idle (id)"],
                                    colors=["#FF9800", "#F44336", "#E0E0E0"], alpha=0.8)
                ax_sp_cpu.set_ylabel("vm-hi CPU %")
                ax_sp_cpu.set_ylim(0, 105)
                ax_sp_cpu.legend(loc="upper right", fontsize=8)
                ax_sp_cpu.grid(alpha=0.3)
                for mt in misses_ts:
                    ax_sp_cpu.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                # ── vm-hi System metrics (in, cs) ──────────────────────
                ax_sp_sys       = axes[row_offset + 1]
                sp_interrupts   = [r.get("in", 0) for r in speedom_vmstat]
                sp_ctx_switches = [r.get("cs", 0) for r in speedom_vmstat]

                ax_sp_sys.plot(sp_ts, sp_interrupts, linewidth=1, color="#9C27B0",
                               label="Interrupts/s (in)")
                ax_sp_sys.set_ylabel("vm-hi Interrupts/s", color="#9C27B0")
                ax_sp_sys.tick_params(axis="y", labelcolor="#9C27B0")

                ax_sp_cs = ax_sp_sys.twinx()
                ax_sp_cs.plot(sp_ts, sp_ctx_switches, linewidth=1, color="#2196F3",
                              label="Ctx Switches/s (cs)")
                ax_sp_cs.set_ylabel("vm-hi Ctx Switches/s", color="#2196F3")
                ax_sp_cs.tick_params(axis="y", labelcolor="#2196F3")

                for mt in misses_ts:
                    ax_sp_sys.axvline(x=mt, color="red", alpha=0.3, linewidth=0.7)

                lines1, labs1 = ax_sp_sys.get_legend_handles_labels()
                lines2, labs2 = ax_sp_cs.get_legend_handles_labels()
                ax_sp_sys.legend(lines1 + lines2, labs1 + labs2,
                                 loc="upper right", fontsize=8)
                ax_sp_sys.set_xlabel("Time (s)")
                ax_sp_sys.grid(alpha=0.3)

            fig.tight_layout()
            fname = os.path.join(output_dir, f"timeseries_{cond_name}_run{run_num}.png")
            fig.savefig(fname, dpi=150)
            plt.close(fig)
            print(f"  Saved: {fname}")

    # ── Combined comparison: one representative run per stressor type ──
    fig, ax = plt.subplots(figsize=(14, 6))
    plotted        = False
    stressor_shown = set()

    def _ts_sort_key(item):
        cname = item[0]
        sep   = cname.rfind("_")
        st    = cname[:sep] if sep != -1 else cname
        return (STRESSOR_ORDER.index(st) if st in STRESSOR_ORDER else 99, cname)

    for cond_name, run_entries in sorted(ts_data.items(), key=_ts_sort_key):
        sep      = cond_name.rfind("_")
        stressor = cond_name[:sep] if sep != -1 else cond_name
        if stressor in stressor_shown:
            continue
        for cycles, _, _, _ in run_entries:
            if cycles:
                ts_vals = [r["timestamp_s"]      for r in cycles]
                rt_vals = [r["response_time_us"] for r in cycles]
                color   = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)
                ax.plot(ts_vals, rt_vals, linewidth=0.8, alpha=0.7, color=color,
                        label=STRESSOR_LABELS.get(stressor, stressor))
                stressor_shown.add(stressor)
                plotted = True
                break

    if plotted:
        ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
                   label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Execution Time (us)")
        ax.set_title("Execution Time Comparison Across Scenarios (first run per stressor type)")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fname = os.path.join(output_dir, "timeseries_comparison.png")
        fig.savefig(fname, dpi=150)
        print(f"  Saved: {fname}")
    plt.close(fig)


# ─── Per-Stressor N-Trend Plots ──────────────────────────────────────────────────────


def plot_mean_response(conditions, out, stressor, suffix=""):
    """Mean response time +/- cross-run std vs N for one stressor type."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    means = [_avg(c["runs"], "resp_mean_us")             for c in sc]
    errs  = [_std_across_runs(c["runs"], "resp_mean_us") for c in sc]
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=errs, fmt="o-", capsize=5,
                color=color, linewidth=2, markersize=7,
                label="mean ± cross-run std")
    ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
               label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean Execution Time (µs)")
    ax.set_title(f"Mean Execution Time vs Load — {stressor}")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"response_mean{suffix}.png"))
    plt.close(fig)


def plot_jitter_trend(conditions, out, stressor, suffix=""):
    """Mean wake-up jitter vs N for one stressor type."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels  = [str(n) for n in n_vals]
    jitters = [_avg(c["runs"], "jitter_mean_us") for c in sc]
    color   = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, jitters, color=color, alpha=0.8, edgecolor="black")
    if jitters and jitters[0] > 0:
        thr = jitters[0] * 2.0
        ax.axhline(thr, linestyle="--", color="red", alpha=0.7,
                   label=f"2x baseline ({thr:.1f} µs)")
        ax.legend()
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean Jitter (µs)")
    ax.set_title(f"Mean Wake-up Jitter vs Load — {stressor}")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"jitter_trend{suffix}.png"))
    plt.close(fig)


def plot_miss_rate_trend(conditions, out, stressor, suffix=""):
    """Deadline miss rate vs N for one stressor type."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels = [str(n) for n in n_vals]
    rates  = []
    for c in sc:
        total_cycles = sum(r.get("total_cycles", 0)    for r in c["runs"])
        total_misses = sum(r.get("deadline_misses", 0) for r in c["runs"])
        rates.append(total_misses / total_cycles * 100 if total_cycles else 0.0)

    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in rates]
    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, rates, color=bar_colors, alpha=0.85, edgecolor="black")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Deadline Miss Rate (%)")
    ax.set_title(f"Deadline Miss Rate vs Load — {stressor}")
    ax.grid(axis="y", alpha=0.3)
    if max(rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No deadline misses detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"miss_rate_trend{suffix}.png"))
    plt.close(fig)


def plot_boxplot_response(conditions, out, stressor, suffix=""):
    """Box plot of per-run mean response time per N condition."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels   = [str(n) for n in n_vals]
    box_data = [_vals(c["runs"], "resp_mean_us") for c in sc]
    colors   = (["#607D8B"] +
                [STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)] * (len(sc) - 1))

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    bp = ax.boxplot(box_data, tick_labels=labels, patch_artist=True)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c); patch.set_alpha(0.7)
    ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
               label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean Execution Time (µs)")
    ax.set_title(f"Execution Time Distribution per Condition — {stressor}  (run-to-run variance)")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"response_boxplot{suffix}.png"))
    plt.close(fig)


def plot_wcet_trend(conditions, out, stressor, suffix=""):
    """Empirical WCET (max response time per run) vs N with bootstrap 99% CI.

    Primary H0/H1 plot: each point is the median WCET across runs at that N
    level; error bars are the bootstrap percentile 99% CI on the median.
    """
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    medians, ci_los, ci_his = [], [], []
    for c in sc:
        wcet_vals = _vals(c["runs"], "resp_max_us")
        if len(wcet_vals) >= 2:
            lo, hi, med = _bootstrap_ci_single(wcet_vals)
        else:
            med = wcet_vals[0] if wcet_vals else float("nan")
            lo, hi = med, med
        medians.append(med)
        ci_los.append(med - lo)
        ci_his.append(hi - med)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, medians, yerr=[ci_los, ci_his],
                fmt="o-", capsize=5, color=color, linewidth=2, markersize=7,
    label=f"median WCET  ±  {CONF_LEVEL*100:.0f}% bootstrap CI")
    ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
               label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Empirical WCET (µs)")
    ax.set_title(f"Empirical WCET vs Load — {stressor}  (H0/H1)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"wcet_trend{suffix}.png"))
    plt.close(fig)


# ─── Statistical Tests ────────────────────────────────────────────────────────────


def run_statistical_tests(conditions, label=""):
    """Hypothesis tests using empirical WCET and bootstrap 99% CIs.

    Operates on all conditions at once (all stressor types and N levels pooled).
    Metric: resp_max_us per run = empirical WCET estimate.

    H0: WCET distributions are identical across stressor types
        → bootstrap 99% CI on median WCET; non-overlapping CIs reject H0.
    H1: At least one stressor causes a significant increase in WCET vs baseline
        → bootstrap 99% CI on ΔWCET; CI excluding zero ⇒ significant.
    H2: Cache/Memory ΔWCET > CPU/IO ΔWCET
        → bootstrap 99% CI on (ΔWCET_cache/mem − ΔWCET_cpu/io); CI > 0 ⇒ supported.
    Compliance check: worst empirical WCET vs 5 ms deadline.
    """
    # Pool runs per stressor type across all N levels
    stressor_runs = {}
    for cond in conditions.values():
        stressor_runs.setdefault(cond["stressor"], []).extend(cond["runs"])

    baseline_runs  = stressor_runs.get("baseline", [])
    baseline_wcet  = _vals(baseline_runs, "resp_max_us")
    stressed_types = sorted(st for st in stressor_runs if st != "baseline")

    print("\n" + "=" * 80)
    print(f"STATISTICAL HYPOTHESIS TESTS  [Empirical WCET + Bootstrap {CONF_LEVEL*100:.0f}% CI]"
          f"{('  -- ' + label) if label else ''}")
    print(f"  Metric : resp_max_us per run  (empirical WCET estimate)")
    print(f"  Method : percentile bootstrap  (N_boot = {N_BOOT:,},  CI = {CONF_LEVEL*100:.0f}%)")
    print("=" * 80)

    # ── H0: Bootstrap CI on median WCET per stressor type ────────────────
    print("\n--- H0: WCET distributions across stressor types ---")
    print(f"  {'Stressor':<16} {'Runs':>5}  {'Median WCET':>13}  {f'{CONF_LEVEL*100:.0f}% CI':>26}  Overlaps baseline?")
    print(f"  {'-' * 78}")

    bl_ci = None
    wcet_cis = {}
    for st in ["baseline"] + stressed_types:
        runs      = stressor_runs.get(st, [])
        wcet_vals = _vals(runs, "resp_max_us")
        lbl       = STRESSOR_LABELS.get(st, st)
        if len(wcet_vals) < 2:
            print(f"  {lbl:<16} {'':>5}  insufficient data")
            continue
        lo, hi, med = _bootstrap_ci_single(wcet_vals)
        wcet_cis[st] = (lo, hi, med)
        if st == "baseline":
            bl_ci  = (lo, hi, med)
            status = "  (reference)"
        elif bl_ci:
            overlaps = not (lo > bl_ci[1] or hi < bl_ci[0])
            status   = "  ✓ overlaps" if overlaps else "  ✗ no overlap  *"
        else:
            status = ""
        print(f"  {lbl:<16} {len(wcet_vals):>5}  {med:>10.1f} µs    "
              f"[{lo:>8.1f},  {hi:>8.1f}] µs  {status}")

    if bl_ci:
        rejected = [STRESSOR_LABELS.get(st, st) for st in stressed_types
                    if st in wcet_cis and
                    (wcet_cis[st][0] > bl_ci[1] or wcet_cis[st][1] < bl_ci[0])]
        print()
        if rejected:
            print(f"  H0 REJECTED: non-overlapping CI vs baseline → {', '.join(rejected)}")
        else:
            print("  H0 NOT REJECTED: all stressor WCET CIs overlap with baseline")

    # ── H1: Bootstrap CI on ΔWCET per stressor ──────────────────────────
    print(f"\n--- H1: ΔWCET vs baseline per stressor  (bootstrap {CONF_LEVEL*100:.0f}% CI) ---")
    print(f"  Significant (*) if CI excludes zero (lo > 0)\n")
    print(f"  {'Stressor':<16} {'ΔWCET':>11}  {f'{CONF_LEVEL*100:.0f}% CI':>28}  Sig")
    print(f"  {'-' * 62}")

    for st in stressed_types:
        wcet_st = _vals(stressor_runs[st], "resp_max_us")
        lbl     = STRESSOR_LABELS.get(st, st)
        if len(baseline_wcet) < 2 or len(wcet_st) < 2:
            print(f"  {lbl:<16} insufficient data")
            continue
        lo, hi, delta = _bootstrap_ci_difference(baseline_wcet, wcet_st)
        sig = "*" if lo > 0 else ("" if hi > 0 else "✗ negative")
        print(f"  {lbl:<16} {delta:>+10.1f}µs   [{lo:>+10.1f},  {hi:>+10.1f}]µs  {sig}")

    # ── H2: Cache/Memory ΔWCET vs CPU/IO ΔWCET ─────────────────────────
    print("\n--- H2: Cache/Memory ΔWCET vs CPU/IO ΔWCET ---")
    print("  Test: bootstrap 99% CI on (ΔWCET_cache/mem − ΔWCET_cpu/io)")
    print("  H2 SUPPORTED if CI excludes zero and is positive\n")

    cm_runs = [r for st in ["cache", "stream"] if st in stressor_runs
               for r in stressor_runs[st]]
    ci_runs = [r for st in ["cpu", "io"] if st in stressor_runs
               for r in stressor_runs[st]]
    cm_wcet = _vals(cm_runs, "resp_max_us")
    ci_wcet = _vals(ci_runs, "resp_max_us")

    if len(cm_wcet) >= 2 and len(ci_wcet) >= 2 and len(baseline_wcet) >= 2:
        lo_cm, hi_cm, d_cm = _bootstrap_ci_difference(baseline_wcet, cm_wcet)
        lo_ci, hi_ci, d_ci = _bootstrap_ci_difference(baseline_wcet, ci_wcet)
        lo,    hi,    diff = _bootstrap_ci_difference(ci_wcet, cm_wcet)
        print(f"  ΔWCET Cache/Memory : {d_cm:>+.1f}µs   [{lo_cm:>+.1f},  {hi_cm:>+.1f}]")
        print(f"  ΔWCET CPU/IO       : {d_ci:>+.1f}µs   [{lo_ci:>+.1f},  {hi_ci:>+.1f}]")
        print(f"  Difference         : {diff:>+.1f}µs   [{lo:>+.1f},  {hi:>+.1f}]")
        if lo > 0:
            print("  H2 SUPPORTED: cache/memory stressors cause significantly larger WCET increase.")
        elif hi < 0:
            print("  H2 NOT SUPPORTED: CPU/IO stressors cause larger WCET increase.")
        else:
            print("  H2 INCONCLUSIVE: CI includes zero.")
    else:
        print("  Insufficient data for one or both stressor groups.")

    # ── Compliance check: empirical WCET vs deadline ──────────────────────
    print("\n--- Compliance check: empirical WCET vs deadline ---")
    all_maxes = [(cname, max(_vals(cond["runs"], "resp_max_us"), default=0.0))
                 for cname, cond in conditions.items()]
    if all_maxes:
        worst_cond, worst_val = max(all_maxes, key=lambda x: x[1])
        margin = (DEADLINE_US - worst_val) / DEADLINE_US * 100
        print(f"  Worst empirical WCET : {worst_val:.1f} µs  ({worst_cond})")
        print(f"  Deadline             : {DEADLINE_US:.0f} µs  ({DEADLINE_US/1000:.0f} ms)")
        print(f"  Safety margin        : {margin:.1f}%")
        if worst_val <= DEADLINE_US:
            print("  All observed response times within deadline.")
        else:
            print(f"  DEADLINE EXCEEDED by {worst_val - DEADLINE_US:.1f} µs.")

    print("=" * 80)


# ─── Cross-Variant Per-Stressor Plots ────────────────────────────────────────────────────


def plot_cv_overlay(conditions_a, conditions_b, out, stressor):
    """Median empirical WCET + bootstrap 99% CI for both configs vs N  (H3).

    Replaces the old mean+std version; WCET is the primary H3 metric.
    """
    import matplotlib.pyplot as plt

    def _extract(conds):
        sc = sorted(conds.values(), key=lambda x: x["n_workers"])
        ns, meds, los, his = [], [], [], []
        for c in sc:
            wcet_vals = _vals(c["runs"], "resp_max_us")
            if len(wcet_vals) >= 2:
                lo, hi, med = _bootstrap_ci_single(wcet_vals)
            else:
                med = wcet_vals[0] if wcet_vals else float("nan")
                lo, hi = med, med
            ns.append(c["n_workers"])
            meds.append(med)
            los.append(med - lo)
            his.append(hi - med)
        return ns, meds, los, his

    a_n, a_m, a_lo, a_hi = _extract(conditions_a)
    b_n, b_m, b_lo, b_hi = _extract(conditions_b)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(a_n, a_m, yerr=[a_lo, a_hi], fmt="o-",  capsize=5,
                color=VARIANT_A_COLOR, linewidth=2, markersize=7,
                label=f"Configuration A  (median WCET ± {CONF_LEVEL*100:.0f}% CI)")
    ax.errorbar(b_n, b_m, yerr=[b_lo, b_hi], fmt="s--", capsize=5,
                color=VARIANT_B_COLOR, linewidth=2, markersize=7,
                label=f"Configuration B  (median WCET ± {CONF_LEVEL*100:.0f}% CI)")
    ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1,
               label=f"Deadline ({DEADLINE_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Empirical WCET (µs)")
    ax.set_title(f"Cross-Variant WCET Comparison — {stressor}  (H3)\n"
                 f"Vertical gap = isolation benefit  |  error bars = bootstrap {CONF_LEVEL*100:.0f}% CI")
    ax.set_xticks(sorted(set(a_n) | set(b_n)))
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"cv_overlay_{stressor}.png"))
    plt.close(fig)


def plot_cv_ratio(conditions_a, conditions_b, out, stressor):
    """Response time ratio (Config B / Config A) vs N — isolation benefit."""
    import matplotlib.pyplot as plt

    a_by_n   = {v["n_workers"]: v for v in conditions_a.values()}
    b_by_n   = {v["n_workers"]: v for v in conditions_b.values()}
    common_n = sorted(set(a_by_n) & set(b_by_n))

    ratios = []
    for n in common_n:
        a_m = _avg(a_by_n[n]["runs"], "resp_mean_us")
        b_m = _avg(b_by_n[n]["runs"], "resp_mean_us")
        ratios.append(b_m / a_m if a_m > 0 else 0.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(common_n, ratios, "o-", color="#9C27B0", linewidth=2, markersize=7)
    ax.axhline(1.0, linestyle="--", color="grey", alpha=0.5,
               label="ratio = 1.0  (no isolation benefit)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Execution Time Ratio  (Config B / Config A)")
    ax.set_title(f"Isolation Benefit — {stressor}\n"
                 "Ratio > 1: Config B is slower — partitioning reduces response time")
    ax.set_xticks(common_n); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"cv_ratio_{stressor}.png"))
    plt.close(fig)


def plot_cv_side_by_side_boxplot(conditions_a, conditions_b, out, stressor):
    """Side-by-side box plots of per-run mean response time per N."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping side-by-side boxplot]")
        return
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D

    a_by_n   = {v["n_workers"]: v for v in conditions_a.values()}
    b_by_n   = {v["n_workers"]: v for v in conditions_b.values()}
    common_n = sorted(set(a_by_n) & set(b_by_n))

    pos    = np.arange(len(common_n))
    width  = 0.35
    a_data = [_vals(a_by_n[n]["runs"], "resp_mean_us") for n in common_n]
    b_data = [_vals(b_by_n[n]["runs"], "resp_mean_us") for n in common_n]

    fig, ax = plt.subplots(figsize=(max(9, len(common_n) * 1.6), 5))
    bp1 = ax.boxplot(a_data, positions=pos - width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)
    bp2 = ax.boxplot(b_data, positions=pos + width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)
    for p in bp1["boxes"]:
        p.set_facecolor(VARIANT_A_COLOR); p.set_alpha(0.6)
    for p in bp2["boxes"]:
        p.set_facecolor(VARIANT_B_COLOR); p.set_alpha(0.6)

    ax.axhline(y=DEADLINE_US, color="red", linestyle="--", linewidth=1)
    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    # ax.set_ylabel("Per-Run Mean Response Time (µs)")
    ax.set_ylabel("Per-Run Mean Execution Time (µs)")
    # ax.set_title(f"Response Time Distribution Comparison — {stressor}")
    ax.set_title(f"Execution Time Distribution Comparison — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=VARIANT_A_COLOR, alpha=0.6, label="Configuration A"),
        Patch(facecolor=VARIANT_B_COLOR, alpha=0.6, label="Configuration B"),
        Line2D([0], [0], color="red", linestyle="--", linewidth=1,
               label=f"Deadline ({DEADLINE_US/1000:.0f} ms)"),
    ])
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"cv_boxplot_{stressor}.png"))
    plt.close(fig)


def plot_cv_miss_rate_comparison(conditions_a, conditions_b, out, stressor):
    """Grouped bar chart of deadline miss rates for both configurations."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping miss rate comparison]")
        return
    from matplotlib.patches import Patch

    a_by_n   = {v["n_workers"]: v for v in conditions_a.values()}
    b_by_n   = {v["n_workers"]: v for v in conditions_b.values()}
    common_n = sorted(set(a_by_n) & set(b_by_n))

    def _miss_pct(cond):
        total_cycles = sum(r.get("total_cycles", 0)    for r in cond["runs"])
        total_misses = sum(r.get("deadline_misses", 0) for r in cond["runs"])
        return total_misses / total_cycles * 100 if total_cycles else 0.0

    a_rates = [_miss_pct(a_by_n[n]) for n in common_n]
    b_rates = [_miss_pct(b_by_n[n]) for n in common_n]

    pos   = np.arange(len(common_n))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(7, len(common_n) * 1.6), 5))
    ax.bar(pos - width / 2, a_rates, width, color=VARIANT_A_COLOR, alpha=0.8,
           edgecolor="black")
    ax.bar(pos + width / 2, b_rates, width, color=VARIANT_B_COLOR, alpha=0.8,
           edgecolor="black")
    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Deadline Miss Rate (%)")
    ax.set_title(f"Deadline Miss Rate Comparison — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=VARIANT_A_COLOR, alpha=0.8, label="Configuration A"),
        Patch(facecolor=VARIANT_B_COLOR, alpha=0.8, label="Configuration B"),
    ])
    ax.grid(axis="y", alpha=0.3)
    if max(a_rates + b_rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No deadline misses detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"cv_miss_rate_{stressor}.png"))
    plt.close(fig)


def test_cross_variant_stats(conditions_a, conditions_b, stressor):
    """H3: ΔWCET caused by stressor is significantly greater in Config A
    (colocated, no isolation) than in Config B (isolated).

    Uses _bootstrap_ci_delta_diff on four independent WCET sample pools:
      ΔWCET_A = mean(WCET_A_stress) − mean(WCET_A_baseline)
      ΔWCET_B = mean(WCET_B_stress) − mean(WCET_B_baseline)
    H3 confirmed if 99% CI on (ΔWCET_A − ΔWCET_B) excludes zero (is positive).

    Also reports a direct WCET level comparison (bootstrap CI on WCET_A − WCET_B)
    to show the absolute isolation benefit at each N level.
    """
    sep = "─" * 72
    print(f"\n{sep}")
    print(f"  Cross-Variant Statistical Tests (H3)  [Bootstrap 99% CI]  —  stressor: {stressor}")
    print(sep)

    # Separate stressor and baseline runs for each config
    a_stress_runs = [r for cond in conditions_a.values()
                     if cond["stressor"] != "baseline" for r in cond["runs"]]
    a_base_runs   = [r for cond in conditions_a.values()
                     if cond["stressor"] == "baseline" for r in cond["runs"]]
    b_stress_runs = [r for cond in conditions_b.values()
                     if cond["stressor"] != "baseline" for r in cond["runs"]]
    b_base_runs   = [r for cond in conditions_b.values()
                     if cond["stressor"] == "baseline" for r in cond["runs"]]

    wcet_a_st = _vals(a_stress_runs, "resp_max_us")
    wcet_a_bl = _vals(a_base_runs,   "resp_max_us")
    wcet_b_st = _vals(b_stress_runs, "resp_max_us")
    wcet_b_bl = _vals(b_base_runs,   "resp_max_us")

    result = {}

    # ─ H3: ΔWCET_A − ΔWCET_B ───────────────────────────────────────────────
    print("\n  H3: ΔWCET_A − ΔWCET_B  (bootstrap 99% CI)")
    print("  H3 confirmed if CI > 0  (Config A more affected than Config B)\n")

    lo, hi, pt = _bootstrap_ci_delta_diff(wcet_a_st, wcet_a_bl,
                                           wcet_b_st, wcet_b_bl)
    if lo == lo:   # not nan
        d_a = (sum(wcet_a_st) / len(wcet_a_st) - sum(wcet_a_bl) / len(wcet_a_bl)) \
              if wcet_a_st and wcet_a_bl else float("nan")
        d_b = (sum(wcet_b_st) / len(wcet_b_st) - sum(wcet_b_bl) / len(wcet_b_bl)) \
              if wcet_b_st and wcet_b_bl else float("nan")
        print(f"  ΔWCET Config A : {d_a:>+.1f} µs")
        print(f"  ΔWCET Config B : {d_b:>+.1f} µs")
        print(f"  ΔΔWCET (A−B)    : {pt:>+.1f} µs   99% CI [{lo:>+.1f},  {hi:>+.1f}] µs")
        if lo > 0:
            print("  H3 CONFIRMED: Config A significantly more affected than Config B.")
        elif hi < 0:
            print("  H3 REJECTED: Config B unexpectedly more affected than Config A.")
        else:
            print("  H3 INCONCLUSIVE: CI includes zero.")
        result["delta_diff"] = {"lo": lo, "hi": hi, "pt": pt}
    else:
        print("  Insufficient data for H3 test.")

    # ─ Direct WCET level comparison per N ──────────────────────────────
    print("\n  Direct WCET comparison per N  (bootstrap 99% CI on WCET_A − WCET_B)")
    print(f"  {'N':>5}  {'A WCET':>10}  {'B WCET':>10}  {'A−B':>10}  {'99% CI':>24}  Sig")
    print(f"  {'-' * 70}")

    a_by_n = {v["n_workers"]: v for v in conditions_a.values()}
    b_by_n = {v["n_workers"]: v for v in conditions_b.values()}
    for n in sorted(set(a_by_n) & set(b_by_n)):
        av = _vals(a_by_n[n]["runs"], "resp_max_us")
        bv = _vals(b_by_n[n]["runs"], "resp_max_us")
        a_med = sorted(av)[len(av) // 2] if av else float("nan")
        b_med = sorted(bv)[len(bv) // 2] if bv else float("nan")
        if len(av) >= 2 and len(bv) >= 2:
            lo_n, hi_n, pt_n = _bootstrap_ci_difference(bv, av)  # A - B: a=b_vals, b=a_vals
            sig = "*" if lo_n > 0 else ""
            print(f"  {n:>5}  {a_med:>9.1f}µs  {b_med:>9.1f}µs  {pt_n:>+9.1f}µs  "
                  f"[{lo_n:>+9.1f},  {hi_n:>+9.1f}]µs  {sig}")
        else:
            print(f"  {n:>5}  insufficient data")

    return result


# ─── Cross-Variant Comparison ─────────────────────────────────────────────────────


def cross_variant_analysis(conditions_a, conditions_b, output_dir):
    """Compare Configuration A (HI-partitioned) vs Configuration B (LI-colocated).

    Prints a summary table, runs H4/H5 statistical tests, and generates
    per-stressor N-trend comparison plots (overlay, ratio, boxplot, miss rate).
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping cross-variant plots.")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Stressor types present in both configurations
    a_stressors = {v["stressor"] for v in conditions_a.values()}
    b_stressors = {v["stressor"] for v in conditions_b.values()}
    common = [st for st in STRESSOR_ORDER if st in a_stressors and st in b_stressors]

    if not common:
        print("No overlapping stressor types between Configuration A and B.")
        return

    # Pool runs by stressor type (for the aggregate summary table)
    def _pool(conditions):
        runs_by_st = {}
        for cond in conditions.values():
            runs_by_st.setdefault(cond["stressor"], []).extend(cond["runs"])
        return runs_by_st

    runs_a = _pool(conditions_a)
    runs_b = _pool(conditions_b)

    # ── Aggregate summary table ───────────────────────────────────────────
    print("\n" + "=" * 100)
    print("CROSS-VARIANT COMPARISON (A = HI-partitioned, B = LI-colocated)")
    print("=" * 100)
    print(f"{'Stressor':<16} {'A Resp Max':>11} {'B Resp Max':>11} {'Ratio B/A':>10} "
          f"{'A Miss%':>8} {'B Miss%':>8} {'A Margin%':>9} {'B Margin%':>9}")
    print("-" * 100)

    for st in common:
        lbl   = STRESSOR_LABELS.get(st, st)
        wa    = max(_vals(runs_a[st], "resp_max_us"), default=0)
        wb    = max(_vals(runs_b[st], "resp_max_us"), default=0)
        ratio = wb / wa if wa > 0 else 0

        cycles_a = sum(r.get("total_cycles", 0)    for r in runs_a[st])
        misses_a = sum(r.get("deadline_misses", 0) for r in runs_a[st])
        miss_a   = (misses_a / cycles_a * 100) if cycles_a > 0 else 0

        cycles_b = sum(r.get("total_cycles", 0)    for r in runs_b[st])
        misses_b = sum(r.get("deadline_misses", 0) for r in runs_b[st])
        miss_b   = (misses_b / cycles_b * 100) if cycles_b > 0 else 0

        margin_a = (DEADLINE_US - wa) / DEADLINE_US * 100
        margin_b = (DEADLINE_US - wb) / DEADLINE_US * 100

        print(f"{lbl:<16} {wa:>10.1f}u {wb:>10.1f}u {ratio:>9.2f}x "
              f"{miss_a:>7.2f}% {miss_b:>7.2f}% {margin_a:>8.1f}% {margin_b:>8.1f}%")

    print("=" * 100)

    # ── Per-stressor N-trend cross-variant plots ───────────────────────────
    stressed = [st for st in common if st != "baseline"]
    for st in stressed:
        a_conds = for_stressor(conditions_a, st)
        b_conds = for_stressor(conditions_b, st)
        if len(a_conds) < 2 or len(b_conds) < 2:
            continue
        plot_cv_overlay(a_conds, b_conds, output_dir, st)
        plot_cv_ratio(a_conds, b_conds, output_dir, st)
        plot_cv_side_by_side_boxplot(a_conds, b_conds, output_dir, st)
        plot_cv_miss_rate_comparison(a_conds, b_conds, output_dir, st)
        test_cross_variant_stats(a_conds, b_conds, st)

    # ── Standalone Config B N-trend plots ────────────────────────────────
    print("\nConfiguration B standalone plots...")
    for st in stressed:
        b_conds = for_stressor(conditions_b, st)
        if len(b_conds) < 2:
            continue
        sfx = f"_confB_{st}"
        plot_wcet_trend(b_conds,    output_dir, st, sfx)
        plot_mean_response(b_conds, output_dir, st, sfx)
        plot_jitter_trend(b_conds,  output_dir, st, sfx)


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Scenario 1 Plots and Statistical Tests"
    )
    parser.add_argument(
        "-r", "--results-dir", required=True,
        help="Path to primary results directory (Configuration A)",
    )
    parser.add_argument("--stressor", default=None,
                        help="Restrict to one stressor type (e.g. cache, stream, cpu)")
    parser.add_argument(
        "-r2", "--results-dir-2", default=None,
        help="Path to second results directory (Configuration B) for cross-variant comparison",
    )
    parser.add_argument("-o", "--output-dir", default=None,
                        help="Plot output directory  (default: <results-dir>/plots)")
    args = parser.parse_args()

    try:
        import matplotlib
        matplotlib.use("Agg")
    except ImportError:
        sys.exit("ERROR: matplotlib not installed.  pip3 install matplotlib")

    out = args.output_dir or os.path.join(args.results_dir, "plots")
    os.makedirs(out, exist_ok=True)

    print("=" * 62)
    print("  Scenario 1 Analysis -- Task Temporal Isolation")
    print("=" * 62)
    print(f"  Configuration A:  {args.results_dir}")
    if args.results_dir_2:
        print(f"  Configuration B:  {args.results_dir_2}")
    print(f"  Output:           {out}")

    # ── Load Configuration A ──────────────────────────────────────────────
    conditions_a = discover_conditions(args.results_dir)
    if not conditions_a:
        sys.exit(
            f"ERROR: no conditions found in {args.results_dir}\n"
            "Expected subdirs like: baseline_n0  cache_n4  stream_n32"
        )

    s_types = stressor_types(conditions_a)
    to_plot = [args.stressor] if args.stressor else s_types
    total   = sum(len(v["runs"]) for v in conditions_a.values())
    print(f"\nConfiguration A: {total} runs, {len(conditions_a)} conditions, "
          f"stressors: {', '.join(s_types)}")

    print_summary_table(conditions_a, label="Configuration A")

    # Statistical tests (H0-H3)
    run_statistical_tests(conditions_a, label="Configuration A")

    # Load time-series data
    ts_data = load_timeseries(args.results_dir)
    ts_runs = sum(len(runs) for runs in ts_data.values())
    print(f"\nLoaded time-series data for {ts_runs} runs.")

    print("\nGenerating aggregate plots...")
    generate_plots(conditions_a, out)

    print("\nGenerating per-stressor N-trend plots (Configuration A)...")
    for st in to_plot:
        conds = for_stressor(conditions_a, st)
        if len(conds) < 2:
            print(f"  [{st}: fewer than 2 conditions — skipped]")
            continue
        sfx = f"_{st}"
        plot_wcet_trend(conds,       out, st, sfx)
        plot_mean_response(conds,    out, st, sfx)
        plot_jitter_trend(conds,     out, st, sfx)
        plot_miss_rate_trend(conds,  out, st, sfx)
        plot_boxplot_response(conds, out, st, sfx)

    # if ts_data:
    #     print("\nGenerating time-series plots...")
    #     generate_timeseries_plots(ts_data, out)

    # Cross-variant comparison (H4, H5)
    if args.results_dir_2:
        print(f"\nLoading Configuration B results from: {args.results_dir_2}")
        conditions_b = discover_conditions(args.results_dir_2)
        if conditions_b:
            total_b = sum(len(v["runs"]) for v in conditions_b.values())
            print(f"Loaded {total_b} Configuration B runs across "
                  f"{len(conditions_b)} conditions.")
            print("\nConfiguration B summary:")
            print_summary_table(conditions_b, label="Configuration B")
            run_statistical_tests(conditions_b, label="Configuration B")

            cross_dir = os.path.join(out, "cross_variant")
            print("\nGenerating cross-variant comparison...")
            cross_variant_analysis(conditions_a, conditions_b, cross_dir)
        else:
            print(f"WARNING: No results found in {args.results_dir_2}")

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
