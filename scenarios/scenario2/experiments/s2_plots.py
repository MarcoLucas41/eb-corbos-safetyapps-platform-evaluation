#!/usr/bin/env python3
"""
Scenario 2 Analysis — Inter-VM Communication Latency and Reliability

Parses run_XX.txt files produced by s2_orchestrator.sh,
prints summary tables, runs formal statistical tests, and generates plots for
every hypothesis in the experiment design (H0-H4).

Usage
-----
# Configuration A only:
  python3 s2_plots.py -r results/2026-05-18_14:00

# Filter to one stressor (recommended for N-trend analysis):
  python3 s2_plots.py -r results/2026-05-18_14:00 --stressor cache

# Configuration A + B cross-configuration (H4):
  python3 s2_plots.py -r results/... --hicom-dir results_hicom/...

Condition directory naming (from s2_orchestrator.sh):
  {stressor}_{n{workers}}   e.g.  baseline_n0  cache_n4  worst-case_n32

"""

import os
import re
import sys
import csv
import argparse
from pathlib import Path

# ─── Thresholds (scenario2_design.md) ──────────────────────────────────────

DEGRADATION_P99_FACTOR    = 3.0    # summary-table flag: P99 > 3x baseline
DEGRADATION_JITTER_FACTOR = 2.0    # summary-table flag: jitter > 2x baseline
ECHO_TIMEOUT_US           = 10_000 # 10 ms echo timeout in microseconds
ALPHA                     = 0.05   # kept for reference; bootstrap CI uses CONF_LEVEL
N_BOOT                    = 10_000 # bootstrap resamples
CONF_LEVEL                = 0.99   # 99% CI — stricter false-positive control for SDV evaluation

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
FALLBACK_COLOR = "#333333"
PROXY_COLOR    = "#F44336"
HICOM_COLOR    = "#2196F3"

# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_run_file(path):
    """
    Parse a single run_XX.txt file (shm_client or shm_ping_hi output).
    Returns a dict of metrics, or None if no recognised data is found.
    """
    text = Path(path).read_text(errors="replace")
    s = {}

    def _re(pattern, cast, key):
        m = re.search(pattern, text)
        if m:
            s[key] = cast(m.group(1))

    _re(r"Messages sent:\s+(\d+)",                      int,   "n_sent")
    _re(r"Messages lost:\s+(\d+)",                      int,   "n_lost")
    _re(r"Messages lost:\s+\d+\s+\(([0-9.]+)%\)",       float, "loss_pct")
    _re(r"Mean:\s+([0-9.]+)\s+Std dev:",                float, "rtt_mean_us")
    _re(r"Mean:\s+[0-9.]+\s+Std dev:\s+([0-9.]+)",      float, "rtt_stddev_us")
    _re(r"Min:\s+([0-9.]+)\s+Max:",                     float, "rtt_min_us")
    _re(r"Min:\s+[0-9.]+\s+Max:\s+([0-9.]+)",           float, "rtt_max_us")
    _re(r"P50:\s+([0-9.]+)\s+P95:",                     float, "rtt_p50_us")
    _re(r"P50:\s+[0-9.]+\s+P95:\s+([0-9.]+)",           float, "rtt_p95_us")
    _re(r"P99:\s+([0-9.]+)\s+P99\.9:",                  float, "rtt_p99_us")
    _re(r"P99:\s+[0-9.]+\s+P99\.9:\s+([0-9.]+)",        float, "rtt_p999_us")
    _re(r"Throughput:\s+([0-9.]+)\s+msg/s",              float, "throughput")

    return s if s else None

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
    """Parse a run_XX.csv file into lists of per-cycle data.

    Handles raw lines that may contain ANSI escape codes and hypervisor
    serial mux prefixes (e.g. from hi VM QEMU serial output).
    """
    rows = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for line in reader:
            try:
                rows.append({
                    "Seq":    int(line["Seq"]),
                    "RTT_ns": float(line["RTT_ns"]),
                })
            except (KeyError, ValueError) as e:
                print(f"Skipping malformed row: {line} (error: {e})")
                continue
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

# ─── Summary table ────────────────────────────────────────────────────────────

def print_summary_table(conditions, label=""):
    W = 122
    print("\n" + "=" * W)
    print(f"  SCENARIO 2 SUMMARY TABLE{('  —  ' + label) if label else ''}")
    print("=" * W)
    print(
        f"  {'Condition':<24} {'Stressor':<12} {'N':>5} {'Runs':>4} "
        f"{'Lost%':>6}  {'MeanRTT':>9} {'StdDev':>8} "
        f"{'P99':>9} {'P99.9':>9} {'MaxRTT':>9} {'Thput':>7}"
    )
    print("-" * W)

    baseline_p99 = baseline_jitter = None

    for name, cond in sorted(conditions.items(),
                              key=lambda x: (x[1]["stressor"], x[1]["n_workers"])):
        runs       = cond["runs"]
        total_sent = sum(r.get("n_sent", 0) for r in runs)
        total_lost = sum(r.get("n_lost", 0) for r in runs)
        loss_pct   = total_lost / total_sent * 100 if total_sent else 0.0

        avg_mean  = _avg(runs, "rtt_mean_us")
        avg_std   = _avg(runs, "rtt_stddev_us")
        avg_p99   = _avg(runs, "rtt_p99_us")
        avg_p999  = _avg(runs, "rtt_p999_us")
        worst_max = max(_vals(runs, "rtt_max_us"), default=0.0)
        avg_tput  = _avg(runs, "throughput")

        if name == "baseline_n0":
            baseline_p99    = avg_p99
            baseline_jitter = avg_std

        flags = []
        if baseline_p99    and avg_p99  > baseline_p99    * DEGRADATION_P99_FACTOR:
            flags.append("P99!")
        if baseline_jitter and avg_std  > baseline_jitter * DEGRADATION_JITTER_FACTOR:
            flags.append("J!")
        flag_str = "  " + " ".join(flags) if flags else ""

        print(
            f"  {name:<24} {cond['stressor']:<12} {cond['n_workers']:>5} {len(runs):>4} "
            f"{loss_pct:>5.2f}%  {avg_mean:>9.1f} {avg_std:>8.1f} "
            f"{avg_p99:>9.1f} {avg_p999:>9.1f} {worst_max:>9.1f} {avg_tput:>7.0f}"
            f"{flag_str}"
        )

    print("=" * W)
    print("  RTT in µs.  Throughput in msg/s.")
    if baseline_p99:
        print(f"  P99! = P99 > {DEGRADATION_P99_FACTOR}x baseline "
              f"({baseline_p99:.1f} µs threshold: {baseline_p99 * DEGRADATION_P99_FACTOR:.1f} µs)")
    if baseline_jitter:
        print(f"  J!   = jitter > {DEGRADATION_JITTER_FACTOR}x baseline "
              f"({baseline_jitter:.1f} µs threshold: {baseline_jitter * DEGRADATION_JITTER_FACTOR:.1f} µs)")

# ─── Statistical tests ────────────────────────────────────────────────────────────────


def run_statistical_tests(conditions, label=""):
    """Hypothesis tests using P99.9 RTT and bootstrap 99% CIs.

    Metric: rtt_p999_us per run.

    H0: P99.9 RTT distributions identical across stressor types
        → bootstrap CI on median P99.9; non-overlapping CIs reject H0.
    H1: At least one stressor causes a significant increase in P99.9 RTT vs baseline
        → bootstrap CI on ΔRTT_P99.9; CI excluding zero ⇒ significant.
    H2: CPU/Cache ΔRTT_P99.9 > Memory/IO ΔRTT_P99.9
        → bootstrap CI on (ΔRTT_cpu/cache − ΔRTT_mem/io); CI > 0 ⇒ supported.
    Compliance check: worst P99.9 RTT vs 10 ms echo timeout.
    """
    # Pool all runs per stressor type across N levels
    stressor_runs = {}
    for cond in conditions.values():
        stressor_runs.setdefault(cond["stressor"], []).extend(cond["runs"])

    baseline_runs  = stressor_runs.get("baseline", [])
    baseline_p999  = _vals(baseline_runs, "rtt_p999_us")
    stressed_types = sorted(st for st in stressor_runs if st != "baseline")

    print("\n" + "=" * 80)
    print(f"STATISTICAL HYPOTHESIS TESTS  [P99.9 RTT + Bootstrap {CONF_LEVEL*100:.0f}% CI]"
          f"{('  —  ' + label) if label else ''}")
    print(f"  Metric : rtt_p999_us per run")
    print(f"  Method : percentile bootstrap  (N_boot = {N_BOOT:,},  CI = {CONF_LEVEL*100:.0f}%)")
    print("=" * 80)

    # ── H0: Bootstrap CI on median P99.9 per stressor ──────────────────────────
    print("\n--- H₀: P99.9 RTT distributions across stressor types ---")
    print(f"  {'Stressor':<16} {'Runs':>5}  {'Median P99.9':>14}  {f'{CONF_LEVEL*100:.0f}% CI':>26}  Overlaps baseline?")
    print(f"  {'-' * 80}")

    bl_ci    = None
    p999_cis = {}
    for st in ["baseline"] + stressed_types:
        runs      = stressor_runs.get(st, [])
        p999_vals = _vals(runs, "rtt_p999_us")
        lbl       = STRESSOR_LABELS.get(st, st)
        if len(p999_vals) < 2:
            print(f"  {lbl:<16} {'':>5}  insufficient data")
            continue
        lo, hi, med = _bootstrap_ci_single(p999_vals)
        p999_cis[st] = (lo, hi, med)
        if st == "baseline":
            bl_ci  = (lo, hi, med)
            status = "  (reference)"
        elif bl_ci:
            overlaps = not (lo > bl_ci[1] or hi < bl_ci[0])
            status   = "  ✓ overlaps" if overlaps else "  ✗ no overlap  *"
        else:
            status = ""
        print(f"  {lbl:<16} {len(p999_vals):>5}  {med:>11.1f} µs    "
              f"[{lo:>8.1f},  {hi:>8.1f}] µs  {status}")

    if bl_ci:
        rejected = [STRESSOR_LABELS.get(st, st) for st in stressed_types
                    if st in p999_cis and
                    (p999_cis[st][0] > bl_ci[1] or p999_cis[st][1] < bl_ci[0])]
        print()
        if rejected:
            print(f"  H₀ REJECTED: non-overlapping CI vs baseline → {', '.join(rejected)}")
        else:
            print("  H₀ NOT REJECTED: all stressor P99.9 CIs overlap with baseline")

    # ── H1: Bootstrap CI on ΔRTT_P99.9 per stressor ─────────────────────────
    print(f"\n--- H₁: ΔRTT_P99.9 vs baseline  (bootstrap {CONF_LEVEL*100:.0f}% CI) ---")
    print(f"  Significant (*) if CI excludes zero (lo > 0)\n")
    print(f"  {'Stressor':<16} {'ΔRTT_P99.9':>12}  {f'{CONF_LEVEL*100:.0f}% CI':>28}  Sig")
    print(f"  {'-' * 63}")

    for st in stressed_types:
        p999_st = _vals(stressor_runs[st], "rtt_p999_us")
        lbl     = STRESSOR_LABELS.get(st, st)
        if len(baseline_p999) < 2 or len(p999_st) < 2:
            print(f"  {lbl:<16} insufficient data")
            continue
        lo, hi, delta = _bootstrap_ci_difference(baseline_p999, p999_st)
        sig = "*" if lo > 0 else ("" if hi > 0 else "✗ negative")
        print(f"  {lbl:<16} {delta:>+11.1f}µs   [{lo:>+10.1f},  {hi:>+10.1f}]µs  {sig}")

    # ── H2: CPU/Cache ΔRTT vs Memory/IO ΔRTT ─────────────────────────────
    print("\n--- H₂: CPU/Cache ΔRTT_P99.9 vs Memory/IO ΔRTT_P99.9 ---")
    print("  Test: bootstrap CI on (ΔRTT_cpu/cache − ΔRTT_mem/io)")
    print("  H₂ SUPPORTED if CI excludes zero and is positive\n")

    cc_runs = [r for st in ["cpu", "cache"] if st in stressor_runs
               for r in stressor_runs[st]]
    mi_runs = [r for st in ["stream", "io"] if st in stressor_runs
               for r in stressor_runs[st]]
    cc_p999 = _vals(cc_runs, "rtt_p999_us")
    mi_p999 = _vals(mi_runs, "rtt_p999_us")

    if len(cc_p999) >= 2 and len(mi_p999) >= 2 and len(baseline_p999) >= 2:
        lo_cc, hi_cc, d_cc = _bootstrap_ci_difference(baseline_p999, cc_p999)
        lo_mi, hi_mi, d_mi = _bootstrap_ci_difference(baseline_p999, mi_p999)
        lo,    hi,    diff = _bootstrap_ci_difference(mi_p999, cc_p999)
        print(f"  ΔRTT_P99.9 CPU/Cache : {d_cc:>+.1f}µs   [{lo_cc:>+.1f},  {hi_cc:>+.1f}]")
        print(f"  ΔRTT_P99.9 Mem/IO    : {d_mi:>+.1f}µs   [{lo_mi:>+.1f},  {hi_mi:>+.1f}]")
        print(f"  Difference           : {diff:>+.1f}µs   [{lo:>+.1f},  {hi:>+.1f}]")
        if lo > 0:
            print("  H₂ SUPPORTED: CPU/cache cause significantly larger P99.9 RTT increase.")
        elif hi < 0:
            print("  H₂ NOT SUPPORTED: Memory/IO cause larger P99.9 RTT increase.")
        else:
            print("  H₂ INCONCLUSIVE: CI includes zero.")
    else:
        print("  Insufficient data for one or both stressor groups.")

    # ── Compliance check: worst P99.9 RTT vs echo timeout ────────────────────
    print(f"\n--- Compliance check: P99.9 RTT vs echo timeout ({ECHO_TIMEOUT_US/1000:.0f} ms) ---")
    all_p999 = [(cname, max(_vals(cond["runs"], "rtt_p999_us"), default=0.0))
                for cname, cond in conditions.items()]
    if all_p999:
        worst_name, worst_val = max(all_p999, key=lambda x: x[1])
        margin = (ECHO_TIMEOUT_US - worst_val) / ECHO_TIMEOUT_US * 100
        print(f"  Worst P99.9 RTT : {worst_val:.1f} µs  ({worst_name})")
        print(f"  Echo timeout    : {ECHO_TIMEOUT_US:.0f} µs  ({ECHO_TIMEOUT_US/1000:.0f} ms)")
        print(f"  Safety margin   : {margin:.1f}%")
        if worst_val <= ECHO_TIMEOUT_US:
            print("  All P99.9 RTTs within echo timeout.")
        else:
            print(f"  TIMEOUT EXCEEDED: P99.9 RTT exceeds timeout by {worst_val - ECHO_TIMEOUT_US:.1f} µs.")

    print("=" * 80)


def test_cross_configuration(proxy_conds, hicom_conds, stressor):
    """H3: ΔRTT_P99.9 caused by stressor is significantly greater in Config A
    (proxycomshm, through LI VM) than in Config B (hicom, HI-only channel).

    Uses _bootstrap_ci_delta_diff on four independent P99.9 RTT sample pools.
    H3 confirmed if 99% CI on (ΔRTT_A − ΔRTT_B) is positive (excludes zero).
    Also reports per-N direct P99.9 comparison with bootstrap CI.
    """
    sep = "─" * 72
    print(f"\n{sep}")
    print(f"  Cross-Configuration Tests (H3)  [Bootstrap {CONF_LEVEL*100:.0f}% CI]  —  stressor: {stressor}")
    print(sep)

    # Separate stressor and baseline runs for each config
    proxy_stress_runs = [r for cond in proxy_conds.values()
                         if cond["stressor"] != "baseline" for r in cond["runs"]]
    proxy_base_runs   = [r for cond in proxy_conds.values()
                         if cond["stressor"] == "baseline" for r in cond["runs"]]
    hicom_stress_runs = [r for cond in hicom_conds.values()
                         if cond["stressor"] != "baseline" for r in cond["runs"]]
    hicom_base_runs   = [r for cond in hicom_conds.values()
                         if cond["stressor"] == "baseline" for r in cond["runs"]]

    p_st = _vals(proxy_stress_runs, "rtt_p999_us")
    p_bl = _vals(proxy_base_runs,   "rtt_p999_us")
    h_st = _vals(hicom_stress_runs, "rtt_p999_us")
    h_bl = _vals(hicom_base_runs,   "rtt_p999_us")

    result = {}

    # ─ H3: ΔRTT_proxy − ΔRTT_hicom ────────────────────────────────────────────
    print(f"\n  H3: ΔRTT_P99.9_proxy − ΔRTT_P99.9_hicom  (bootstrap {CONF_LEVEL*100:.0f}% CI)")
    print("  H3 confirmed if CI > 0  (proxycomshm more affected than hicom)\n")

    lo, hi, pt = _bootstrap_ci_delta_diff(p_st, p_bl, h_st, h_bl)
    if lo == lo:   # not nan
        d_p = (sum(p_st) / len(p_st) - sum(p_bl) / len(p_bl)) \
              if p_st and p_bl else float("nan")
        d_h = (sum(h_st) / len(h_st) - sum(h_bl) / len(h_bl)) \
              if h_st and h_bl else float("nan")
        print(f"  ΔRTT_P99.9 proxy : {d_p:>+.1f} µs")
        print(f"  ΔRTT_P99.9 hicom : {d_h:>+.1f} µs")
        print(f"  ΔΔRTT (proxy−hicom): {pt:>+.1f} µs   {CONF_LEVEL*100:.0f}% CI [{lo:>+.1f},  {hi:>+.1f}] µs")
        if lo > 0:
            print("  H3 CONFIRMED: proxycomshm significantly more affected than hicom.")
        elif hi < 0:
            print("  H3 REJECTED: hicom unexpectedly more affected than proxycomshm.")
        else:
            print("  H3 INCONCLUSIVE: CI includes zero.")
        result["delta_diff"] = {"lo": lo, "hi": hi, "pt": pt}
    else:
        print("  Insufficient data for H3 test.")

    # ─ Direct P99.9 comparison per N ──────────────────────────────────────
    print(f"\n  Direct P99.9 RTT comparison per N  (bootstrap {CONF_LEVEL*100:.0f}% CI on proxy − hicom)")
    print(f"  {'N':>5}  {'Proxy P99.9':>12}  {'hicom P99.9':>12}  {'Proxy−hicom':>12}  {f'{CONF_LEVEL*100:.0f}% CI':>24}  Sig")
    print(f"  {'-' * 75}")

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    for n in sorted(set(proxy_by_n) & set(hicom_by_n)):
        pv = _vals(proxy_by_n[n]["runs"], "rtt_p999_us")
        hv = _vals(hicom_by_n[n]["runs"], "rtt_p999_us")
        p_med = sorted(pv)[len(pv) // 2] if pv else float("nan")
        h_med = sorted(hv)[len(hv) // 2] if hv else float("nan")
        if len(pv) >= 2 and len(hv) >= 2:
            lo_n, hi_n, pt_n = _bootstrap_ci_difference(hv, pv)  # proxy - hicom
            sig = "*" if lo_n > 0 else ""
            print(f"  {n:>5}  {p_med:>11.1f}µs  {h_med:>11.1f}µs  {pt_n:>+11.1f}µs  "
                  f"[{lo_n:>+9.1f},  {hi_n:>+9.1f}]µs  {sig}")
        else:
            print(f"  {n:>5}  insufficient data")

    return result

# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"  Saved: {path}")

def _sorted_sc(conditions):
    """Return (n_vals_list, sorted_cond_list)."""
    sc = sorted(conditions.values(), key=lambda x: x["n_workers"])
    return [c["n_workers"] for c in sc], sc

# ─── Bootstrap CI helpers ────────────────────────────────────────────────


def _bootstrap_ci_single(vals, n_boot=N_BOOT, ci=CONF_LEVEL):
    """Bootstrap CI on the median of a single sample (percentile method).
    Returns (ci_lower, ci_upper, point_median). No scipy/numpy required.
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
    """Bootstrap CI on (ΔRTT_A − ΔRTT_B) from four independent samples.
    ΔRTT_X = mean(stress_X) − mean(base_X).
    Returns (ci_lower, ci_upper, point_estimate).
    CI > 0 means Config A more affected by the stressor than Config B (H3).
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

# ─── Per-configuration plots ────────────────────────────────────────────────────────

def plot_mean_rtt(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    means  = [_avg(c["runs"], "rtt_mean_us")             for c in sc]
    errs   = [_std_across_runs(c["runs"], "rtt_mean_us") for c in sc]
    color  = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=errs, fmt="o-", capsize=5,
                color=color, linewidth=2, markersize=7,
                label="mean ± cross-run std")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean RTT (µs)")
    ax.set_title(f"Mean RTT vs Load — {stressor}  (H1)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_mean{suffix}.png"))
    plt.close(fig)


def plot_percentiles(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    p50s  = [_avg(c["runs"], "rtt_p50_us")  for c in sc]
    p95s  = [_avg(c["runs"], "rtt_p95_us")  for c in sc]
    p99s  = [_avg(c["runs"], "rtt_p99_us")  for c in sc]
    p999s = [_avg(c["runs"], "rtt_p999_us") for c in sc]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, p50s,  "o-", label="P50",   color="#4CAF50", linewidth=2)
    ax.plot(n_vals, p95s,  "D-", label="P95",   color="#2196F3", linewidth=2)
    ax.plot(n_vals, p99s,  "s-", label="P99",   color="#FF9800", linewidth=2)
    ax.plot(n_vals, p999s, "^-", label="P99.9", color="#F44336", linewidth=2)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTT (µs)")
    ax.set_title(f"RTT Percentiles vs Load — {stressor}  (H1/H2)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_percentiles{suffix}.png"))
    plt.close(fig)


def plot_boxplot(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels   = [str(n) for n in n_vals]
    box_data = [_vals(c["runs"], "rtt_mean_us") for c in sc]
    colors   = (["#607D8B"] +
                [STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)] * (len(sc) - 1))

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    bp = ax.boxplot(box_data, tick_labels=labels, patch_artist=True)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c); patch.set_alpha(0.7)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean RTT (µs)")
    ax.set_title(f"RTT Distribution per Condition — {stressor}  (run-to-run variance)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_boxplot{suffix}.png"))
    plt.close(fig)


def plot_loss_rate(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels = [str(n) for n in n_vals]
    rates  = []
    for c in sc:
        sent = sum(r.get("n_sent", 0) for r in c["runs"])
        lost = sum(r.get("n_lost", 0) for r in c["runs"])
        rates.append(lost / sent * 100 if sent else 0.0)

    bar_colors = ["#4CAF50" if r == 0 else "#F44336" for r in rates]
    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, rates, color=bar_colors, alpha=0.85, edgecolor="black")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Message Loss Rate (%)")
    ax.set_title(f"Message Loss Rate — {stressor}  (H3: should be 0%)")
    ax.grid(axis="y", alpha=0.3)
    if max(rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No message loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"loss_rate{suffix}.png"))
    plt.close(fig)


def plot_jitter(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels  = [str(n) for n in n_vals]
    jitters = [_avg(c["runs"], "rtt_stddev_us") for c in sc]
    color   = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, jitters, color=color, alpha=0.8, edgecolor="black")
    if jitters:
        thr = jitters[0] * DEGRADATION_JITTER_FACTOR
        ax.axhline(thr, linestyle="--", color="red", alpha=0.7,
                   label=f"{DEGRADATION_JITTER_FACTOR}x baseline ({thr:.1f} µs)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTT Std Dev (µs)")
    ax.set_title(f"RTT Jitter vs Load — {stressor}  (H1)")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_jitter{suffix}.png"))
    plt.close(fig)

def plot_p999_trend(conditions, out, stressor, suffix=""):
    """P99.9 RTT vs N with bootstrap 99% CI  —  primary H0/H1 metric plot."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    medians, ci_los, ci_his = [], [], []
    for c in sc:
        p999_vals = _vals(c["runs"], "rtt_p999_us")
        if len(p999_vals) >= 2:
            lo, hi, med = _bootstrap_ci_single(p999_vals)
        else:
            med = p999_vals[0] if p999_vals else float("nan")
            lo, hi = med, med
        medians.append(med)
        ci_los.append(med - lo)
        ci_his.append(hi - med)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, medians, yerr=[ci_los, ci_his],
                fmt="o-", capsize=5, color=color, linewidth=2, markersize=7,
                label=f"median P99.9 RTT  ±  {CONF_LEVEL*100:.0f}% bootstrap CI")
    ax.axhline(y=ECHO_TIMEOUT_US, color="red", linestyle="--", linewidth=1,
               label=f"Echo timeout ({ECHO_TIMEOUT_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("P99.9 RTT (µs)")
    ax.set_title(f"P99.9 RTT vs Load — {stressor}  (H0/H1)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"p999_trend{suffix}.png"))
    plt.close(fig)


# ─── Cross-configuration plots (H3/H4) ───────────────────────────────────────────────

def plot_overlay(proxy_conds, hicom_conds, out, stressor):
    """Median P99.9 RTT + bootstrap CI for both configurations  —  primary H3 figure."""
    import matplotlib.pyplot as plt

    def _extract(conds):
        sc = sorted(conds.values(), key=lambda x: x["n_workers"])
        ns, meds, los, his = [], [], [], []
        for c in sc:
            p999_vals = _vals(c["runs"], "rtt_p999_us")
            if len(p999_vals) >= 2:
                lo, hi, med = _bootstrap_ci_single(p999_vals)
            else:
                med = p999_vals[0] if p999_vals else float("nan")
                lo, hi = med, med
            ns.append(c["n_workers"])
            meds.append(med)
            los.append(med - lo)
            his.append(hi - med)
        return ns, meds, los, his

    pn, pm, p_lo, p_hi = _extract(proxy_conds)
    hn, hm, h_lo, h_hi = _extract(hicom_conds)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(pn, pm, yerr=[p_lo, p_hi], fmt="o-",  capsize=5, color=PROXY_COLOR,
                linewidth=2, markersize=7,
                label=f"Configuration A — proxycomshm  (median P99.9 ± {CONF_LEVEL*100:.0f}% CI)")
    ax.errorbar(hn, hm, yerr=[h_lo, h_hi], fmt="s--", capsize=5, color=HICOM_COLOR,
                linewidth=2, markersize=7,
                label=f"Configuration B — hicom  (median P99.9 ± {CONF_LEVEL*100:.0f}% CI)")
    ax.axhline(y=ECHO_TIMEOUT_US, color="red", linestyle="--", linewidth=1,
               label=f"Echo timeout ({ECHO_TIMEOUT_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("P99.9 RTT (µs)")
    ax.set_title(f"Cross-Configuration P99.9 RTT Comparison — {stressor}  (H3)\n"
                 f"Vertical gap = LI VM channel exposure  |  error bars = bootstrap {CONF_LEVEL*100:.0f}% CI")
    ax.set_xticks(sorted(set(pn) | set(hn)))
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"overlay_{stressor}.png"))
    plt.close(fig)


def plot_rtt_ratio(proxy_conds, hicom_conds, out, stressor):
    """RTT ratio (hicom / proxycomshm) vs N — isolation effectiveness."""
    import matplotlib.pyplot as plt

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n   = sorted(set(proxy_by_n) & set(hicom_by_n))

    ratios = []
    for n in common_n:
        pm = _avg(proxy_by_n[n]["runs"], "rtt_mean_us")
        hm = _avg(hicom_by_n[n]["runs"],  "rtt_mean_us")
        ratios.append(hm / pm if pm > 0 else 0.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(common_n, ratios, "o-", color="#9C27B0", linewidth=2, markersize=7)
    ax.axhline(1.0, linestyle="--", color="grey", alpha=0.5,
               label="ratio = 1.0  (equal impact on both channels)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTT ratio  (hicom / proxycomshm)")
    ax.set_title(f"Isolation Effectiveness — {stressor}  (H4)\n"
                 "Decreasing ratio: hicom increasingly less affected than proxycomshm")
    ax.set_xticks(common_n); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_ratio_{stressor}.png"))
    plt.close(fig)


def plot_side_by_side_boxplot(proxy_conds, hicom_conds, out, stressor):
    """Side-by-side box plots per N for both configurations."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping side-by-side boxplot]")
        return

    from matplotlib.patches import Patch

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n   = sorted(set(proxy_by_n) & set(hicom_by_n))

    pos    = np.arange(len(common_n))
    width  = 0.35
    p_data = [_vals(proxy_by_n[n]["runs"], "rtt_mean_us") for n in common_n]
    h_data = [_vals(hicom_by_n[n]["runs"],  "rtt_mean_us") for n in common_n]

    fig, ax = plt.subplots(figsize=(max(9, len(common_n) * 1.6), 5))
    bp1 = ax.boxplot(p_data, positions=pos - width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)
    bp2 = ax.boxplot(h_data, positions=pos + width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)

    for p in bp1["boxes"]:
        p.set_facecolor(PROXY_COLOR); p.set_alpha(0.6)
    for p in bp2["boxes"]:
        p.set_facecolor(HICOM_COLOR); p.set_alpha(0.6)

    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean RTT (µs)")
    ax.set_title(f"RTT Distribution Comparison — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=PROXY_COLOR, alpha=0.6, label="Configuration A — proxycomshm"),
        Patch(facecolor=HICOM_COLOR, alpha=0.6, label="Configuration B — hicom"),
    ])
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"boxplot_comparison_{stressor}.png"))
    plt.close(fig)

def plot_loss_rate_comparison(proxy_conds, hicom_conds, out, stressor):
    """Grouped bar chart of message loss rate for both configurations."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping loss rate comparison]")
        return

    from matplotlib.patches import Patch

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n   = sorted(set(proxy_by_n) & set(hicom_by_n))

    def _loss_pct(cond):
        sent = sum(r.get("n_sent", 0) for r in cond["runs"])
        lost = sum(r.get("n_lost", 0) for r in cond["runs"])
        return lost / sent * 100 if sent else 0.0

    p_rates = [_loss_pct(proxy_by_n[n]) for n in common_n]
    h_rates = [_loss_pct(hicom_by_n[n]) for n in common_n]

    pos   = np.arange(len(common_n))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(7, len(common_n) * 1.6), 5))
    ax.bar(pos - width / 2, p_rates, width, color=PROXY_COLOR, alpha=0.8,
           edgecolor="black", label="Configuration A — proxycomshm")
    ax.bar(pos + width / 2, h_rates, width, color=HICOM_COLOR, alpha=0.8,
           edgecolor="black", label="Configuration B — hicom")
    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Message Loss Rate (%)")
    ax.set_title(f"Message Loss Rate — {stressor}  (H3: should be 0%)")
    ax.legend(handles=[
        Patch(facecolor=PROXY_COLOR, alpha=0.8, label="Configuration A — proxycomshm"),
        Patch(facecolor=HICOM_COLOR, alpha=0.8, label="Configuration B — hicom"),
    ])
    ax.grid(axis="y", alpha=0.3)
    if max(p_rates + h_rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No message loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"loss_rate_comparison_{stressor}.png"))
    plt.close(fig)


def generate_timeseries_plots(ts_data, output_dir):
    """Generate per-run time-series plots (RTT + optional vmstat panels).

    RTT data is indexed by sequence number; vmstat data is indexed by wall-clock
    second.  When vmstat is present the sequence numbers are linearly mapped onto
    the vmstat duration so that all panels share a common time axis.  The mapping
    assumes a roughly uniform message rate, which is valid for a tight ping loop;
    vmstat is 1-second granularity so sub-second errors are invisible.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping time-series plots.")
        print("Install with: pip3 install matplotlib")
        return
    os.makedirs(output_dir, exist_ok=True)

    def _sort_key(item):
        cname = item[0]
        sep   = cname.rfind("_")
        st    = cname[:sep] if sep != -1 else cname
        return (STRESSOR_ORDER.index(st) if st in STRESSOR_ORDER else 99, cname)

    for cond_name, run_entries in sorted(ts_data.items(), key=_sort_key):
        sep      = cond_name.rfind("_")
        stressor = cond_name[:sep] if sep != -1 else cond_name
        color    = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

        for cycles, vmstat, speedom_vmstat, run_num in run_entries:
            if not cycles:
                continue

            has_vmstat  = bool(vmstat)
            has_speedom = bool(speedom_vmstat)

            ts_rtt = [s["Seq"] for s in cycles]
            rtt_us = [s["RTT_ns"] / 1000.0 for s in cycles]

            nrows = 1 + (2 if has_vmstat else 0) + (2 if has_speedom else 0)
            # RTT uses sequence numbers; vmstat uses wall-clock seconds.
            # sharex=False keeps the axes independent so both are legible.
            fig, axes = plt.subplots(nrows, 1, figsize=(14, 4 * nrows), sharex=False)
            if nrows == 1:
                axes = [axes]

            # ── Panel 0: RTT timeline ──────────────────────────────────────
            ax_rtt = axes[0]
            ax_rtt.plot(ts_rtt, rtt_us, linewidth=0.8, color=color, label="RTT")
            ax_rtt.axhline(y=ECHO_TIMEOUT_US, color="red", linestyle="--",
                           linewidth=1,
                           label=f"Echo timeout ({ECHO_TIMEOUT_US / 1000:.0f} ms)")
            ax_rtt.set_ylabel("RTT (µs)")
            ax_rtt.set_title(f"{cond_name} — Run {run_num}")
            ax_rtt.set_xlabel("Sequence #")
            ax_rtt.legend(loc="upper right", fontsize=8)
            ax_rtt.grid(alpha=0.3)

            if has_vmstat:
                vm_ts = [r["timestamp_s"] for r in vmstat]

                # ── Panel 1: vm-li CPU breakdown ───────────────────────────
                ax_cpu = axes[1]
                ax_cpu.stackplot(vm_ts,
                                 [r.get("us", 0) for r in vmstat],
                                 [r.get("sy", 0) for r in vmstat],
                                 [r.get("id", 0) for r in vmstat],
                                 tick_labels=["User (us)", "System (sy)", "Idle (id)"],
                                 colors=["#FF9800", "#F44336", "#E0E0E0"], alpha=0.8)
                ax_cpu.set_ylabel("vm-li CPU %")
                ax_cpu.set_xlabel("Time (s)")
                ax_cpu.set_ylim(0, 105)
                ax_cpu.legend(loc="upper right", fontsize=8)
                ax_cpu.grid(alpha=0.3)

                # ── Panel 2: vm-li system metrics ──────────────────────────
                ax_sys = axes[2]
                ax_sys.plot(vm_ts, [r.get("in", 0) for r in vmstat],
                            linewidth=1, color="#9C27B0", label="Interrupts/s (in)")
                ax_sys.set_ylabel("Interrupts/s", color="#9C27B0")
                ax_sys.tick_params(axis="y", labelcolor="#9C27B0")

                ax_cs = ax_sys.twinx()
                ax_cs.plot(vm_ts, [r.get("cs", 0) for r in vmstat],
                           linewidth=1, color="#2196F3", label="Ctx Switches/s (cs)")
                ax_cs.set_ylabel("Ctx Switches/s", color="#2196F3")
                ax_cs.tick_params(axis="y", labelcolor="#2196F3")

                lines1, labs1 = ax_sys.get_legend_handles_labels()
                lines2, labs2 = ax_cs.get_legend_handles_labels()
                ax_sys.legend(lines1 + lines2, labs1 + labs2,
                              loc="upper right", fontsize=8)
                if not has_speedom:
                    ax_sys.set_xlabel("Time (s)")
                ax_sys.grid(alpha=0.3)

            if has_speedom:
                row_offset = 3 if has_vmstat else 1
                sp_ts = [r["timestamp_s"] for r in speedom_vmstat]

                # ── Panel row_offset: vm-hi CPU breakdown ──────────────────
                ax_sp_cpu = axes[row_offset]
                ax_sp_cpu.stackplot(sp_ts,
                                    [r.get("us", 0) for r in speedom_vmstat],
                                    [r.get("sy", 0) for r in speedom_vmstat],
                                    [r.get("id", 0) for r in speedom_vmstat],
                                    tick_labels=["User (us)", "System (sy)", "Idle (id)"],
                                    colors=["#FF9800", "#F44336", "#E0E0E0"], alpha=0.8)
                ax_sp_cpu.set_ylabel("vm-hi CPU %")
                ax_sp_cpu.set_ylim(0, 105)
                ax_sp_cpu.legend(loc="upper right", fontsize=8)
                ax_sp_cpu.grid(alpha=0.3)

                # ── Panel row_offset+1: vm-hi system metrics ───────────────
                ax_sp_sys = axes[row_offset + 1]
                ax_sp_sys.plot(sp_ts, [r.get("in", 0) for r in speedom_vmstat],
                               linewidth=1, color="#9C27B0",
                               label="Interrupts/s (in)")
                ax_sp_sys.set_ylabel("vm-hi Interrupts/s", color="#9C27B0")
                ax_sp_sys.tick_params(axis="y", labelcolor="#9C27B0")

                ax_sp_cs = ax_sp_sys.twinx()
                ax_sp_cs.plot(sp_ts, [r.get("cs", 0) for r in speedom_vmstat],
                              linewidth=1, color="#2196F3",
                              label="Ctx Switches/s (cs)")
                ax_sp_cs.set_ylabel("vm-hi Ctx Switches/s", color="#2196F3")
                ax_sp_cs.tick_params(axis="y", labelcolor="#2196F3")

                lines1, labs1 = ax_sp_sys.get_legend_handles_labels()
                lines2, labs2 = ax_sp_cs.get_legend_handles_labels()
                ax_sp_sys.legend(lines1 + lines2, labs1 + labs2,
                                 loc="upper right", fontsize=8)
                ax_sp_sys.set_xlabel("Time (s)")
                ax_sp_sys.grid(alpha=0.3)

            elif not has_vmstat:
                pass  # RTT panel already has its x-label set above

            fig.tight_layout()
            fname = os.path.join(output_dir,
                                 f"timeseries_{cond_name}_run{run_num}.png")
            fig.savefig(fname, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"  Saved: {fname}")
# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scenario 2 RTT analysis — plots and statistical tests"
    )
    parser.add_argument("-r", "--results-dir", required=True,
                        help="Configuration A (proxycomshm) results directory")
    parser.add_argument("--hicom-dir", default=None,
                        help="Configuration B (hicom) results dir — enables H4 cross-configuration analysis")
    parser.add_argument("--stressor", default=None,
                        help="Restrict to one stressor type (e.g. cache, stream, cpu)")
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
    print("  Scenario 2 Analysis — Inter-VM Communication Latency")
    print("=" * 62)
    print(f"  Configuration A:  {args.results_dir}")
    if args.hicom_dir:
        print(f"  Configuration B:  {args.hicom_dir}")
    print(f"  Output:     {out}")

    # ── Load Configuration A ────────────────────────────────────────────────────
    proxy_all = discover_conditions(args.results_dir)
    if not proxy_all:
        sys.exit(
            f"ERROR: no conditions found in {args.results_dir}\n"
            "Expected subdirs like: baseline_n0  cache_n4  stream_n32"
        )

    s_types = stressor_types(proxy_all)
    total   = sum(len(v["runs"]) for v in proxy_all.values())
    print(f"\nConfiguration A: {total} runs, {len(proxy_all)} conditions, "
          f"stressors: {', '.join(s_types)}")
    print_summary_table(proxy_all, label="Configuration A — proxycomshm")

    to_plot = [args.stressor] if args.stressor else s_types

    # Statistical tests (H0–H3) — run once over all Configuration A conditions
    run_statistical_tests(proxy_all, label="Configuration A")

    # Load time-series data
    ts_data = load_timeseries(args.results_dir)
    ts_runs = sum(len(runs) for runs in ts_data.values())
    print(f"\nLoaded time-series data for {ts_runs} runs.")
    
    
    print("\nGenerating per-stressor N-trend plots (Configuration A)...")
    for st in to_plot:
        conds = for_stressor(proxy_all, st)
        if len(conds) < 2:
            print(f"  [{st}: fewer than 2 conditions — skipped]")
            continue
        sfx = f"_{st}"
        plot_p999_trend(conds, out, st, sfx)
        plot_mean_rtt(conds,    out, st, sfx)
        plot_percentiles(conds, out, st, sfx)
        plot_boxplot(conds,     out, st, sfx)
        plot_loss_rate(conds,   out, st, sfx)
        plot_jitter(conds,      out, st, sfx)

    # if ts_data:
    #     print("\nGenerating time-series plots...")
    #     generate_timeseries_plots(ts_data, out)

    # Configuration B and cross-configuration (H4)
    if args.hicom_dir:
        hicom_all = discover_conditions(args.hicom_dir)
        if not hicom_all:
            print(f"\nWARNING: no conditions found in {args.hicom_dir}")
        else:
            h_types = stressor_types(hicom_all)
            h_total = sum(len(v["runs"]) for v in hicom_all.values())
            print(f"\nConfiguration B: {h_total} runs, {len(hicom_all)} conditions, "
                  f"stressors: {', '.join(h_types)}")
            print_summary_table(hicom_all, label="Configuration B — hicom")

            # Statistical tests for Configuration B
            run_statistical_tests(hicom_all, label="Configuration B")

            # Cross-configuration analysis for stressors present in both result sets
            cv_stressors = [s for s in to_plot
                            if any(v["stressor"] == s for v in hicom_all.values())]

            if cv_stressors:
                print("\nCross-configuration plots and tests (H4)...")
            for st in cv_stressors:
                p_conds = for_stressor(proxy_all, st)
                h_conds = for_stressor(hicom_all, st)
                if len(p_conds) < 2 or len(h_conds) < 2:
                    continue
                plot_overlay(p_conds, h_conds, out, st)
                plot_rtt_ratio(p_conds, h_conds, out, st)
                plot_side_by_side_boxplot(p_conds, h_conds, out, st)
                plot_loss_rate_comparison(p_conds, h_conds, out, st)
                test_cross_configuration(p_conds, h_conds, st)

            # Standalone Configuration B N-trend plots
            print("\nConfiguration B standalone plots...")
            for st in (cv_stressors or h_types):
                h_conds = for_stressor(hicom_all, st)
                if len(h_conds) < 2:
                    continue
                sfx = f"_hicom_{st}"
                plot_p999_trend(h_conds, out, st, sfx)
                plot_mean_rtt(h_conds,   out, st, sfx)
                plot_jitter(h_conds,     out, st, sfx)

if __name__ == "__main__":
    main()
