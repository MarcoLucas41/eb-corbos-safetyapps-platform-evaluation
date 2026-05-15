# Scenario 1: Freedom From Interference (FFI) — Experiment Design

---

## 1. Problem Statement

**Relationship to Scenario 2 and Scenario 3:**

The three scenarios cover three distinct properties of the same platform:

| Scenario | Property tested           | Layer                               |
|----------|---------------------------|-------------------------------------|
| 1        | Freedom from interference | Intra-VM: task timing isolation     |
| 2        | Inter-VM channel quality  | Hypervisor: shared memory latency   |
| 3        | Network determinism       | External: socket I/O stack          |

Scenario 2 asks whether the communication channel between partitions is predictable. Scenario 1 asks whether the safety partition's *computation* is temporally isolated — whether a high-integrity periodic task can meet its deadline when a concurrent low-integrity workload generates interference through shared hardware resources.

**Core question:**
To what extent does EBcLfSA's hypervisor-based partitioning ensure that the timing behaviour of a periodic speedometer task remains within its specified deadline (10 ms) when subjected to varying types of interference from a concurrent low-integrity workload targeting shared hardware resources (L2 cache, memory bus, CPU scheduler, block I/O)?

This scenario is implemented as **two complementary variants** that test the same stressor matrix from different isolation contexts:

| Variant | Speedometer runs on | Stressor runs on | Research question |
|---------|--------------------|--------------------|-------------------|
| A — HI-partitioned | vm-hi (SDK-restricted) | vm-li | Does hypervisor partitioning keep the HI task within its deadline despite LI interference? |
| B — LI-colocated   | vm-li (standard Linux) | vm-li (same VM) | How does the same task behave without partitioning, sharing an OS with the stressor? |

Variant A measures the platform's FFI guarantee — the hypervisor's ability to isolate timing behaviour across VM boundaries. Variant B provides a **control condition**: the same task and stressors sharing an OS and CPU cores without hypervisor protection. Comparing the two variants isolates the hypervisor's contribution to timing isolation from QEMU's baseline scheduling behaviour.

---

## 2. Variables

### Independent variables (shared across both variants)

**Primary — Stress type:** The type of interference workload running concurrently with the speedometer. Each targets a different shared hardware resource.

Stress types: **none (baseline), CPU, cache (L2 thrashing), memory bandwidth (stream), I/O, cache+stream combined, worst-case combined (all stressors)**.

**Fixed parameters:**
- Number of repetitions per scenario: **10 runs**
- Test duration per run: **60 s**
- Speedometer task period: **50 ms** (20 Hz)
- Deadline: **10 ms**

### Dependent variables (metrics, both variants)

- **Wake-up jitter (μs)** — deviation of actual wakeup time from the scheduled activation time. Measured as |wakeup_time − next_wakeup| using `CLOCK_MONOTONIC`.
- **Execution time (μs)** — wall-clock time to complete the speedometer task (exec_end − exec_start).
- **Response time (μs)** — end-to-end time from scheduled activation to task completion (exec_end − next_wakeup). Equals jitter + overhead + execution time. This is the primary FFI metric.
- **Deadline miss rate (%)** — proportion of cycles where response_time > DEADLINE (10 ms).
- **Safety margin (%)** — (DEADLINE − worst_response_time) / DEADLINE × 100. Negative = deadline exceeded.

### Cross-variant comparison metric

**Response time ratio (Variant B / Variant A) at each scenario** — if hypervisor partitioning is effective, Variant A response times should be lower and more stable than Variant B. A ratio > 1 at stressed conditions quantifies how much worse the unpartitioned case is.

---

## 3. Hypotheses

### Variant A — HI-partitioned

**H₀ (null):** The type of low-integrity workload has no statistically significant effect on the response time, jitter, or execution time of the high-integrity speedometer task.

**H₁:** The type of low-integrity workload has a statistically significant effect on response time and jitter. Interference is measurable despite hypervisor partitioning.

**H₂ (resource ranking):** Cache and memory bandwidth stressors (S2, S3, S5) cause significantly higher jitter and response time degradation than CPU-only (S1) and I/O-only (S4) stressors, due to contention on the shared L2 cache and memory interconnect.

**H₃ (deadline compliance):** Worst-case response time remains below the 10 ms deadline across all scenarios. This is the core FFI acceptance criterion.

### Variant B — LI-colocated

**H₄ (no isolation):** Without hypervisor partitioning, stressors cause significantly larger response time degradation than in Variant A. The speedometer shares CPU cores, scheduler, and memory subsystem with the stressors, so interference is direct and unmitigated.

**H₅ (partitioning benefit):** The response time ratio (Variant B / Variant A) is significantly greater than 1 under stressed conditions, quantifying the hypervisor's contribution to timing isolation.

---

## 4. Experimental Setup

### Platform architecture

- Hypervisor: EB corbos Linux (L4/Fiasco.OC-based), running on QEMU (ARM virt machine)
- CPU: Emulated ARM Cortex-A53 (ARMv8), 4 cores
  - L1 caches: private per core
  - L2 cache: shared across all cores (primary interference channel)
  - Memory bus: shared AXI interconnect
- VM partitioning:
  - vm-hi: cores 2–3 (cpus: 0xc), 128 MB RAM, SDK enabled (syscall whitelist)
  - vm-li: cores 0–1 (cpus: 0x3), 512 MB RAM, standard Linux
- Inter-VM communication: shared memory ring buffers (proxycomshm, 2 MB)
- Storage: vm-hi root filesystem served via virtio_block through vm-li

### Speedometer task parameters (both variants)

| Parameter | Value |
|-----------|-------|
| Period | 50 ms (20 Hz) |
| Deadline | 10 ms |
| RT priority | 99 (SCHED_FIFO, Variant B only — blocked by SDK in Variant A) |
| Memory locking | mlockall (Variant B only — blocked by SDK in Variant A) |
| Timing source | `clock_gettime(CLOCK_MONOTONIC)` |
| Sleep method | `nanosleep` (relative) |

### Experiment matrix (shared across both variants)

| # | Scenario | Stressor (stress-ng args) |
|---|----------|---------------------------|
| S0 | Baseline (no stress) | (none) |
| S1 | CPU stress | `--cpu 0 --cpu-method all` |
| S2 | Cache stress (L2 thrashing) | `--cache 2` |
| S3 | Memory bandwidth stress | `--stream 2` |
| S4 | I/O stress | `--io 4` |
| S5 | Cache + stream combined | `--cache 2 --stream 2` |
| S6 | Worst-case combined | `--cpu 0 --cpu-method all --cache 2 --stream 2 --vm 2 --vm-bytes 128M --io 2` |

Each scenario is repeated 10 times. QEMU is rebooted between scenarios for a clean system state.

**Variant A total: 7 scenarios × 10 runs = 70 runs.**
**Variant B total: 7 scenarios × 10 runs = 70 runs.**
**Combined total: 140 runs.**

---

## 5. Applications

### Variant A — HI-partitioned

#### `speedometer` (vm-hi — PID 1)
Periodic RT task running as init on the high-integrity VM. Registers with UUID in proxycomshm, waits for START command from vm-li, runs the RT measurement loop for the configured duration, computes aggregate statistics, dumps per-cycle CSV to the serial console (between `===CSV_START===` / `===CSV_END===` markers), sends statistics back to vm-li via shared memory. Supports multiple consecutive runs without rebooting. SDK-restricted: `mlockall` and `signal` are no-ops; `SCHED_FIFO` is not set.

#### `stress_workload` (vm-li)
Orchestration wrapper. Discovers speedometer in proxycomshm, sends START, forks stress-ng with the scenario's stressor arguments and `--timeout` flag, waits for stress-ng to complete, sends STOP, receives statistics from speedometer via shared memory, prints results to stdout.

#### `stress-ng` (vm-li, third-party)
Generates targeted system load. Stressors used: `--cpu`, `--cache`, `--stream`, `--vm`, `--io`.

### Variant B — LI-colocated

#### `speedometer_li` (vm-li)
Standalone version of the speedometer with no shared memory dependencies. Runs directly for the configured duration (`-d` flag), prints statistics and per-cycle CSV to stdout (same format and markers as Variant A). Supports `SCHED_FIFO` priority 99 and `mlockall` since it runs on standard Linux as root. Handles SIGINT and SIGTERM for clean shutdown.

#### `stress-ng` (vm-li, same VM)
Same stressor configurations as Variant A, but now sharing the same OS instance, CPU cores, and memory subsystem with the speedometer task.

### Orchestration scripts

- `s1_orchestrator.sh` — Variant A. Boots QEMU, waits for SSH, runs `stress_workload` via SSH for each scenario/run, extracts per-cycle CSV from the QEMU serial log, collects vmstat monitoring data.
- `s1_orchestrator_li.sh` — Variant B. Boots QEMU, waits for SSH, starts stress-ng in background, runs `speedometer_li` in foreground via SSH, extracts per-cycle CSV directly from SSH output, collects vmstat monitoring data.
- `s1_plots.py` — Analysis script. Parses both variants' output (auto-detects format), generates aggregate plots and per-run time-series plots. Accepts `-r <results_dir>` for either variant.

---

## 6. Run Procedure

### Variant A — HI-partitioned (`s1_orchestrator.sh`)

1. Boot QEMU with hypervisor image; vm-hi runs `speedometer` as PID 1.
2. Wait for li VM boot marker and SSH readiness.
3. For each scenario S0–S6:
   a. For each run 1–10:
      - Record serial log offset.
      - SSH into vm-li: run `stress_workload <params> -d 60` with vmstat monitoring.
      - `stress_workload` sends START → speedometer runs RT loop → stress-ng executes → STOP → statistics via shared memory.
      - Wait for serial log flush; extract per-cycle CSV from serial log.
      - Save `run_XX.txt` (stress_workload output), `run_XX_cycles.csv`, `run_XX_vmstat.csv`.
   b. Stop QEMU; reboot for next scenario.

Multiple runs within a scenario share one QEMU boot. QEMU is rebooted between scenarios.

### Variant B — LI-colocated (`s1_orchestrator_li.sh`)

1. Boot QEMU with same hypervisor image.
2. Wait for li VM boot marker and SSH readiness.
3. For each scenario S0–S6:
   a. For each run 1–10:
      - SSH into vm-li: start vmstat in background, start stress-ng in background (if applicable, with `--timeout` slightly longer than duration), wait 2 s for stress to stabilise, run `speedometer_li -d 60` in foreground.
      - All output (statistics + CSV dump + vmstat) captured in SSH stdout.
      - Extract per-cycle CSV and vmstat from run file.
      - Save `run_XX.txt`, `run_XX_cycles.csv`, `run_XX_vmstat.csv`.
   b. Stop QEMU; reboot for next scenario.

Results default to `results/` (Variant A) and `results_li/` (Variant B).

---

## 7. Acceptance Criteria

### Variant A — HI-partitioned (FFI requirement)

**Deadline compliance (H₃):** Zero deadline misses (response_time ≤ 10 ms) across all 70 runs.

**Jitter bound:** Worst-case jitter < 3.3 ms (33% of deadline margin) across all scenarios.

**Determinism under stress:** Mean response time < 5 ms and 99.9th percentile response time < 10 ms in all scenarios including S6 (worst-case combined).

**Safety margin:** (DEADLINE − worst_response_time) / DEADLINE > 0% for all scenarios.

### Cross-variant comparison

**Partitioning benefit (H₅):** Variant A worst-case response times are significantly lower than Variant B worst-case response times under stressed conditions (Mann-Whitney U, p < 0.05).

---

## 8. Data Analysis and Hypothesis Testing

### Per-variant tests (applied to both A and B independently)

**H₀ (no effect of stress type):**
- Kruskal-Wallis test on per-run mean response time across all 7 scenarios.
- Reject if p < 0.05.
- Follow up with pairwise Mann-Whitney U tests (with Bonferroni correction) comparing each S1–S6 against S0 (baseline).

**H₂ (resource ranking, Variant A):**
- Compare the magnitude of response time increase relative to baseline across stressor types.
- Rank scenarios by median and worst-case response time increase.
- Confirm or reject whether cache/memory stressors (S2, S3, S5) cause significantly higher degradation than CPU-only (S1) and I/O-only (S4).

**H₃ (deadline compliance, Variant A):**
- Report worst-case response time observed across all runs and scenarios.
- Report safety margin per scenario.
- Confirm H₃ if worst-case response time < 10 ms in all scenarios.

### Cross-variant comparison (H₄, H₅)

- **Overlay plot:** per-scenario worst-case response time for both variants.
- **Response time ratio (B/A):** compute per scenario. Ratio > 1 indicates Variant B is worse (partitioning helps). Report mean ratio across stressed scenarios.
- **Mann-Whitney U test:** compare Variant A vs Variant B per-run max response time at each stressed scenario. Significant p < 0.05 confirms H₅.

### Visualisations

**Aggregate plots (per variant):**
- Box plot of max jitter per scenario (across 10 runs)
- Bar chart of mean execution time per scenario (avg ± std across runs)
- Bar chart of mean response time per scenario (avg ± std across runs)
- Box plot of max response time per scenario (with deadline reference line)
- Deadline miss rate bar chart
- Safety margin bar chart

**Per-run time-series plots (per variant, per run):**
- Top: response time timeline with deadline line and miss markers
- Middle: CPU breakdown (us/sy/id from vmstat, stacked area)
- Bottom: system metrics (interrupts/s and context switches/s from vmstat, dual axis)

**Cross-variant plots:**
- Response time comparison overlay (Variant A vs Variant B, same scenarios)
- Time-series comparison (baseline vs worst-case for representative runs of each variant)

---

## 9. Platform Constraints and Limitations

**QEMU does not model real cache/bus contention.** Both VMs run as threads in a single QEMU process. Cache coherency is automatic (no physical L2 hierarchy), and there is no modelled AXI bus arbitration. Timing results reflect QEMU's host OS vCPU scheduling, not physical memory system interference. On real hardware, cache and memory bus stressors would create physical contention absent in QEMU.

**SDK syscall restrictions affect Variant A.** The EBcLfSA SDK restricts high-integrity applications to a syscall whitelist. In the QEMU setup, prohibited syscalls are no-ops with a console warning. Affected calls: `mlockall` (memory locking — no effect on 128 MB dedicated partition), `signal/rt_sigprocmask` (signal handler — STOP command provides alternative shutdown), `sched_setscheduler` (commented out — RT priority not set in Variant A). None of these affect measurement integrity: `clock_gettime`, `nanosleep`, and shared memory read/write are all allowed.

**Variant B runs with RT privileges that Variant A cannot use.** `speedometer_li` on vm-li successfully sets `SCHED_FIFO` priority 99 and `mlockall`, giving it stronger RT guarantees than the SDK-restricted speedometer on vm-hi. This means Variant B has a *scheduling advantage* over Variant A. If Variant A still meets the deadline while Variant B does not (under heavy stress), this strengthens the FFI conclusion — hypervisor partitioning compensates for the lack of intra-VM RT scheduling.

**Variant B shares CPU cores with stressors.** vm-li has 2 cores (0–1). Both `speedometer_li` and stress-ng compete for these cores. In Variant A, the speedometer runs on isolated cores (2–3) while stressors run on cores (0–1). This core separation is the hypervisor's primary isolation mechanism.

**QEMU vCPU scheduling introduces measurement artefacts.** Between any two `clock_gettime` calls, the host OS may deschedule the QEMU vCPU thread, injecting millisecond-scale gaps that appear as jitter or inflated response times. These artefacts are present in both variants and do not invalidate relative comparisons, but absolute timing values should not be taken as representative of real hardware.

---

## 10. Expected Outcomes and Conclusions Framework

### Variant A — HI-partitioned

- **H₁ expected confirmed:** interference is measurable as increased jitter and response time under stress, even with hypervisor partitioning. The interference path is indirect (QEMU host scheduling, shared virtual cache).
- **H₂ expected partially confirmed:** cache and stream stressors may show higher impact than CPU-only, but the effect may be muted in QEMU compared to real hardware due to the absence of physical L2 contention.
- **H₃ expected confirmed:** the 10 ms deadline is expected to be met in all scenarios, demonstrating adequate FFI for this task at the tested stress levels.

### Variant B — LI-colocated

- **H₄ expected confirmed:** without partitioning, stress-ng directly competes with speedometer_li for CPU time, causing significantly larger response time spikes and more deadline misses.
- Under heavy stress (S6), deadline misses are expected. Under moderate stress, some degradation but potentially within deadline.

### Cross-variant conclusions

- **H₅ expected confirmed:** the response time ratio (B/A) should be > 1 under stressed conditions, quantifying the hypervisor's timing isolation benefit.
- The magnitude of the ratio characterises the "FFI factor" — e.g., "hypervisor partitioning reduced worst-case response time by X× under cache stress compared to the unpartitioned case."

### Conclusions to draw

- Report per-scenario deadline compliance and safety margin for Variant A.
- Report the interference ranking: which stressor types cause the most degradation.
- Report the partitioning benefit: quantify the FFI factor from Variant A vs Variant B comparison.
- Overall FFI assessment: even if H₁ is confirmed (interference exists), H₃ may hold (bounded within deadline). This demonstrates adequate FFI despite imperfect hardware isolation.

**Potential follow-up:**
- Repeat on real EBcLfSA hardware for physical L2 cache and memory bus contention data.
- Vary stressor intensity (worker count) within each type to characterise scaling.
- Test with tighter deadlines (5 ms, 2 ms) to find the FFI breaking point.
- Test with a more computationally intensive HI task to stress the execution time component.
- Add CPU core pinning in Variant B (`speedometer_li -c 0`) to isolate the scheduler effect from the core-sharing effect.
