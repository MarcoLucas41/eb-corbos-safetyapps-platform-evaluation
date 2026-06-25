#!/usr/bin/env python3
"""
Scenario 3 Analysis — Network Communication Determinism

Parses run_XX.txt files produced by s3_orchestrator.sh,
prints summary tables, runs formal statistical tests, and generates plots for
every hypothesis in s3_design.md (H0–H4).

Usage
-----
# Configuration A only (UDP):
  python3 s3_plots.py -r results_scenario3

# Filter to one stressor:
  python3 s3_plots.py -r results_scenario3 --stressor cpu

# Configuration A UDP + TCP comparison:
  python3 s3_plots.py -r results_scenario3 --tcp-dir results_scenario3_tcp

# Configuration A + B cross-configuration (H4):
  python3 s3_plots.py -r results_scenario3 --hicom-dir results_hicom

# All:
  python3 s3_plots.py -r results_scenario3 --tcp-dir results_scenario3_tcp --hicom-dir results_hicom

Condition directory naming (from s3_orchestrator.sh):
  {stressor}_n{workers}   e.g.  baseline_n0  cpu_n4  worst-case_n32
"""

import os
import re
import sys
import argparse
import csv
import io
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

ECHO_TIMEOUT_US = 10_000   # 10 ms echo timeout
ALPHA           = 0.05   # kept for reference; bootstrap CI uses CONF_LEVEL
N_BOOT          = 10_000 # bootstrap resamples
CONF_LEVEL      = 0.99   # 99% CI — stricter false-positive control for SDV evaluation

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
FALLBACK_COLOR = "#333333"
CONF_A_COLOR   = "#F44336"
CONF_B_COLOR   = "#2196F3"

# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_run_file(path):
    """
    Parse a single run_XX.txt file produced by net_echo, net_client,
    or hi_net_ping.  Returns a dict of metrics, or None if nothing parsed.
    """
    text = Path(path).read_text(errors="replace")
    s = {}

    m = re.search(r"Probes sent:\s+(\d+)", text)
    if m: s["n_sent"] = int(m.group(1))

    m = re.search(r"Echoes received:\s+(\d+)", text)
    if m: s["n_recv"] = int(m.group(1))

    m = re.search(r"Lost \(no echo\):\s+(\d+)\s+\(([0-9.]+)%\)", text)
    if m:
        s["n_lost"]  = int(m.group(1))
        s["loss_pct"] = float(m.group(2))

    m = re.search(r"Elapsed:\s+([0-9.]+)\s+s", text)
    if m: s["elapsed_s"] = float(m.group(1))

    # ── rtl block ──
    rtl_block = re.search(r"RTL \(us\):(.*?)(?=IAJ \(us\)|={5})", text, re.DOTALL)
    if rtl_block:
        _block_extract(rtl_block.group(1), s, "rtl")

    # ── IAJ block ──
    iaj_block = re.search(r"IAJ \(us\):(.*?)(?=={5})", text, re.DOTALL)
    if iaj_block:
        _block_extract(iaj_block.group(1), s, "iaj")

    return s if s else None


def _block_extract(block, s, prefix):
    """Extract Mean/Stddev/Min/Max/P50/P95/P99/P99.9 from a metric block."""
    m = re.search(r"Mean:\s+([0-9.]+)\s+Std dev:\s+([0-9.]+)", block)
    if m:
        s[f"{prefix}_mean_us"]   = float(m.group(1))
        s[f"{prefix}_stddev_us"] = float(m.group(2))
    m = re.search(r"Min:\s+([0-9.]+)\s+Max:\s+([0-9.]+)", block)
    if m:
        s[f"{prefix}_min_us"] = float(m.group(1))
        s[f"{prefix}_max_us"] = float(m.group(2))
    m = re.search(r"P50:\s+([0-9.]+)\s+P95:\s+([0-9.]+)", block)
    if m:
        s[f"{prefix}_p50_us"] = float(m.group(1))
        s[f"{prefix}_p95_us"] = float(m.group(2))
    m = re.search(r"P99:\s+([0-9.]+)\s+P99\.9:\s+([0-9.]+)", block)
    if m:
        s[f"{prefix}_p99_us"]  = float(m.group(1))
        s[f"{prefix}_p999_us"] = float(m.group(2))


def discover_conditions(results_dir):
    """
    Scan results_dir for condition subdirectories and parse their run files.

    Expected naming:  {stressor}_n{workers}
    e.g.  baseline_n0  cpu_n8  worst-case_n32

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
            stressor  = name[:sep]
            n_workers = int(name[sep + 1:][1:])
        else:
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
    if not v: return 0.0
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0

def _std_across_runs(runs, key):
    v = _vals(runs, key)
    if len(v) < 2: return 0.0
    mu = sum(v) / len(v)
    return (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5

def _sorted_sc(conditions):
    """Return (n_vals_list, sorted_cond_list) ordered by n_workers."""
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
    """Bootstrap CI on (Δmetric_A − Δmetric_B) from four independent samples.
    Δmetric_X = mean(stress_X) − mean(base_X).
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


# ─── Summary table ────────────────────────────────────────────────────────────

def print_summary_table(conditions, label=""):
    W = 132
    print("\n" + "=" * W)
    print(f"  SCENARIO 3 SUMMARY TABLE{('  —  ' + label) if label else ''}")
    print("=" * W)
    print(
        f"  {'Condition':<24} {'Stressor':<12} {'N':>5} {'Runs':>4} "
        f"{'Lost%':>6}  {'Meanrtl':>9} {'StdDev':>8} "
        f"{'P99':>9} {'P99.9':>9} {'Maxrtl':>9} "
        f"{'IAJmean':>9} {'IAJstd':>8}"
    )
    print("-" * W)

    def _sort_key(item):
        st = item[1]["stressor"]
        return (STRESSOR_ORDER.index(st) if st in STRESSOR_ORDER else 99,
                item[1]["n_workers"])

    for name, cond in sorted(conditions.items(), key=_sort_key):
        runs       = cond["runs"]
        total_sent = sum(r.get("n_sent", 0) for r in runs)
        total_lost = sum(r.get("n_lost", 0) for r in runs)
        loss_pct   = total_lost / total_sent * 100 if total_sent else 0.0

        avg_mean  = _avg(runs, "rtl_mean_us")
        avg_std   = _avg(runs, "rtl_stddev_us")
        avg_p99   = _avg(runs, "rtl_p99_us")
        avg_p999  = _avg(runs, "rtl_p999_us")
        worst_max = max(_vals(runs, "rtl_max_us"), default=0.0)
        iaj_mean  = _avg(runs, "iaj_mean_us")
        iaj_std   = _avg(runs, "iaj_stddev_us")

        print(
            f"  {name:<24} {cond['stressor']:<12} {cond['n_workers']:>5} {len(runs):>4} "
            f"{loss_pct:>5.2f}%  {avg_mean:>9.1f} {avg_std:>8.1f} "
            f"{avg_p99:>9.1f} {avg_p999:>9.1f} {worst_max:>9.1f} "
            f"{iaj_mean:>9.1f} {iaj_std:>8.1f}"
        )

    print("=" * W)
    print("  rtl / IAJ in \u00b5s.")


# ─── Statistical tests ────────────────────────────────────────────────────────────────


def run_statistical_tests(conditions, label=""):
    """Hypothesis tests using P99.9 RTT and IAJ std dev with bootstrap 99% CIs.

    Metrics: rtl_p999_us (H0a/H1a) and iaj_stddev_us (H0b/H1b).
    Two tests per hypothesis pair — Bonferroni applies across both metrics.

    H0a/H0b: P99.9 RTT / IAJ distributions identical across stressor types
             → bootstrap CI on median; non-overlapping CI with baseline rejects H0.
    H1a/H1b: At least one stressor causes significant increase in P99.9 RTT / IAJ
             → bootstrap CI on Δmetric; CI excluding zero ⇒ significant.
    H2 (dual): CPU/IO cause higher ΔIAJ;  Cache/Memory cause higher ΔRTT_P99.9
             → separate bootstrap CIs on each contrast.
    Compliance check: worst P99.9 RTT vs 10 ms echo timeout.
    """
    # Pool all runs per stressor type across N levels
    stressor_runs = {}
    for cond in conditions.values():
        stressor_runs.setdefault(cond["stressor"], []).extend(cond["runs"])

    baseline_runs = stressor_runs.get("baseline", [])
    bl_p999       = _vals(baseline_runs, "rtl_p999_us")
    bl_iaj        = _vals(baseline_runs, "iaj_stddev_us")
    stressed_types = sorted(st for st in stressor_runs if st != "baseline")

    print("\n" + "=" * 80)
    print(f"STATISTICAL HYPOTHESIS TESTS  [P99.9 RTT + IAJ + Bootstrap {CONF_LEVEL*100:.0f}% CI]"
          f"{('  —  ' + label) if label else ''}")
    print(f"  Metrics: rtl_p999_us  (H0a/H1a)   iaj_stddev_us  (H0b/H1b)")
    print(f"  Method : percentile bootstrap  (N_boot = {N_BOOT:,},  CI = {CONF_LEVEL*100:.0f}%)")
    print("=" * 80)

    # ── H0a: Bootstrap CI on median P99.9 RTT ────────────────────────────────
    print(f"\n--- H₀a: P99.9 RTT distributions across stressor types ---")
    print(f"  {'Stressor':<16} {'Runs':>5}  {'Median P99.9 RTT':>17}  {f'{CONF_LEVEL*100:.0f}% CI':>26}  Overlaps baseline?")
    print(f"  {'-' * 78}")
    bl_p999_ci  = None
    p999_cis_h0 = {}
    for st in ["baseline"] + stressed_types:
        vals = _vals(stressor_runs.get(st, []), "rtl_p999_us")
        lbl  = STRESSOR_LABELS.get(st, st)
        if len(vals) < 2:
            print(f"  {lbl:<16} {'':>5}  insufficient data")
            continue
        lo, hi, med = _bootstrap_ci_single(vals)
        p999_cis_h0[st] = (lo, hi, med)
        if st == "baseline":
            bl_p999_ci = (lo, hi, med)
            status = "  (reference)"
        elif bl_p999_ci:
            overlaps = not (lo > bl_p999_ci[1] or hi < bl_p999_ci[0])
            status   = "  ✓ overlaps" if overlaps else "  ✗ no overlap  *"
        else:
            status = ""
        print(f"  {lbl:<16} {len(vals):>5}  {med:>14.1f} µs    [{lo:>8.1f},  {hi:>8.1f}] µs  {status}")
    if bl_p999_ci:
        rej = [STRESSOR_LABELS.get(st, st) for st in stressed_types
               if st in p999_cis_h0 and
               (p999_cis_h0[st][0] > bl_p999_ci[1] or p999_cis_h0[st][1] < bl_p999_ci[0])]
        print(f"\n  H₀a {'REJECTED → ' + ', '.join(rej) if rej else 'NOT REJECTED: all P99.9 RTT CIs overlap with baseline'}")

    # ── H0b: Bootstrap CI on median IAJ std dev ─────────────────────────────
    print(f"\n--- H₀b: IAJ std dev distributions across stressor types ---")
    print(f"  {'Stressor':<16} {'Runs':>5}  {'Median IAJ std':>15}  {f'{CONF_LEVEL*100:.0f}% CI':>26}  Overlaps baseline?")
    print(f"  {'-' * 76}")
    bl_iaj_ci  = None
    iaj_cis_h0 = {}
    for st in ["baseline"] + stressed_types:
        vals = _vals(stressor_runs.get(st, []), "iaj_stddev_us")
        lbl  = STRESSOR_LABELS.get(st, st)
        if len(vals) < 2:
            print(f"  {lbl:<16} {'':>5}  insufficient data")
            continue
        lo, hi, med = _bootstrap_ci_single(vals)
        iaj_cis_h0[st] = (lo, hi, med)
        if st == "baseline":
            bl_iaj_ci = (lo, hi, med)
            status = "  (reference)"
        elif bl_iaj_ci:
            overlaps = not (lo > bl_iaj_ci[1] or hi < bl_iaj_ci[0])
            status   = "  ✓ overlaps" if overlaps else "  ✗ no overlap  *"
        else:
            status = ""
        print(f"  {lbl:<16} {len(vals):>5}  {med:>12.1f} µs    [{lo:>8.1f},  {hi:>8.1f}] µs  {status}")
    if bl_iaj_ci:
        rej = [STRESSOR_LABELS.get(st, st) for st in stressed_types
               if st in iaj_cis_h0 and
               (iaj_cis_h0[st][0] > bl_iaj_ci[1] or iaj_cis_h0[st][1] < bl_iaj_ci[0])]
        print(f"\n  H₀b {'REJECTED → ' + ', '.join(rej) if rej else 'NOT REJECTED: all IAJ CIs overlap with baseline'}")

    # ── H1a: ΔRTT_P99.9 per stressor ───────────────────────────────────────────
    print(f"\n--- H₁a: ΔRTT_P99.9 vs baseline  (bootstrap {CONF_LEVEL*100:.0f}% CI) ---")
    print(f"  Significant (*) if CI excludes zero (lo > 0)\n")
    print(f"  {'Stressor':<16} {'ΔRTT_P99.9':>12}  {f'{CONF_LEVEL*100:.0f}% CI':>28}  Sig")
    print(f"  {'-' * 62}")
    for st in stressed_types:
        vals = _vals(stressor_runs[st], "rtl_p999_us")
        lbl  = STRESSOR_LABELS.get(st, st)
        if len(bl_p999) < 2 or len(vals) < 2:
            print(f"  {lbl:<16} insufficient data")
            continue
        lo, hi, delta = _bootstrap_ci_difference(bl_p999, vals)
        sig = "*" if lo > 0 else ("" if hi > 0 else "✗ negative")
        print(f"  {lbl:<16} {delta:>+11.1f}µs   [{lo:>+10.1f},  {hi:>+10.1f}]µs  {sig}")

    # ── H1b: ΔIAJ std dev per stressor ───────────────────────────────────────────
    print(f"\n--- H₁b: ΔIAJ std dev vs baseline  (bootstrap {CONF_LEVEL*100:.0f}% CI) ---")
    print(f"  Significant (*) if CI excludes zero (lo > 0)\n")
    print(f"  {'Stressor':<16} {'ΔIAJ stddev':>12}  {f'{CONF_LEVEL*100:.0f}% CI':>28}  Sig")
    print(f"  {'-' * 62}")
    for st in stressed_types:
        vals = _vals(stressor_runs[st], "iaj_stddev_us")
        lbl  = STRESSOR_LABELS.get(st, st)
        if len(bl_iaj) < 2 or len(vals) < 2:
            print(f"  {lbl:<16} insufficient data")
            continue
        lo, hi, delta = _bootstrap_ci_difference(bl_iaj, vals)
        sig = "*" if lo > 0 else ("" if hi > 0 else "✗ negative")
        print(f"  {lbl:<16} {delta:>+11.1f}µs   [{lo:>+10.1f},  {hi:>+10.1f}]µs  {sig}")

    # ── H2 dual: CPU/IO ΔIAJ vs Cache/Mem ΔIAJ;  Cache/Mem ΔRTT vs CPU/IO ΔRTT ──
    print("\n--- H₂ (dual): CPU/IO vs Cache/Memory for ΔIAJ and ΔRTT_P99.9 ---")
    print("  H₂ prediction: CPU/IO ΔIAJ > Cache/Mem ΔIAJ  (syscall scheduling pressure drives jitter)")
    print("                 Cache/Mem ΔRTT_P99.9 > CPU/IO ΔRTT_P99.9  (cache eviction elevates latency)\n")

    ci_runs  = [r for st in ["cpu", "io"]      if st in stressor_runs for r in stressor_runs[st]]
    cm_runs  = [r for st in ["cache", "stream"] if st in stressor_runs for r in stressor_runs[st]]
    ci_iaj   = _vals(ci_runs, "iaj_stddev_us")
    cm_iaj   = _vals(cm_runs, "iaj_stddev_us")
    ci_p999  = _vals(ci_runs, "rtl_p999_us")
    cm_p999  = _vals(cm_runs, "rtl_p999_us")

    if len(ci_iaj) >= 2 and len(cm_iaj) >= 2 and len(bl_iaj) >= 2:
        lo_ci_iaj, hi_ci_iaj, d_ci_iaj = _bootstrap_ci_difference(bl_iaj, ci_iaj)
        lo_cm_iaj, hi_cm_iaj, d_cm_iaj = _bootstrap_ci_difference(bl_iaj, cm_iaj)
        lo_iaj,    hi_iaj,    diff_iaj  = _bootstrap_ci_difference(cm_iaj, ci_iaj)
        print(f"  ΔIAJ CPU/IO       : {d_ci_iaj:>+.1f}µs   [{lo_ci_iaj:>+.1f},  {hi_ci_iaj:>+.1f}]")
        print(f"  ΔIAJ Cache/Mem    : {d_cm_iaj:>+.1f}µs   [{lo_cm_iaj:>+.1f},  {hi_cm_iaj:>+.1f}]")
        print(f"  CPU/IO − Cache/Mem : {diff_iaj:>+.1f}µs   [{lo_iaj:>+.1f},  {hi_iaj:>+.1f}]")
        print(f"  IAJ part → H₂ {'SUPPORTED' if lo_iaj > 0 else 'NOT SUPPORTED' if hi_iaj < 0 else 'INCONCLUSIVE'}: "
              f"CPU/IO ΔIAJ {'>' if lo_iaj > 0 else '≤'} Cache/Mem ΔIAJ")

    if len(ci_p999) >= 2 and len(cm_p999) >= 2 and len(bl_p999) >= 2:
        print()
        lo_ci_rtl, hi_ci_rtl, d_ci_rtl = _bootstrap_ci_difference(bl_p999, ci_p999)
        lo_cm_rtl, hi_cm_rtl, d_cm_rtl = _bootstrap_ci_difference(bl_p999, cm_p999)
        lo_rtl,    hi_rtl,    diff_rtl  = _bootstrap_ci_difference(ci_p999, cm_p999)
        print(f"  ΔRTT_P99.9 CPU/IO  : {d_ci_rtl:>+.1f}µs   [{lo_ci_rtl:>+.1f},  {hi_ci_rtl:>+.1f}]")
        print(f"  ΔRTT_P99.9 Cache/Mem: {d_cm_rtl:>+.1f}µs   [{lo_cm_rtl:>+.1f},  {hi_cm_rtl:>+.1f}]")
        print(f"  Cache/Mem − CPU/IO  : {diff_rtl:>+.1f}µs   [{lo_rtl:>+.1f},  {hi_rtl:>+.1f}]")
        print(f"  RTT part  → H₂ {'SUPPORTED' if lo_rtl > 0 else 'NOT SUPPORTED' if hi_rtl < 0 else 'INCONCLUSIVE'}: "
              f"Cache/Mem ΔRTT {'>' if lo_rtl > 0 else '≤'} CPU/IO ΔRTT")

    # ── Compliance check: worst P99.9 RTT vs echo timeout ─────────────────────
    print(f"\n--- Compliance check: P99.9 RTT vs echo timeout ({ECHO_TIMEOUT_US/1000:.0f} ms) ---")
    all_p999 = [(cname, max(_vals(cond["runs"], "rtl_p999_us"), default=0.0))
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


def test_cross_configuration(cv_conds, hicom_conds, stressor):
    """H3: Both ΔRTT_P99.9 and ΔIAJ are significantly larger in Config A
    (cross-VM) than in Config B (HI-only).

    Uses _bootstrap_ci_delta_diff independently for each metric.
    H3 fully confirmed when BOTH CIs exclude zero on the positive side.
    Also reports per-N direct comparisons for both metrics.
    """
    sep = "─" * 72
    print(f"\n{sep}")
    print(f"  Cross-Configuration Tests (H3)  [Bootstrap {CONF_LEVEL*100:.0f}% CI]  —  stressor: {stressor}")
    print(sep)

    # Separate stressor and baseline runs for each config
    cv_stress_runs = [r for cond in cv_conds.values()
                      if cond["stressor"] != "baseline" for r in cond["runs"]]
    cv_base_runs   = [r for cond in cv_conds.values()
                      if cond["stressor"] == "baseline" for r in cond["runs"]]
    hi_stress_runs = [r for cond in hicom_conds.values()
                      if cond["stressor"] != "baseline" for r in cond["runs"]]
    hi_base_runs   = [r for cond in hicom_conds.values()
                      if cond["stressor"] == "baseline" for r in cond["runs"]]

    cv_p999_st = _vals(cv_stress_runs, "rtl_p999_us")
    cv_p999_bl = _vals(cv_base_runs,   "rtl_p999_us")
    hi_p999_st = _vals(hi_stress_runs, "rtl_p999_us")
    hi_p999_bl = _vals(hi_base_runs,   "rtl_p999_us")

    cv_iaj_st  = _vals(cv_stress_runs, "iaj_stddev_us")
    cv_iaj_bl  = _vals(cv_base_runs,   "iaj_stddev_us")
    hi_iaj_st  = _vals(hi_stress_runs, "iaj_stddev_us")
    hi_iaj_bl  = _vals(hi_base_runs,   "iaj_stddev_us")

    result = {}

    # ─ H3 — RTT_P99.9 component ─────────────────────────────────────────────
    print(f"\n  H3 — RTT_P99.9:  (ΔRTT_A − ΔRTT_B)  (bootstrap {CONF_LEVEL*100:.0f}% CI)")
    print("  Confirmed if CI > 0  (cross-VM more affected than HI-only)\n")
    lo_rtl, hi_rtl, pt_rtl = _bootstrap_ci_delta_diff(cv_p999_st, cv_p999_bl,
                                                        hi_p999_st, hi_p999_bl)
    rtl_confirmed = False
    if lo_rtl == lo_rtl:  # not nan
        d_cv_rtl = (sum(cv_p999_st) / len(cv_p999_st) - sum(cv_p999_bl) / len(cv_p999_bl)) \
                   if cv_p999_st and cv_p999_bl else float("nan")
        d_hi_rtl = (sum(hi_p999_st) / len(hi_p999_st) - sum(hi_p999_bl) / len(hi_p999_bl)) \
                   if hi_p999_st and hi_p999_bl else float("nan")
        print(f"  ΔRTT_P99.9 cross-VM : {d_cv_rtl:>+.1f} µs")
        print(f"  ΔRTT_P99.9 HI-only  : {d_hi_rtl:>+.1f} µs")
        print(f"  ΔΔRTT (cv−hi)        : {pt_rtl:>+.1f} µs   {CONF_LEVEL*100:.0f}% CI [{lo_rtl:>+.1f},  {hi_rtl:>+.1f}] µs")
        rtl_confirmed = lo_rtl > 0
        print(f"  RTT component → {'CONFIRMED' if rtl_confirmed else 'REJECTED' if hi_rtl < 0 else 'INCONCLUSIVE'}")
        result["rtl_delta_diff"] = {"lo": lo_rtl, "hi": hi_rtl, "pt": pt_rtl}
    else:
        print("  Insufficient data for RTT component.")

    # ─ H3 — IAJ std dev component ──────────────────────────────────────────
    print(f"\n  H3 — IAJ std dev:  (ΔIAJ_A − ΔIAJ_B)  (bootstrap {CONF_LEVEL*100:.0f}% CI)")
    print("  Confirmed if CI > 0  (cross-VM IAJ more affected than HI-only)\n")
    lo_iaj, hi_iaj, pt_iaj = _bootstrap_ci_delta_diff(cv_iaj_st, cv_iaj_bl,
                                                        hi_iaj_st, hi_iaj_bl)
    iaj_confirmed = False
    if lo_iaj == lo_iaj:  # not nan
        d_cv_iaj = (sum(cv_iaj_st) / len(cv_iaj_st) - sum(cv_iaj_bl) / len(cv_iaj_bl)) \
                   if cv_iaj_st and cv_iaj_bl else float("nan")
        d_hi_iaj = (sum(hi_iaj_st) / len(hi_iaj_st) - sum(hi_iaj_bl) / len(hi_iaj_bl)) \
                   if hi_iaj_st and hi_iaj_bl else float("nan")
        print(f"  ΔIAJ cross-VM       : {d_cv_iaj:>+.1f} µs")
        print(f"  ΔIAJ HI-only        : {d_hi_iaj:>+.1f} µs")
        print(f"  ΔΔIAJ (cv−hi)        : {pt_iaj:>+.1f} µs   {CONF_LEVEL*100:.0f}% CI [{lo_iaj:>+.1f},  {hi_iaj:>+.1f}] µs")
        iaj_confirmed = lo_iaj > 0
        print(f"  IAJ component → {'CONFIRMED' if iaj_confirmed else 'REJECTED' if hi_iaj < 0 else 'INCONCLUSIVE'}")
        result["iaj_delta_diff"] = {"lo": lo_iaj, "hi": hi_iaj, "pt": pt_iaj}
    else:
        print("  Insufficient data for IAJ component.")

    # ─ Overall H3 verdict ───────────────────────────────────────────────────
    print()
    if rtl_confirmed and iaj_confirmed:
        print("  H3 FULLY CONFIRMED: both RTT_P99.9 and IAJ are significantly larger under cross-VM.")
    elif rtl_confirmed or iaj_confirmed:
        print(f"  H3 PARTIALLY CONFIRMED: {'RTT_P99.9' if rtl_confirmed else 'IAJ'} confirmed only.")
    else:
        print("  H3 NOT CONFIRMED: neither RTT nor IAJ component shows significant difference.")

    # ─ Per-N direct comparison table ──────────────────────────────────────
    print(f"\n  Per-N P99.9 RTT: cross-VM − HI-only  (bootstrap {CONF_LEVEL*100:.0f}% CI)")
    print(f"  {'N':>5}  {'cross-VM':>10}  {'HI-only':>10}  {'diff':>9}  {f'{CONF_LEVEL*100:.0f}% CI':>24}  Sig")
    print(f"  {'-' * 65}")
    cv_by_n = {v["n_workers"]: v for v in cv_conds.values()}
    hi_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    for n in sorted(set(cv_by_n) & set(hi_by_n)):
        cv_v = _vals(cv_by_n[n]["runs"], "rtl_p999_us")
        hi_v = _vals(hi_by_n[n]["runs"], "rtl_p999_us")
        cv_m = sorted(cv_v)[len(cv_v) // 2] if cv_v else float("nan")
        hi_m = sorted(hi_v)[len(hi_v) // 2] if hi_v else float("nan")
        if len(cv_v) >= 2 and len(hi_v) >= 2:
            lo_n, hi_n, pt_n = _bootstrap_ci_difference(hi_v, cv_v)
            sig = "*" if lo_n > 0 else ""
            print(f"  {n:>5}  {cv_m:>9.1f}µs  {hi_m:>9.1f}µs  {pt_n:>+8.1f}µs  [{lo_n:>+9.1f},  {hi_n:>+9.1f}]µs  {sig}")
        else:
            print(f"  {n:>5}  insufficient data")

    return result


# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"  Saved: {path}")


# ─── Per-configuration plots ──────────────────────────────────────────────────

def plot_mean_rtl(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    means = [_avg(c["runs"], "rtl_mean_us")             for c in sc]
    errs  = [_std_across_runs(c["runs"], "rtl_mean_us") for c in sc]
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=errs, fmt="o-", capsize=5,
                color=color, linewidth=2, markersize=7,
                label="mean \u00b1 cross-run std")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean rtl (\u00b5s)")
    ax.set_title(f"Mean rtl vs Load — {stressor}  (H₁)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_mean{suffix}.png"))
    plt.close(fig)


def plot_percentiles(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    p50s  = [_avg(c["runs"], "rtl_p50_us")  for c in sc]
    p95s  = [_avg(c["runs"], "rtl_p95_us")  for c in sc]
    p99s  = [_avg(c["runs"], "rtl_p99_us")  for c in sc]
    p999s = [_avg(c["runs"], "rtl_p999_us") for c in sc]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, p50s,  "o-", label="P50",   color="#4CAF50", linewidth=2)
    ax.plot(n_vals, p95s,  "D-", label="P95",   color="#2196F3", linewidth=2)
    ax.plot(n_vals, p99s,  "s-", label="P99",   color="#FF9800", linewidth=2)
    ax.plot(n_vals, p999s, "^-", label="P99.9", color="#F44336", linewidth=2)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTT (\u00b5s)")
    ax.set_title(f"RTT Percentiles vs Load — {stressor}  (H₁/H₂)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtt_percentiles{suffix}.png"))
    plt.close(fig)


def plot_boxplot(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels   = [str(n) for n in n_vals]
    box_data = [_vals(c["runs"], "rtl_mean_us") for c in sc]
    colors   = (["#607D8B"] +
                [STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)] * (len(sc) - 1))

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    bp = ax.boxplot(box_data, tick_labels=labels, patch_artist=True)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c); patch.set_alpha(0.7)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean RTT (\u00b5s)")
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
    ax.set_ylabel("Packet Loss Rate (%)")
    ax.set_title(f"Packet Loss Rate — {stressor}  (H₃: should be 0%)")
    ax.grid(axis="y", alpha=0.3)
    if max(rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No packet loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"loss_rate{suffix}.png"))
    plt.close(fig)


def plot_iaj_stddev(conditions, out, stressor, suffix="", label=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels  = [str(n) for n in n_vals]
    iaj_sds = [_avg(c["runs"], "iaj_stddev_us") for c in sc]
    color   = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, iaj_sds, color=color, alpha=0.8, edgecolor="black")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("IAJ Std Dev (\u00b5s)")
    ax.set_title(f"{label} Inter-Arrival Jitter vs Load — {stressor}  (H₁)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"{label}_iaj_stddev{suffix}.png"))
    plt.close(fig)


def plot_p999_trend(conditions, out, stressor, suffix=""):
    """P99.9 RTT vs N with bootstrap 99% CI  —  primary H0a/H1a metric plot."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    medians, ci_los, ci_his = [], [], []
    for c in sc:
        p999_vals = _vals(c["runs"], "rtl_p999_us")
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
    ax.set_title(f"P99.9 RTT vs Load — {stressor}  (H0a/H1a)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"p999_trend{suffix}.png"))
    plt.close(fig)


def plot_iaj_bootstrap_trend(conditions, out, stressor, suffix="", label=""):
    """Median IAJ std dev + bootstrap CI vs N  —  primary H0b/H1b metric plot."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    color = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    medians, ci_los, ci_his = [], [], []
    for c in sc:
        iaj_vals = _vals(c["runs"], "iaj_stddev_us")
        if len(iaj_vals) >= 2:
            lo, hi, med = _bootstrap_ci_single(iaj_vals)
        else:
            med = iaj_vals[0] if iaj_vals else float("nan")
            lo, hi = med, med
        medians.append(med)
        ci_los.append(med - lo)
        ci_his.append(hi - med)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, medians, yerr=[ci_los, ci_his],
                fmt="o-", capsize=5, color=color, linewidth=2, markersize=7,
                label=f"median IAJ std dev  ±  {CONF_LEVEL*100:.0f}% bootstrap CI")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("IAJ Std Dev (µs)")
    ax.set_title(f"{label} IAJ Std Dev vs Load — {stressor}  (H0b/H1b)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"{label}_iaj_bootstrap_trend{suffix}.png"))
    plt.close(fig)


# ─── Cross-configuration plots (H3) ───────────────────────────────────────────────────

def plot_overlay(cv_conds, hicom_conds, out, stressor):
    """Median P99.9 RTT + bootstrap CI for both configurations  —  primary H3 figure."""
    import matplotlib.pyplot as plt

    def _extract(conds):
        sc = sorted(conds.values(), key=lambda x: x["n_workers"])
        ns, meds, los, his = [], [], [], []
        for c in sc:
            p999_vals = _vals(c["runs"], "rtl_p999_us")
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

    cn, cm, c_lo, c_hi = _extract(cv_conds)
    hn, hm, h_lo, h_hi = _extract(hicom_conds)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(cn, cm, yerr=[c_lo, c_hi], fmt="o-",  capsize=5, color=CONF_A_COLOR,
                linewidth=2, markersize=7,
                label=f"Configuration A — cross-VM  (median P99.9 ± {CONF_LEVEL*100:.0f}% CI)")
    ax.errorbar(hn, hm, yerr=[h_lo, h_hi], fmt="s--", capsize=5, color=CONF_B_COLOR,
                linewidth=2, markersize=7,
                label=f"Configuration B — HI-only  (median P99.9 ± {CONF_LEVEL*100:.0f}% CI)")
    ax.axhline(y=ECHO_TIMEOUT_US, color="red", linestyle="--", linewidth=1,
               label=f"Echo timeout ({ECHO_TIMEOUT_US/1000:.0f} ms)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("P99.9 RTT (µs)")
    ax.set_title(f"Cross-Configuration P99.9 RTT Comparison — {stressor}  (H3)\n"
                 f"Vertical gap = cross-VM overhead  |  error bars = bootstrap {CONF_LEVEL*100:.0f}% CI")
    ax.set_xticks(sorted(set(cn) | set(hn)))
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"overlay_{stressor}.png"))
    plt.close(fig)


def plot_rtl_ratio(cv_conds, hicom_conds, out, stressor):
    """rtl ratio (HI-only / cross-VM) vs N \u2014 isolation effectiveness."""
    import matplotlib.pyplot as plt

    cv_by_n  = {v["n_workers"]: v for v in cv_conds.values()}
    hi_by_n  = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n = sorted(set(cv_by_n) & set(hi_by_n))

    ratios = []
    for n in common_n:
        cm = _avg(cv_by_n[n]["runs"], "rtl_mean_us")
        hm = _avg(hi_by_n[n]["runs"], "rtl_mean_us")
        ratios.append(hm / cm if cm > 0 else 0.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(common_n, ratios, "o-", color="#9C27B0", linewidth=2, markersize=7)
    ax.axhline(1.0, linestyle="--", color="grey", alpha=0.5,
               label="ratio = 1.0  (equal impact on both channels)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTT ratio  (Conf.B / Conf.A)")
    ax.set_title(f"Isolation Effectiveness — {stressor}  (H4)\n"
                 "Decreasing ratio: HI-only increasingly less affected")
    ax.set_xticks(common_n); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_ratio_{stressor}.png"))
    plt.close(fig)


def plot_side_by_side_boxplot(cv_conds, hicom_conds, out, stressor):
    """Side-by-side box plots for both configurations at each N."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping side-by-side boxplot]")
        return
    from matplotlib.patches import Patch

    cv_by_n  = {v["n_workers"]: v for v in cv_conds.values()}
    hi_by_n  = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n = sorted(set(cv_by_n) & set(hi_by_n))

    pos     = np.arange(len(common_n))
    width   = 0.35
    cv_data = [_vals(cv_by_n[n]["runs"], "rtl_mean_us") for n in common_n]
    hi_data = [_vals(hi_by_n[n]["runs"], "rtl_mean_us") for n in common_n]

    fig, ax = plt.subplots(figsize=(max(9, len(common_n) * 1.6), 5))
    bp1 = ax.boxplot(cv_data, positions=pos - width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)
    bp2 = ax.boxplot(hi_data, positions=pos + width / 2, widths=width,
                     patch_artist=True, manage_ticks=False)
    for p in bp1["boxes"]:
        p.set_facecolor(CONF_A_COLOR); p.set_alpha(0.6)
    for p in bp2["boxes"]:
        p.set_facecolor(CONF_B_COLOR); p.set_alpha(0.6)

    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean RTT (\u00b5s)")
    ax.set_title(f"RTT Distribution Comparison — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=CONF_A_COLOR, alpha=0.6, label="Configuration A — cross-VM"),
        Patch(facecolor=CONF_B_COLOR, alpha=0.6, label="Configuration B — HI-only"),
    ])
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"boxplot_comparison_{stressor}.png"))
    plt.close(fig)


def plot_loss_rate_comparison(cv_conds, hicom_conds, out, stressor):
    """Grouped bar chart of packet loss rate for both configurations."""
    import matplotlib.pyplot as plt
    try:
        import numpy as np
    except ImportError:
        print("  [numpy unavailable — skipping loss rate comparison]")
        return
    from matplotlib.patches import Patch

    cv_by_n  = {v["n_workers"]: v for v in cv_conds.values()}
    hi_by_n  = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n = sorted(set(cv_by_n) & set(hi_by_n))

    def _loss_pct(cond):
        sent = sum(r.get("n_sent", 0) for r in cond["runs"])
        lost = sum(r.get("n_lost", 0) for r in cond["runs"])
        return lost / sent * 100 if sent else 0.0

    cv_rates = [_loss_pct(cv_by_n[n]) for n in common_n]
    hi_rates = [_loss_pct(hi_by_n[n]) for n in common_n]

    pos   = np.arange(len(common_n))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(7, len(common_n) * 1.6), 5))
    ax.bar(pos - width / 2, cv_rates, width, color=CONF_A_COLOR, alpha=0.8,
           edgecolor="black", label="Configuration A \u2014 cross-VM")
    ax.bar(pos + width / 2, hi_rates, width, color=CONF_B_COLOR, alpha=0.8,
           edgecolor="black", label="Configuration B \u2014 HI-only")
    ax.set_xticks(pos)
    ax.set_xticklabels([str(n) for n in common_n])
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Packet Loss Rate (%)")
    ax.set_title(f"Packet Loss Rate — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=CONF_A_COLOR, alpha=0.8, label="Configuration A — cross-VM"),
        Patch(facecolor=CONF_B_COLOR, alpha=0.8, label="Configuration B — HI-only"),
    ])
    ax.grid(axis="y", alpha=0.3)
    if max(cv_rates + hi_rates, default=0) == 0:
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No packet loss detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=13, color="green", fontweight="bold")
    fig.tight_layout()
    _save(fig, os.path.join(out, f"loss_rate_comparison_{stressor}.png"))
    plt.close(fig)


# ─── UDP vs TCP comparison ────────────────────────────────────────────────────

def plot_udp_vs_tcp(udp_conds, tcp_conds, out, stressor):
    """Mean rtl +/- std for UDP and TCP on same axes."""
    import matplotlib.pyplot as plt

    def _extract(conds):
        sc = sorted(conds.values(), key=lambda x: x["n_workers"])
        return (
            [c["n_workers"]                           for c in sc],
            [_avg(c["runs"], "rtl_mean_us")             for c in sc],
            [_std_across_runs(c["runs"], "rtl_mean_us") for c in sc],
        )

    un, um, us = _extract(udp_conds)
    tn, tm, ts = _extract(tcp_conds)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(un, um, yerr=us, fmt="o-",  capsize=5, color="#2196F3",
                linewidth=2, markersize=7, label="UDP")
    ax.errorbar(tn, tm, yerr=ts, fmt="s--", capsize=5, color="#FF9800",
                linewidth=2, markersize=7, label="TCP")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean RTT (\u00b5s)")
    ax.set_title(f"UDP vs TCP RTT Comparison \u2014 {stressor}")
    ax.set_xticks(sorted(set(un) | set(tn)))
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"udp_vs_tcp_{stressor}.png"))
    plt.close(fig)

# ─── Time-series parsing ─────────────────────────────────────────────────────

_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_VM_PREFIX_RE = re.compile(r"^vm-\S+\s*\|\s*")


def _clean_serial_line(line):
    """Strip ANSI codes and hypervisor serial mux prefixes."""
    line = _ANSI_RE.sub("", line)
    line = _VM_PREFIX_RE.sub("", line)
    return line


def parse_run_csv(filepath):
    """Extract per-probe RTT and IAJ from the CSV block in a run_XX.txt file.

    The block is delimited by ===CSV_START=== / ===CSV_END=== and has columns:
      index, rtl_ns, iaj_ns

    Returns a list of dicts with keys 'index', 'rtl_us', 'iaj_us'
    (values converted from nanoseconds to microseconds).
    """
    try:
        text = Path(filepath).read_text(errors="replace")
    except FileNotFoundError:
        return []

    m = re.search(r"===CSV_START===\n(.*?)===CSV_END===", text, re.DOTALL)
    if not m:
        return []

    rows = []
    try:
        reader = csv.DictReader(io.StringIO(m.group(1)))
        for row in reader:
            try:
                rows.append({
                    "index":  int(row["index"]),
                    "rtl_us": float(row["rtl_ns"]) / 1000.0,
                    "iaj_us": float(row["iaj_ns"]) / 1000.0,
                })
            except (KeyError, ValueError):
                continue
    except Exception:
        pass
    return rows


def parse_vmstat(filepath):
    """Parse a vmstat output file into per-second rows.

    Handles repeated headers that vmstat prints
    Returns a list of dicts with vmstat columns
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
                    continue
    except FileNotFoundError:
        pass
    return rows


def load_timeseries(results_dir):
    """Load per-probe CSV and vmstat data for every run in every condition.

    Returns {condition_name: [(probes, vmstat_rows, run_num), ...]}.
    """
    ts_data = {}
    for d in sorted(Path(results_dir).iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name == "plots":
            continue
        name = d.name
        sep  = name.rfind("_")
        if sep != -1 and re.fullmatch(r"n\d+", name[sep + 1:]):
            pass  # standard format
        elif sep != -1:
            continue

        run_entries = []
        run_idx = 1
        while True:
            run_num  = f"{run_idx:02d}"
            run_file  = d / f"run_{run_num}.txt"
            if not run_file.exists():
                break
            cycles_file = d / f"run_{run_num}.csv"
            if not cycles_file.exists():
                cycles_file = d / f"run_{run_num}_cycles.csv"
                if not cycles_file.exists():
                    cycles_file = d / f"run_{run_idx}_cycles.csv"
                    if not cycles_file.exists():
                        cycles_file = d / f"run_{run_idx}.csv"
            vmstat_file = d / f"run_{run_num}_vmstat.csv"
            if not vmstat_file.exists():
                vmstat_file = d / f"run_{run_idx}_vmstat.csv"
            probes = parse_run_csv(run_file)
            vmstat = parse_vmstat(vmstat_file) if vmstat_file.exists() else []
            run_entries.append((probes, vmstat, run_num))
            run_idx += 1

        if run_entries:
            ts_data[name] = run_entries

    return ts_data


# ─── Time-series plots ────────────────────────────────────────────────────────

def generate_timeseries_plots(ts_data, output_dir):
    """Generate per-run time-series plots (RTT, IAJ, and optional vmstat)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed. Skipping time-series plots.")
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

        for probes, vmstat, run_num in run_entries:
            has_vmstat = bool(vmstat)
            # RTT panel + IAJ panel + optional vmstat panels
            nrows = 2 + (2 if has_vmstat else 0)
            fig, axes = plt.subplots(nrows, 1, figsize=(14, 4 * nrows),
                                     sharex=False)

            xs       = [p["index"]  for p in probes]
            rtl_vals = [p["rtl_us"] for p in probes]
            iaj_vals = [p["iaj_us"] for p in probes]

            # ── Panel 0: RTT time-series ────────────────────────────
            ax_rtl = axes[0]
            ax_rtl.plot(xs, rtl_vals, linewidth=0.8, color=color, label="RTT")
            ax_rtl.axhline(y=ECHO_TIMEOUT_US, color="red", linestyle="--",
                           linewidth=1,
                           label=f"Echo timeout ({ECHO_TIMEOUT_US / 1000:.0f} ms)")
            ax_rtl.set_ylabel("RTT (\u00b5s)")
            ax_rtl.set_xlabel("Probe #")
            ax_rtl.set_title(f"{cond_name} \u2014 Run {run_num}")
            ax_rtl.legend(loc="upper right", fontsize=8)
            ax_rtl.grid(alpha=0.3)

            # ── Panel 1: IAJ time-series ────────────────────────────
            ax_iaj = axes[1]
            ax_iaj.plot(xs, iaj_vals, linewidth=0.8, color="#9C27B0", label="IAJ")
            ax_iaj.set_ylabel("IAJ (\u00b5s)")
            ax_iaj.set_title(f"{cond_name} \u2014 Run {run_num}")
            ax_iaj.set_xlabel("Probe #")
            ax_iaj.legend(loc="upper right", fontsize=8)
            ax_iaj.grid(alpha=0.3)
            if not has_vmstat:
                ax_iaj.set_xlabel("Probe #")

            if has_vmstat:
                vm_ts = [r["timestamp_s"] for r in vmstat]

                # ── Panel 2: CPU breakdown (us, sy, id) ─────────────────
                ax_cpu = axes[2]
                cpu_us = [r.get("us", 0) for r in vmstat]
                cpu_sy = [r.get("sy", 0) for r in vmstat]
                cpu_id = [r.get("id", 0) for r in vmstat]
                ax_cpu.stackplot(vm_ts, cpu_us, cpu_sy, cpu_id,
                                 tick_labels=["User (us)", "System (sy)", "Idle (id)"],
                                 colors=["#FF9800", "#F44336", "#E0E0E0"], alpha=0.8)
                ax_cpu.set_ylabel("CPU %")
                ax_cpu.set_xlabel("Time (s)")
                ax_cpu.set_ylim(0, 105)
                ax_cpu.legend(loc="upper right", fontsize=8)
                ax_cpu.grid(alpha=0.3)

                # ── Panel 3: System metrics (in, cs) ──────────────────
                ax_sys       = axes[3]
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

                lines1, labs1 = ax_sys.get_legend_handles_labels()
                lines2, labs2 = ax_cs.get_legend_handles_labels()
                ax_sys.legend(lines1 + lines2, labs1 + labs2,
                              loc="upper right", fontsize=8)
                ax_sys.set_xlabel("Time (s)")
                ax_sys.grid(alpha=0.3)

            fig.tight_layout()
            fname = os.path.join(output_dir,
                                 f"timeseries_{cond_name}_run{run_num}.png")
            fig.savefig(fname, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"  Saved: {fname}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scenario 3 rtl analysis \u2014 plots and statistical tests"
    )
    parser.add_argument("-r", "--results-dir", required=True,
                        help="Configuration A (cross-VM, UDP) results directory")
    parser.add_argument("--tcp-dir", default=None,
                        help="Configuration A TCP results dir \u2014 enables UDP vs TCP comparison")
    parser.add_argument("--hicom-dir", default=None,
                        help="Configuration B (HI-only) results dir \u2014 enables H4 cross-configuration analysis")
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
    print("  Scenario 3 Analysis \u2014 Network Communication Determinism")
    print("=" * 62)
    print(f"  Configuration A (UDP):  {args.results_dir}")
    if args.tcp_dir:
        print(f"  Configuration A (TCP):  {args.tcp_dir}")
    if args.hicom_dir:
        print(f"  Configuration B:        {args.hicom_dir}")
    print(f"  Output:                 {out}")

    # ── Load Configuration A (UDP) ─────────────────────────────────────────
    udp_all = discover_conditions(args.results_dir)
    if not udp_all:
        sys.exit(
            f"ERROR: no conditions found in {args.results_dir}\n"
            "Expected subdirs like: baseline_n0  cpu_n4  worst-case_n32"
        )
    print(f"udp_all keys: {list(udp_all.keys())}")
    s_types = stressor_types(udp_all)
    total   = sum(len(v["runs"]) for v in udp_all.values())
    print(f"\nConfiguration A (UDP): {total} runs, {len(udp_all)} conditions, "
          f"stressors: {', '.join(s_types)}")
    print_summary_table(udp_all, label="Configuration A \u2014 cross-VM (UDP)")

    to_plot = [args.stressor] if args.stressor else s_types

    # Statistical tests (H0\u2013H3)
    run_statistical_tests(udp_all, label="Configuration A \u2014 UDP")

     # Time-series plots (per-run RTT + IAJ + optional vmstat)
    # ts_data = load_timeseries(args.results_dir)
    # if ts_data:
    #     ts_runs = sum(len(runs) for runs in ts_data.values())
    #     print(f"\nLoaded time-series data for {ts_runs} runs.")
    #     print("Generating time-series plots...")
    #     generate_timeseries_plots(ts_data, out)


    print("\nConfiguration A (UDP) plots...")
    for st in to_plot:
        conds = for_stressor(udp_all, st)
        if len(conds) < 2:
            print(f"  [{st}: fewer than 2 conditions \u2014 skipped]")
            continue
        sfx = f"_udp_{st}"
        plot_p999_trend(conds,          out, st, sfx)
        plot_iaj_bootstrap_trend(conds, out, st, sfx, "conf_A")
        plot_mean_rtl(conds,            out, st, sfx)
        plot_percentiles(conds,         out, st, sfx)
        plot_boxplot(conds,             out, st, sfx)
        plot_loss_rate(conds,           out, st, sfx)
        plot_iaj_stddev(conds,          out, st, sfx, "conf_A")

    # ── Configuration A TCP ────────────────────────────────────────────────
    tcp_all = None
    if args.tcp_dir:
        tcp_all = discover_conditions(args.tcp_dir)
        if not tcp_all:
            print(f"\nWARNING: no conditions found in {args.tcp_dir}")
        else:
            tcp_types = stressor_types(tcp_all)
            tcp_total = sum(len(v["runs"]) for v in tcp_all.values())
            print(f"\nConfiguration A (TCP): {tcp_total} runs, {len(tcp_all)} conditions, "
                  f"stressors: {', '.join(tcp_types)}")
            print_summary_table(tcp_all, label="Configuration A \u2014 cross-VM (TCP)")

            run_statistical_tests(tcp_all, label="Configuration A \u2014 TCP")

            print("\nConfiguration A (TCP) plots...")
            for st in ([args.stressor] if args.stressor else tcp_types):
                conds = for_stressor(tcp_all, st)
                if len(conds) < 2:
                    continue
                sfx = f"_tcp_{st}"
                plot_p999_trend(conds,          out, st, sfx)
                plot_iaj_bootstrap_trend(conds, out, st, sfx, "conf_A")
                plot_mean_rtl(conds,            out, st, sfx)
                plot_percentiles(conds,         out, st, sfx)
                plot_loss_rate(conds,           out, st, sfx)

            # UDP vs TCP overlay per stressor
            cv_stressors = [s for s in to_plot
                            if any(v["stressor"] == s for v in tcp_all.values())]
            if cv_stressors:
                print("\nUDP vs TCP comparison plots...")
                for st in cv_stressors:
                    u_conds = for_stressor(udp_all, st)
                    t_conds = for_stressor(tcp_all, st)
                    if len(u_conds) >= 2 and len(t_conds) >= 2:
                        plot_udp_vs_tcp(u_conds, t_conds, out, st)

    # ── Configuration B and cross-configuration (H4) ──────────────────────
    if args.hicom_dir:
        hicom_all = discover_conditions(args.hicom_dir)

        if not hicom_all:
            print(f"\nWARNING: no conditions found in {args.hicom_dir}")
        else:
            h_types = stressor_types(hicom_all)
            h_total = sum(len(v["runs"]) for v in hicom_all.values())
            print(f"\nConfiguration B: {h_total} runs, {len(hicom_all)} conditions, "
                  f"stressors: {', '.join(h_types)}")
            print_summary_table(hicom_all, label="Configuration B \u2014 HI-only")

            run_statistical_tests(hicom_all, label="Configuration B")

            cv_stressors = [s for s in to_plot
                            if any(v["stressor"] == s for v in hicom_all.values())]

            if cv_stressors:
                print("\nCross-configuration plots and tests (H4)...")
            for st in cv_stressors:
                cv_conds = for_stressor(udp_all, st)
                hi_conds = for_stressor(hicom_all, st)
                if len(cv_conds) < 2 or len(hi_conds) < 2:
                    continue
                plot_overlay(cv_conds, hi_conds, out, st)
                plot_rtl_ratio(cv_conds, hi_conds, out, st)
                plot_side_by_side_boxplot(cv_conds, hi_conds, out, st)
                plot_loss_rate_comparison(cv_conds, hi_conds, out, st)
                test_cross_configuration(cv_conds, hi_conds, st)

            # Standalone Configuration B plots
            print("\nConfiguration B standalone plots...")
            for st in (cv_stressors or h_types):
                h_conds = for_stressor(hicom_all, st)
                if len(h_conds) < 2:
                    continue
                sfx = f"_hicom_{st}"
                plot_p999_trend(h_conds,          out, st, sfx)
                plot_iaj_bootstrap_trend(h_conds, out, st, sfx, "conf_B")
                plot_mean_rtl(h_conds,            out, st, sfx)
                plot_iaj_stddev(h_conds,          out, st, sfx, "conf_B")

    print(f"\nDone.  Plots saved to: {out}")




if __name__ == "__main__":
    main()
