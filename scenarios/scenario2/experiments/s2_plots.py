#!/usr/bin/env python3
"""
Scenario 2 Analysis — Inter-VM Communication Latency and Reliability

Parses run_XX.txt files produced by s2_orchestrator.sh / s2hv_orchestrator.sh,
prints summary tables, runs formal statistical tests, and generates plots for
every hypothesis in the experiment design (H0-H5, H4 payload size).

Usage
-----
# Variant A only:
  python3 s2_plots.py -r results/2026-05-18_14:00

# Filter to one stressor (recommended for N-trend analysis):
  python3 s2_plots.py -r results/2026-05-18_14:00 --stressor cache

# Variant A + B cross-variant (H5):
  python3 s2_plots.py -r results/... --hicom-dir results_hicom/...

# H4 payload size (secondary experiment):
  python3 s2_plots.py -r results/... --h4-dir results_s2_h4

Condition directory naming (from s2_orchestrator.sh):
  {stressor}_{n{workers}}   e.g.  baseline_n0  cache_n4  worst-case_n32

H4 directory naming:
  {size}B   e.g.  16B  64B  512B  3072B
"""

import os
import re
import sys
import argparse
from pathlib import Path

# ─── Thresholds (scenario2_design.md §7) ──────────────────────────────────────

DEGRADATION_P99_FACTOR    = 3.0   # H3 acceptance: P99 <= 3x baseline
DEGRADATION_JITTER_FACTOR = 2.0   # H3 acceptance: jitter <= 2x baseline
SPEARMAN_RHO_THRESHOLD    = 0.8   # |rho| > 0.8 to confirm H1
ALPHA                     = 0.05  # significance level

STRESSOR_PALETTE = {
    "baseline":   "#607D8B",
    "cpu":        "#2196F3",
    "cache":      "#FF9800",
    "stream":     "#4CAF50",
    "io":         "#9C27B0",
    "shm":        "#00BCD4",
    "vm":         "#795548",
    "atomic":     "#E91E63",
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
    _re(r"Mean:\s+([0-9.]+)\s+Std dev:",                float, "rtl_mean_us")
    _re(r"Mean:\s+[0-9.]+\s+Std dev:\s+([0-9.]+)",      float, "rtl_stddev_us")
    _re(r"Min:\s+([0-9.]+)\s+Max:",                     float, "rtl_min_us")
    _re(r"Min:\s+[0-9.]+\s+Max:\s+([0-9.]+)",           float, "rtl_max_us")
    _re(r"P50:\s+([0-9.]+)\s+P95:",                     float, "rtl_p50_us")
    _re(r"P50:\s+[0-9.]+\s+P95:\s+([0-9.]+)",           float, "rtl_p95_us")
    _re(r"P99:\s+([0-9.]+)\s+P99\.9:",                  float, "rtl_p99_us")
    _re(r"P99:\s+[0-9.]+\s+P99\.9:\s+([0-9.]+)",        float, "rtl_p999_us")
    _re(r"Throughput:\s+([0-9.]+)\s+msg/s",              float, "throughput")

    return s if s else None


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
        if sep == -1:
            continue
        stressor   = name[:sep]
        worker_key = name[sep + 1:]
        if not re.fullmatch(r"n\d+", worker_key):
            continue
        n_workers = int(worker_key[1:])

        runs = [parse_run_file(f) for f in sorted(d.glob("run_*.txt"))]
        runs = [r for r in runs if r]
        if runs:
            conditions[name] = {
                "stressor":  stressor,
                "n_workers": n_workers,
                "runs":      runs,
            }

    return conditions


def load_h4_results(h4_dir):
    """
    Load H4 payload-size results.
    Expected: h4_dir/{N}B/run_XX.txt   e.g. 16B/ 64B/ 512B/ 3072B/

    Returns dict[payload_bytes_int] = list[run_dict]
    """
    results = {}
    for d in sorted(Path(h4_dir).iterdir()):
        if not d.is_dir():
            continue
        m = re.fullmatch(r"(\d+)[Bb]", d.name)
        if not m:
            continue
        payload = int(m.group(1))
        runs    = [parse_run_file(f) for f in sorted(d.glob("run_*.txt"))]
        runs    = [r for r in runs if r]
        if runs:
            results[payload] = runs
    return results

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
    if include_baseline and stressor != "baseline" and "baseline_n0" in conditions:
        sel = {"baseline_n0": conditions["baseline_n0"], **sel}
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
        f"{'Lost%':>6}  {'MeanRTL':>9} {'StdDev':>8} "
        f"{'P99':>9} {'P99.9':>9} {'MaxRTL':>9} {'Thput':>7}"
    )
    print("-" * W)

    baseline_p99 = baseline_jitter = None

    for name, cond in sorted(conditions.items(),
                              key=lambda x: (x[1]["stressor"], x[1]["n_workers"])):
        runs       = cond["runs"]
        total_sent = sum(r.get("n_sent", 0) for r in runs)
        total_lost = sum(r.get("n_lost", 0) for r in runs)
        loss_pct   = total_lost / total_sent * 100 if total_sent else 0.0

        avg_mean  = _avg(runs, "rtl_mean_us")
        avg_std   = _avg(runs, "rtl_stddev_us")
        avg_p99   = _avg(runs, "rtl_p99_us")
        avg_p999  = _avg(runs, "rtl_p999_us")
        worst_max = max(_vals(runs, "rtl_max_us"), default=0.0)
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
    print("  RTL in µs.  Throughput in msg/s.")
    if baseline_p99:
        print(f"  P99! = P99 > {DEGRADATION_P99_FACTOR}x baseline "
              f"({baseline_p99:.1f} µs threshold: {baseline_p99 * DEGRADATION_P99_FACTOR:.1f} µs)")
    if baseline_jitter:
        print(f"  J!   = jitter > {DEGRADATION_JITTER_FACTOR}x baseline "
              f"({baseline_jitter:.1f} µs threshold: {baseline_jitter * DEGRADATION_JITTER_FACTOR:.1f} µs)")

# ─── Statistical tests ────────────────────────────────────────────────────────

def _scipy():
    try:
        from scipy import stats
        return stats
    except ImportError:
        return None


def test_per_variant(conditions, label=""):
    """
    H0:  Kruskal-Wallis — load has no significant effect on RTL.
    H1:  Spearman rho  — mean RTL and jitter increase monotonically with N.
    H2:  P99.9/mean ratio trend — tail grows faster than mean.

    Prints results; returns dict for downstream use.
    """
    sp = _scipy()
    sep = "─" * 62
    print(f"\n{sep}")
    print(f"  Statistical Tests{('  —  ' + label) if label else ''}")
    print(sep)

    sc      = sorted(conditions.values(), key=lambda x: x["n_workers"])
    n_vals  = [c["n_workers"]                  for c in sc]
    means   = [_avg(c["runs"], "rtl_mean_us")  for c in sc]
    jitters = [_avg(c["runs"], "rtl_stddev_us") for c in sc]
    p999s   = [_avg(c["runs"], "rtl_p999_us")  for c in sc]
    groups  = [_vals(c["runs"], "rtl_mean_us") for c in sc]
    groups  = [g for g in groups if len(g) >= 2]

    out = {}

    if not sp:
        print("  [scipy not available — pip3 install scipy]")
        return out

    # H0: Kruskal-Wallis
    if len(groups) >= 2:
        kw_h, kw_p = sp.kruskal(*groups)
        verdict = ("H0 REJECTED  [load has significant effect]"
                   if kw_p < ALPHA
                   else "H0 not rejected  (no significant effect detected)")
        print(f"  Kruskal-Wallis:       H = {kw_h:.3f},  p = {kw_p:.4f}")
        print(f"    -> {verdict}")
        out["kruskal"] = {"H": kw_h, "p": kw_p}

    # H1: Spearman rho
    if len(n_vals) >= 3:
        rho_m, p_m = sp.spearmanr(n_vals, means)
        rho_j, p_j = sp.spearmanr(n_vals, jitters)
        h1_ok = abs(rho_m) >= SPEARMAN_RHO_THRESHOLD and p_m < ALPHA
        print(f"  Spearman rho (mean):  rho = {rho_m:+.3f},  p = {p_m:.4f}"
              f"  ->  {'H1 CONFIRMED' if h1_ok else 'H1 not confirmed'}")
        print(f"  Spearman rho (jitter):rho = {rho_j:+.3f},  p = {p_j:.4f}")
        out["spearman_mean"]   = {"rho": rho_m, "p": p_m}
        out["spearman_jitter"] = {"rho": rho_j, "p": p_j}

        lr   = sp.linregress(n_vals, means)
        ci95 = 1.96 * lr.stderr
        print(f"  Linear slope:         {lr.slope:.3f} +/- {ci95:.3f} µs/worker"
              f"  (R2 = {lr.rvalue**2:.3f},  p = {lr.pvalue:.4f})")
        out["slope"] = {"slope": lr.slope, "ci": ci95,
                        "r2": lr.rvalue ** 2, "p": lr.pvalue}

    # H2: P99.9/mean ratio trend
    ratios   = [p / m if m > 0 else 0.0 for p, m in zip(p999s, means)]
    monotone = all(ratios[i] <= ratios[i + 1] for i in range(len(ratios) - 1))
    print(f"  P99.9/mean ratios:    {[round(r, 2) for r in ratios]}")
    print(f"    -> H2 {'CONFIRMED  [ratio grows monotonically]' if monotone else 'not confirmed (non-monotone)'}")
    out["tail_ratios"] = {"ratios": ratios, "n_vals": n_vals, "monotone": monotone}

    return out


def test_cross_variant(proxy_conds, hicom_conds, stressor):
    """
    H5:  Wilcoxon on per-condition medians — hicom less affected than proxy.
    H5a: Kruskal-Wallis on hicom alone — load has no effect on hicom RTL.
    Plus slope comparison with 95% CIs.
    """
    sp  = _scipy()
    sep = "─" * 62
    print(f"\n{sep}")
    print(f"  Cross-Variant Tests (H5)  —  stressor: {stressor}")
    print(sep)

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n   = sorted(set(proxy_by_n) & set(hicom_by_n))
    out = {"common_n": common_n}

    if len(common_n) < 3:
        print("  [< 3 overlapping N values — tests skipped]")
        return out

    p_meds  = [_median(proxy_by_n[n]["runs"], "rtl_mean_us") for n in common_n]
    h_meds  = [_median(hicom_by_n[n]["runs"],  "rtl_mean_us") for n in common_n]
    p_means = [_avg(proxy_by_n[n]["runs"],     "rtl_mean_us") for n in common_n]
    h_means = [_avg(hicom_by_n[n]["runs"],     "rtl_mean_us") for n in common_n]

    if not sp:
        print("  [scipy not available — pip3 install scipy]")
        return out

    # Wilcoxon
    wil_s, wil_p = sp.wilcoxon(p_meds, h_meds)
    h5_ok = wil_p < ALPHA
    print(f"  Wilcoxon (medians):   W = {wil_s:.1f},  p = {wil_p:.4f}")
    print(f"    -> H5 {'CONFIRMED  [variants differ significantly]' if h5_ok else 'not confirmed (no significant difference)'}")
    out["wilcoxon"] = {"W": wil_s, "p": wil_p}

    # Slope comparison with 95% CIs
    lr_p  = sp.linregress(common_n, p_means)
    lr_h  = sp.linregress(common_n, h_means)
    ci_p  = 1.96 * lr_p.stderr
    ci_h  = 1.96 * lr_h.stderr
    overlap = not (
        lr_p.slope - ci_p > lr_h.slope + ci_h or
        lr_h.slope - ci_h > lr_p.slope + ci_p
    )
    print(f"  Proxy slope:          {lr_p.slope:.3f} +/- {ci_p:.3f} µs/worker")
    print(f"  hicom slope:          {lr_h.slope:.3f} +/- {ci_h:.3f} µs/worker")
    print(f"    -> {'H5 CONFIRMED by slope  [non-overlapping 95% CIs]' if not overlap else 'slope CIs overlap — inconclusive'}")
    out["slopes"] = {
        "proxy": {"slope": lr_p.slope, "ci": ci_p, "r2": lr_p.rvalue ** 2},
        "hicom": {"slope": lr_h.slope, "ci": ci_h, "r2": lr_h.rvalue ** 2},
    }

    # H5a: Kruskal on hicom only
    h_groups = [_vals(hicom_by_n[n]["runs"], "rtl_mean_us") for n in common_n]
    h_groups = [g for g in h_groups if len(g) >= 2]
    if len(h_groups) >= 2:
        kw_h, kw_p = sp.kruskal(*h_groups)
        h5a_ok = kw_p >= ALPHA
        print(f"  hicom Kruskal-Wallis: H = {kw_h:.3f},  p = {kw_p:.4f}")
        print(f"    -> H5a {'CONFIRMED  [hicom unaffected by load]' if h5a_ok else 'REJECTED  (hicom RTL changes with load)'}")
        out["hicom_kruskal"] = {"H": kw_h, "p": kw_p}

    return out

# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"  Saved: {path}")

def _sorted_sc(conditions):
    """Return (n_vals_list, sorted_cond_list)."""
    sc = sorted(conditions.values(), key=lambda x: x["n_workers"])
    return [c["n_workers"] for c in sc], sc

# ─── Per-variant plots ────────────────────────────────────────────────────────

def plot_mean_rtl(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    means  = [_avg(c["runs"], "rtl_mean_us")             for c in sc]
    errs   = [_std_across_runs(c["runs"], "rtl_mean_us") for c in sc]
    color  = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(n_vals, means, yerr=errs, fmt="o-", capsize=5,
                color=color, linewidth=2, markersize=7,
                label="mean ± cross-run std")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean RTL (µs)")
    ax.set_title(f"Mean RTL vs Load — {stressor}  (H1)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_mean{suffix}.png"))
    plt.close(fig)


def plot_percentiles(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    p50s  = [_avg(c["runs"], "rtl_p50_us")  for c in sc]
    p99s  = [_avg(c["runs"], "rtl_p99_us")  for c in sc]
    p999s = [_avg(c["runs"], "rtl_p999_us") for c in sc]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, p50s,  "o-", label="P50",   color="#4CAF50", linewidth=2)
    ax.plot(n_vals, p99s,  "s-", label="P99",   color="#FF9800", linewidth=2)
    ax.plot(n_vals, p999s, "^-", label="P99.9", color="#F44336", linewidth=2)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTL (µs)")
    ax.set_title(f"RTL Percentiles vs Load — {stressor}  (H2)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_percentiles{suffix}.png"))
    plt.close(fig)


def plot_tail_ratio(conditions, out, stressor, suffix=""):
    """P99.9/mean ratio per condition — direct evidence for H2."""
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    means  = [_avg(c["runs"], "rtl_mean_us")  for c in sc]
    p999s  = [_avg(c["runs"], "rtl_p999_us")  for c in sc]
    ratios = [p / m if m > 0 else 0.0 for p, m in zip(p999s, means)]
    color  = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(n_vals, ratios, "o-", color=color, linewidth=2, markersize=7)
    if ratios:
        ax.axhline(ratios[0], linestyle="--", color="grey", alpha=0.6,
                   label=f"Baseline ratio ({ratios[0]:.1f})")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("P99.9 / Mean RTL")
    ax.set_title(f"Tail Growth Ratio vs Load — {stressor}  (H2: should increase)")
    ax.set_xticks(n_vals); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"tail_ratio{suffix}.png"))
    plt.close(fig)


def plot_boxplot(conditions, out, stressor, suffix=""):
    import matplotlib.pyplot as plt
    n_vals, sc = _sorted_sc(conditions)
    labels   = [str(n) for n in n_vals]
    box_data = [_vals(c["runs"], "rtl_mean_us") for c in sc]
    colors   = (["#607D8B"] +
                [STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)] * (len(sc) - 1))

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    bp = ax.boxplot(box_data, labels=labels, patch_artist=True)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c); patch.set_alpha(0.7)
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Per-Run Mean RTL (µs)")
    ax.set_title(f"RTL Distribution per Condition — {stressor}  (run-to-run variance)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_boxplot{suffix}.png"))
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
    jitters = [_avg(c["runs"], "rtl_stddev_us") for c in sc]
    color   = STRESSOR_PALETTE.get(stressor, FALLBACK_COLOR)

    fig, ax = plt.subplots(figsize=(max(7, len(sc) * 1.2), 5))
    ax.bar(labels, jitters, color=color, alpha=0.8, edgecolor="black")
    if jitters:
        thr = jitters[0] * DEGRADATION_JITTER_FACTOR
        ax.axhline(thr, linestyle="--", color="red", alpha=0.7,
                   label=f"{DEGRADATION_JITTER_FACTOR}x baseline ({thr:.1f} µs)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTL Std Dev (µs)")
    ax.set_title(f"RTL Jitter vs Load — {stressor}  (H1)")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_jitter{suffix}.png"))
    plt.close(fig)

# ─── Cross-variant plots (H5) ─────────────────────────────────────────────────

def plot_overlay(proxy_conds, hicom_conds, out, stressor):
    """Mean RTL +/- std for both variants on same axes — primary H5 figure."""
    import matplotlib.pyplot as plt

    def _extract(conds):
        sc = sorted(conds.values(), key=lambda x: x["n_workers"])
        return (
            [c["n_workers"]                        for c in sc],
            [_avg(c["runs"], "rtl_mean_us")          for c in sc],
            [_std_across_runs(c["runs"], "rtl_mean_us") for c in sc],
        )

    pn, pm, ps = _extract(proxy_conds)
    hn, hm, hs = _extract(hicom_conds)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(pn, pm, yerr=ps, fmt="o-",  capsize=5, color=PROXY_COLOR,
                linewidth=2, markersize=7, label="Variant A — proxycomshm")
    ax.errorbar(hn, hm, yerr=hs, fmt="s--", capsize=5, color=HICOM_COLOR,
                linewidth=2, markersize=7, label="Variant B — hicom (HI-only)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("Mean RTL (µs)")
    ax.set_title(f"Cross-Variant RTL Comparison — {stressor}  (H5)\n"
                 "Vertical gap = contribution of vm-li direct channel participation")
    ax.set_xticks(sorted(set(pn) | set(hn)))
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"overlay_{stressor}.png"))
    plt.close(fig)


def plot_rtl_ratio(proxy_conds, hicom_conds, out, stressor):
    """RTL ratio (hicom / proxycomshm) vs N — isolation effectiveness."""
    import matplotlib.pyplot as plt

    proxy_by_n = {v["n_workers"]: v for v in proxy_conds.values()}
    hicom_by_n = {v["n_workers"]: v for v in hicom_conds.values()}
    common_n   = sorted(set(proxy_by_n) & set(hicom_by_n))

    ratios = []
    for n in common_n:
        pm = _avg(proxy_by_n[n]["runs"], "rtl_mean_us")
        hm = _avg(hicom_by_n[n]["runs"],  "rtl_mean_us")
        ratios.append(hm / pm if pm > 0 else 0.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(common_n, ratios, "o-", color="#9C27B0", linewidth=2, markersize=7)
    ax.axhline(1.0, linestyle="--", color="grey", alpha=0.5,
               label="ratio = 1.0  (equal impact on both channels)")
    ax.set_xlabel("Background Workers on vm-li (N)")
    ax.set_ylabel("RTL ratio  (hicom / proxycomshm)")
    ax.set_title(f"Isolation Effectiveness — {stressor}  (H5)\n"
                 "Decreasing ratio: hicom increasingly less affected than proxycomshm")
    ax.set_xticks(common_n); ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"rtl_ratio_{stressor}.png"))
    plt.close(fig)


def plot_side_by_side_boxplot(proxy_conds, hicom_conds, out, stressor):
    """Side-by-side box plots per N for both variants."""
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
    p_data = [_vals(proxy_by_n[n]["runs"], "rtl_mean_us") for n in common_n]
    h_data = [_vals(hicom_by_n[n]["runs"],  "rtl_mean_us") for n in common_n]

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
    ax.set_ylabel("Per-Run Mean RTL (µs)")
    ax.set_title(f"RTL Distribution Comparison — {stressor}")
    ax.legend(handles=[
        Patch(facecolor=PROXY_COLOR, alpha=0.6, label="Variant A — proxycomshm"),
        Patch(facecolor=HICOM_COLOR, alpha=0.6, label="Variant B — hicom"),
    ])
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, f"boxplot_comparison_{stressor}.png"))
    plt.close(fig)

# ─── H4 payload size ──────────────────────────────────────────────────────────

def plot_h4_payload_size(h4_results, out):
    """Scatter + linear fit of mean RTL vs payload bytes — H4."""
    import matplotlib.pyplot as plt

    sp            = _scipy()
    payload_sizes = sorted(h4_results.keys())
    means         = [_avg(h4_results[p], "rtl_mean_us")             for p in payload_sizes]
    errs          = [_std_across_runs(h4_results[p], "rtl_mean_us") for p in payload_sizes]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(payload_sizes, means, yerr=errs, fmt="o",
                color="#FF9800", capsize=5, markersize=8, zorder=3)

    if sp and len(payload_sizes) >= 2:
        lr = sp.linregress(payload_sizes, means)
        fit_x = [min(payload_sizes), max(payload_sizes)]
        fit_y = [lr.slope * x + lr.intercept for x in fit_x]
        ax.plot(fit_x, fit_y, "--", color="#F44336", linewidth=1.5,
                label=f"Linear fit: {lr.slope:.5f} µs/B  (R2 = {lr.rvalue**2:.3f})")
        ax.legend()

        baseline = means[0] if means else 1.0
        effect   = lr.slope * 3072
        h4_ok    = effect < 0.1 * baseline
        sep = "─" * 62
        print(f"\n{sep}")
        print(f"  H4 — Payload Size Analysis")
        print(sep)
        print(f"  Slope:                  {lr.slope:.6f} µs/B")
        print(f"  Predicted effect @3072B: {effect:.2f} µs"
              f"  ({effect / baseline * 100:.1f}% of baseline mean)")
        print(f"  R2:                     {lr.rvalue**2:.3f}")
        print(f"  -> H4 {'CONFIRMED  [size effect < 10% of baseline mean]' if h4_ok else 'NOT confirmed'}")

    ax.set_xlabel("Message Payload Size (bytes) — log scale")
    ax.set_ylabel("Mean RTL (µs)")
    ax.set_title("Mean RTL vs Payload Size  (N=0 baseline, H4)\n"
                 "Flat slope -> latency dominated by polling, not data copy")
    ax.set_xscale("log")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, os.path.join(out, "h4_payload_size.png"))
    plt.close(fig)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scenario 2 RTL analysis — plots and statistical tests"
    )
    parser.add_argument("-r", "--results-dir", required=True,
                        help="Variant A (proxycomshm) results directory")
    parser.add_argument("--hicom-dir", default=None,
                        help="Variant B (hicom) results dir — enables H5 cross-variant analysis")
    parser.add_argument("--h4-dir", default=None,
                        help="H4 payload-size results directory  (subdirs: 16B/ 64B/ etc.)")
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
    print(f"  Variant A:  {args.results_dir}")
    if args.hicom_dir:
        print(f"  Variant B:  {args.hicom_dir}")
    if args.h4_dir:
        print(f"  H4:         {args.h4_dir}")
    print(f"  Output:     {out}")

    # ── Load Variant A ────────────────────────────────────────────────────
    proxy_all = discover_conditions(args.results_dir)
    if not proxy_all:
        sys.exit(
            f"ERROR: no conditions found in {args.results_dir}\n"
            "Expected subdirs like: baseline_n0  cache_n4  stream_n32"
        )

    s_types = stressor_types(proxy_all)
    total   = sum(len(v["runs"]) for v in proxy_all.values())
    print(f"\nVariant A: {total} runs, {len(proxy_all)} conditions, "
          f"stressors: {', '.join(s_types)}")
    print_summary_table(proxy_all, label="Variant A — proxycomshm")

    to_plot = [args.stressor] if args.stressor else s_types

    print("\nVariant A plots and tests...")
    for st in to_plot:
        conds = for_stressor(proxy_all, st)
        if len(conds) < 2:
            print(f"  [{st}: fewer than 2 conditions — skipped]")
            continue
        sfx = f"_{st}"
        plot_mean_rtl(conds,    out, st, sfx)
        plot_percentiles(conds, out, st, sfx)
        plot_tail_ratio(conds,  out, st, sfx)
        plot_boxplot(conds,     out, st, sfx)
        plot_loss_rate(conds,   out, st, sfx)
        plot_jitter(conds,      out, st, sfx)
        test_per_variant(conds, label=f"Variant A — {st}")

    # ── Variant B and cross-variant (H5) ──────────────────────────────────
    if args.hicom_dir:
        hicom_all = discover_conditions(args.hicom_dir)
        if not hicom_all:
            print(f"\nWARNING: no conditions found in {args.hicom_dir}")
        else:
            h_types = stressor_types(hicom_all)
            h_total = sum(len(v["runs"]) for v in hicom_all.values())
            print(f"\nVariant B: {h_total} runs, {len(hicom_all)} conditions, "
                  f"stressors: {', '.join(h_types)}")
            print_summary_table(hicom_all, label="Variant B — hicom")

            # Cross-variant analysis for stressors present in both result sets
            cv_stressors = [s for s in to_plot
                            if any(v["stressor"] == s for v in hicom_all.values())]

            if cv_stressors:
                print("\nCross-variant plots and tests (H5)...")
            for st in cv_stressors:
                p_conds = for_stressor(proxy_all, st)
                h_conds = for_stressor(hicom_all, st)
                if len(p_conds) < 2 or len(h_conds) < 2:
                    continue
                plot_overlay(p_conds, h_conds, out, st)
                plot_rtl_ratio(p_conds, h_conds, out, st)
                plot_side_by_side_boxplot(p_conds, h_conds, out, st)
                test_cross_variant(p_conds, h_conds, st)

            # Standalone Variant B N-trend plots
            print("\nVariant B standalone plots...")
            for st in (cv_stressors or h_types):
                h_conds = for_stressor(hicom_all, st)
                if len(h_conds) < 2:
                    continue
                sfx = f"_hicom_{st}"
                plot_mean_rtl(h_conds, out, st, sfx)
                plot_jitter(h_conds,   out, st, sfx)
                test_per_variant(h_conds, label=f"Variant B — {st}")

    # ── H4 payload size ───────────────────────────────────────────────────
    if args.h4_dir:
        print("\nH4 payload size analysis...")
        h4 = load_h4_results(args.h4_dir)
        if not h4:
            print(f"  WARNING: no results found in {args.h4_dir}")
            print("  Expected subdirs: 16B/  64B/  512B/  3072B/")
        else:
            print(f"  Payload sizes found: {sorted(h4.keys())} B")
            plot_h4_payload_size(h4, out)

    print(f"\nDone.  Plots saved to: {out}")


if __name__ == "__main__":
    main()
