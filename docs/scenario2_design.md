# Scenario 2: Inter-VM Communication Latency and Reliability — Experiment Design

---

## 1. Problem Statement

**Relationship to Scenario 1 and Scenario 3:**

The three scenarios cover three distinct communication layers of the same platform:

| Scenario | Property tested           | Layer                               |
|----------|---------------------------|-------------------------------------|
| 1        | Freedom from interference | Intra-VM: task timing isolation     |
| 2        | Inter-VM channel quality  | Hypervisor: shared memory latency   |
| 3        | Network determinism       | External: socket I/O stack          |

Scenario 1 asks whether the safety partition's *computation* is temporally isolated. Scenario 2 asks whether the *communication channel* between partitions is predictable and reliable. These are independent properties: a system can have perfectly isolated computation but a non-deterministic communication channel, or vice versa.

**Motivation:**
In a mixed-criticality SDV platform, safety-relevant partitions do not operate in isolation — they exchange data with other partitions (sensor readings, actuator commands, status signals). The latency and reliability of the inter-partition communication channel directly affect the end-to-end response time of safety functions. If the channel has unpredictable latency under load, the safety case is undermined even if each partition individually meets its timing requirements.

This scenario is implemented as **two complementary variants** that test different shared memory channels exposed by the EB corbos hypervisor:

| Variant | Channel | Accessible from | Research question |
|---------|---------|-----------------|-------------------|
| A — proxycomshm | `hv_proxycomshm` (2 MB) | vm-hi **and** vm-li | Does the cross-VM channel maintain bounded RTL as vm-li load increases? |
| B — hicom | `hv_hicomshm` (132 KB) | vm-hi **only** | Does vm-li load affect a HI-only channel that vm-li cannot access at all? |

Variant A characterises the shared inter-VM channel under direct sender-side load. Variant B isolates the *indirect* interference path — if latency increases under vm-li load even when vm-li cannot touch the channel, this implicates host-level resource contention (QEMU scheduler, shared CPU cache) rather than direct memory access competition. Together, the two variants decompose the observed RTL degradation into attributable causes.

---

## 2. Variables

### Independent variables (shared across both variants)

**Primary — Background load level N:**
Number of concurrent stress-ng worker pairs (`--cache N --stream N`) running on vm-li. These represent progressively consolidated low-criticality workloads. The orchestration script starts stress-ng once per condition before the first run and terminates it after the last.

Load levels: **N = 0, 1, 2, 4, 6, 8**

- N = 0: baseline — no background stress on vm-li.
- N = 6, 8: heavy oversubscription of vm-li's 2 vCPUs.

Rationale for the `--cache --stream` profile: these stressors directly compete with the ring buffer access pattern (cache line reads/writes, memory bus transactions) in Variant A, and pollute the shared CPU cache in Variant B.

**Secondary — Message payload size (Variant A, N = 0 only):**
Sizes: **8 B, 64 B, 512 B, 3072 B** (within the 4095 B ring buffer limit).
Not applied to Variant B; message size is fixed at 64 B.

### Dependent variables (metrics, both variants)

- **Round-trip latency (RTL, μs)** — time from writing a ping to receiving the echo. Measured on vm-li (Variant A) or vm-hi (Variant B) using `CLOCK_MONOTONIC`; no cross-VM clock synchronisation required in either case.
- **RTL jitter (μs)** — standard deviation of RTL per run. Primary indicator of temporal predictability.
- **Tail latency** — P95, P99, P99.9 RTL.
- **Message loss rate (%)** — proportion of pings that timed out (500 ms threshold).
- **Throughput (messages/s)** — secondary metric.

### Cross-variant comparison metric

**RTL ratio (hicom / proxycomshm) at each N** — if vm-li load affects both channels equally, this ratio stays constant; if the hicom channel is less affected, the ratio decreases with N, quantifying how much of the degradation in Variant A is caused by vm-li's direct participation in the channel.

---

## 3. Hypotheses

### Variant A — proxycomshm

**H₀ (null):** Load level N has no statistically significant effect on RTL or jitter.

**H₁:** Mean RTL and jitter increase monotonically with N. Background cache/stream stress on vm-li evicts ring buffer cache lines and delays the `shm_client` poll cycles.

**H₂ (tail growth):** P99.9 RTL grows faster than mean RTL as N increases. Occasional severe preemptions produce a heavy tail that is the most safety-relevant indicator of channel degradation.

**H₃ (reliability):** Message delivery remains 100% reliable (0% loss) at all load levels under the stop-and-wait protocol.

**H₄ (size independence):** At baseline (N = 0), RTL is insensitive to payload size across the 8 B – 3072 B range, confirming that latency is dominated by polling overhead rather than the data copy.

### Variant B — hicom

**H₅ (isolation):** RTL on the hicom channel is significantly less sensitive to N than on the proxycomshm channel. Because vm-li cannot access `hv_hicomshm`, any degradation must arise through indirect paths only (QEMU host scheduler contention, shared CPU cache pollution). If H₅ is confirmed, the EB corbos hypervisor provides meaningful channel-level isolation between partitions beyond simple memory access control.

**H₅a (stronger form):** RTL on the hicom channel does not increase significantly with N. This would indicate that QEMU's host-level resource sharing has negligible impact on the HI-only channel — i.e., the partitioning is effective end-to-end at the tested load levels.

**H₅b (weaker form):** RTL on the hicom channel does increase with N, but less steeply than on the proxycomshm channel. This would indicate that indirect interference (host scheduler / cache) is present but smaller than the direct interference path tested in Variant A.

---

## 4. Experimental Setup

### Platform architecture

- Hypervisor: EB corbos Linux (L4/Fiasco.OC-based), running on QEMU ARM virt machine
- CPU: Emulated ARM Cortex-A57 (ARMv8), 4 vCPUs total
- vm-hi: vCPUs 2–3 (cpus: 0xc), 128 MB RAM
- vm-li: vCPUs 0–1 (cpus: 0x3), 512 MB RAM

### Shared memory regions used

| Region | Size | Accessible from | Used by |
|--------|------|-----------------|--------|
| `hv_proxycomshm` | 2 MB | vm-hi + vm-li | Variant A ping channel; Variant B trigger only |
| `hv_hicomshm` | 132 KB | vm-hi only | Variant B ping channel |

### Polling strategy and floor effect

The ring buffer has no blocking read primitive — consumers busy-poll with a 100 μs `nanosleep` between attempts. This floors RTL at ~200 μs (one poll interval per side) and avoids QEMU host CPU starvation from busy-spinning. The interval is constant across all conditions, so relative comparisons remain valid. Both variants use the same 100 μs poll interval.

### Message structures

**Variant A** (`scenario2/common/common.h`):
```c
struct ping_message {
    uint64_t seq;            /* Sequence number                       */
    uint64_t send_time_ns;   /* CLOCK_MONOTONIC at send on vm-li      */
    uint8_t  payload[48];    /* Fixed padding (total: 64 B)           */
};
```
`send_time_ns` stamped on vm-li before `shmlib_ring_buffer_write`. RTL = `t_recv − send_time_ns`, both on vm-li.

**Variant B** (`scenario2/hicom_variant/common/common.h`):
```c
struct hicom_ping_message {
    uint64_t seq;            /* Sequence number                       */
    uint64_t send_time_ns;   /* CLOCK_MONOTONIC at send on vm-hi      */
    uint8_t  payload[48];    /* Fixed padding (total: 64 B)           */
};

struct hicom_channel {       /* Overlaid on hv_hicomshm               */
    struct shmlib_ring_buffer pinger_to_echo;
    struct shmlib_ring_buffer echo_to_pinger;
};
```
`send_time_ns` stamped on vm-hi (in `shm_ping_hi`) before writing to `pinger_to_echo`. RTL = `t_recv − send_time_ns`, both in `shm_ping_hi`. No cross-VM clock synchronisation required.

### Experiment matrix

**Variant A — proxycomshm (primary: varying N, fixed 64 B payload, 1000 pings/run):**

| Condition | N workers | Orchestrator action                          | Runs |
|-----------|-----------|----------------------------------------------|------|
| C0        | 0         | `shm_client -n 1000` (no stress-ng)          | 10   |
| C1        | 1         | stress-ng `--cache 1 --stream 1` on vm-li    | 10   |
| C2        | 2         | stress-ng `--cache 2 --stream 2` on vm-li    | 10   |
| C3        | 4         | stress-ng `--cache 4 --stream 4` on vm-li    | 10   |
| C4        | 6         | stress-ng `--cache 6 --stream 6` on vm-li    | 10   |
| C5        | 8         | stress-ng `--cache 8 --stream 8` on vm-li    | 10   |

stress-ng is started by the orchestrator (`s2_orchestrator.sh`) once per condition before the run loop and killed after. `shm_client` only sends pings.

**Variant A — secondary (varying payload, N = 0, 1000 pings/run):**

| Condition | Payload | Runs |
|-----------|---------|------|
| P1        | 8 B     | 5    |
| P2        | 64 B    | covered by C0 |
| P3        | 512 B   | 5    |
| P4        | 3072 B  | 5    |

**Variant A total: 60 + 15 = 75 runs.**

**Variant B — hicom (same N conditions, fixed 64 B payload, 1000 pings/run):**

| Condition | N workers | Orchestrator action                       | Runs |
|-----------|-----------|-------------------------------------------|------|
| C0        | 0         | `shm_trigger` GO only (no stress-ng)      | 10   |
| C1–C5     | 1–8       | stress-ng `--cache N --stream N` on vm-li | 10   |

**Variant B total: 60 runs.**

**Combined total: 135 runs** across both variants.

---

## 5. Applications

### Variant A — proxycomshm

#### `shm_echo` (vm-hi — PID 1)
Minimal echo server. Registers with `SHM_ECHO_UUID` in proxyshm, polls `li_to_hi` ring buffer every 100 μs, echoes each `ping_message` unchanged to `hi_to_li`. Loops indefinitely; never exits as PID 1.

#### `shm_client` (vm-li)
Ping-pong client. Waits for \texttt{shm\_echo} to register in \texttt{hv\_proxycomshm}. It then sends pings sequentially (stop-and-wait) and records RTL for each message. 
At the end, it prints a summary of statistics and writes per-message data to CSV for analysis. 



Command-line interface:
```
shm_client [options]
  -n <count>  Ping messages to send (default: 1000)
  -o <file>   Output CSV path (default: shm_client_log.csv)
  -h          Show help
```

Console output format (captured as `run_XX.txt`):
```
========== SHM PING-PONG RESULTS ==========
Messages sent:    1000
Messages lost:    0 (0.000%)
RTL (us):
  Mean:   342.1    Std dev: 284.6
  Min:     98.4    Max:    8712.3
  P50:    310.5    P95:     621.2
  P99:   1843.7    P99.9:  6100.1
Throughput: 2923 msg/s
============================================
```

### Variant B — hicom

#### `shm_ping_hi` (vm-hi — PID 1)
Pinger and statistics collector. On startup: initialises `hv_hicomshm` as a `hicom_channel` struct (two ring buffers), then spawns `shm_echo_hi` as a child process via `clone(CLONE_VFORK | CLONE_VM)` + `execve`. Prints `HICOM_PING_READY` once ready, then waits for a `HICOM_TRIGGER_GO` byte on its proxyshm `li_to_hi` channel. On trigger: drains stale echoes, sends N stop-and-wait pings through `pinger_to_echo`, collects RTL samples, prints results, prints `HICOM_PING_DONE run=N`. Repeats for subsequent triggers without rebooting.

#### `shm_echo_hi` (vm-hi — child of `shm_ping_hi`)
Echo server co-located in vm-hi. Registers with `HICOM_ECHO_UUID` in proxyshm (`init_proxyshm=false`), overlays the already-initialised `hicom_channel`, polls `pinger_to_echo` every 100 μs, echoes each message unchanged to `echo_to_pinger`. Never exits.

#### `shm_trigger` (vm-li)
Run trigger. Finds `shm_ping_hi` in proxyshm by UUID and writes a single `HICOM_TRIGGER_GO` byte (0x47) to its `li_to_hi` channel. Called once per run by the orchestration script.

Console output format for Variant B (extracted from QEMU serial log as `run_XX.txt`):
```
HICOM_PING_START run=1
Messages: 1000  |  Payload: 64 B  |  Channel: hicom (HI-only)
[... progress lines ...]
========== SHM PING-PONG RESULTS ==========
Messages sent:    1000
Messages lost:    0 (0.000%)
RTL (us):
  Mean:   XXX.X    Std dev: XXX.X
  ...
Throughput: XXXX msg/s
============================================
HICOM_PING_DONE run=1
```
Note: results come from the vm-hi serial console log, not from an SSH session.

---

## 6. Run Procedure

### Variant A — proxycomshm (`experiments/s2_orchestrator.sh`)

1. Build hi image with `shm_echo` as PID 1 (`init=/shm_echo` in `hv-base.yaml`).
2. Boot QEMU; wait for li VM SSH readiness.
3. `shm_echo` starts automatically on vm-hi.
4. If N > 0: SSH into vm-li, start `stress-ng --cache N --stream N` in background; wait 3 s for ramp-up.
5. For each run: SSH into vm-li, run `shm_client -n 1000`; output saved to `run_XX.txt`.
6. After all runs: kill stress-ng via SSH.
7. Stop QEMU; reboot for next condition.

Multiple runs within a condition share one QEMU boot. QEMU is rebooted between conditions for a clean system state.

### Variant B — hicom (`experiments/s2hv_orchestrator.sh`)

1. Build hi image with `shm_ping_hi` as PID 1 (`init=/shm_ping_hi` in `hv-base-s2hicom.yaml`). Both `shm_ping_hi` and `shm_echo_hi` must be in the hi VM root filesystem.
2. Boot QEMU; wait for li VM SSH readiness **and** for `HICOM_PING_READY` in the vm-hi serial console log.
3. `shm_ping_hi` starts, initialises `hv_hicomshm`, spawns `shm_echo_hi`, prints `HICOM_PING_READY`.
4. If N > 0: SSH into vm-li, start `stress-ng --cache N --stream N` in background; wait 3 s.
5. For each run: SSH into vm-li, run `shm_trigger`; wait for `HICOM_PING_DONE run=R` in serial log; extract result block to `run_XX.txt`.
6. After all runs: kill stress-ng via SSH.
7. Stop QEMU; reboot for next condition.

Results for Variant B are extracted from the QEMU serial log by `awk` between the `HICOM_PING_START`/`HICOM_PING_DONE` markers. The output format is identical to Variant A, so `analyze_scenario2.py` can parse both result sets.

---

## 7. Acceptance Criteria

### Variant A — proxycomshm

**Reliability:** message loss rate = 0% at all load levels (H₃).

**Predictability** at load level N:
- P99 RTL ≤ 3× baseline (C0) P99 RTL.
- RTL jitter (stddev) ≤ 2× baseline jitter.

The **channel degradation threshold N\*** is the smallest N for which either predictability criterion is violated.

### Variant B — hicom

**Reliability:** message loss rate = 0% at all load levels.

**Isolation criterion (H₅):** The slope of mean RTL vs N in Variant B is statistically significantly smaller than in Variant A (paired Wilcoxon test on per-condition medians, p < 0.05). If the slopes are not significantly different, H₅ is rejected: indirect interference is as damaging as direct participation in the channel.

**Strong isolation (H₅a):** Kruskal-Wallis test on Variant B per-run mean RTL across all 6 conditions fails to reject H₀ (p ≥ 0.05) — load level has no significant effect on hicom RTL.

---

## 8. Data Analysis and Hypothesis Testing

### Per-variant tests (applied to both A and B independently)

**H₀ / H₅a (no effect of N):**
- Kruskal-Wallis test on per-run mean RTL across all 6 conditions.
- Reject if p < 0.05.

**H₁ / H₅b (monotonic RTL increase):**
- Spearman rank correlation (ρ) between N and per-run mean RTL and jitter.
- Confirm if ρ > 0.8 and p < 0.05.

**H₂ (tail growth):**
- Compute ratio P99.9 / mean at each N for Variant A.
- If this ratio increases with N, the tail grows disproportionately.

**H₃ (zero message loss):**
- Report total lost messages per condition per variant.
- If any loss: report condition, run index, and sequence number of first lost message.

**H₄ (size independence, Variant A only):**
- Plot mean RTL vs payload size at N = 0 (C0 + P1–P4).
- Fit a linear model; report slope (μs/byte) and R².
- H₄ confirmed if slope × 3072 B < 10% of baseline mean RTL.

### Cross-variant comparison (H₅)

- **Overlay plot:** mean RTL vs N for both Variant A and Variant B on the same axes. The vertical gap between curves at each N represents the contribution of vm-li's direct channel participation to RTL.
- **Slope comparison:** fit a linear trend to mean RTL vs N for each variant; compare slopes and 95% confidence intervals. Non-overlapping CIs confirm H₅.
- **RTL ratio (B/A) vs N:** if partitioning is effective, this ratio should approach 1 or decrease as N increases (hicom becomes relatively less affected).
- Paired Wilcoxon test on per-condition median RTL: Variant A vs Variant B at each N.

### Visualisations
- Line plot of mean RTL ± std vs N (both variants overlaid — primary comparison)
- Multi-line plot of P50 / P99 / P99.9 RTL vs N (Variant A — tail growth)
- Box plots of per-run mean RTL per condition (both variants, side-by-side)
- Bar chart of message loss rate per condition and variant
- RTL ratio (Variant B / Variant A) vs N (isolation effectiveness)
- Scatter plot of RTL vs payload size at N = 0 (Variant A — H₄)

---

## 9. Platform Constraints and Limitations

**QEMU's shared memory model differs from real hardware.** Both `hv_proxycomshm` and `hv_hicomshm` are host-process mmaps in QEMU. Cache coherency is automatic (no real cache hierarchy between vCPUs), and there is no modelled AXI bus arbitration. On real EBcLfSA hardware, cross-VM accesses traverse the physical cache hierarchy and memory interconnect. RTL values here reflect host-OS vCPU thread scheduling, not physical memory system timing.

**The hicom isolation result is QEMU-specific.** In QEMU, vm-li's stress-ng workers compete with vm-hi's processes for *host OS* CPU time. If H₅a holds (no effect on hicom RTL), this only demonstrates that the QEMU scheduler does not measurably perturb the HI partition at these load levels — not that the hypervisor provides physical cache isolation. On real hardware with shared L3 cache, vm-li's stress-ng would directly pollute vm-hi's working set, and hicom RTL would likely be more affected.

**Polling interval floors measurable RTL at ~200 μs.** Any channel behaviour faster than this is not observable. The 100 μs nanosleep is a deliberate trade-off for QEMU stability; it is identical in both variants, so intra-variant comparisons remain valid.

**No cross-VM clock synchronisation.** Variant A RTL is measured on vm-li; Variant B RTL is measured on vm-hi. Both are intra-process measurements using `CLOCK_MONOTONIC`, so each is individually accurate. However, the absolute RTL values are *not directly comparable* across variants as indicators of channel efficiency, since the two channels have different topology (one crosses the VM boundary, one does not). The cross-variant comparison is meaningful only for the slope-vs-N analysis, not for absolute latency.

**vm-hi co-location load is not varied.** The effect of additional processes competing with `shm_echo` or `shm_ping_hi` for the ring buffer spinlock is not tested. This is a relevant follow-up.

---

## 10. Expected Outcomes and Conclusions Framework

### Variant A — proxycomshm

- **H₁ expected confirmed:** vm-li's vCPUs are preempted by stress-ng workers, delaying `shm_client` write and poll cycles, increasing RTL.
- **H₂ expected confirmed:** occasional severe preemptions produce a heavy tail at N ≥ 4 (vm-li oversubscribed).
- **H₃ expected to hold:** stop-and-wait + spinlock prevents loss.
- **H₄ expected to hold:** polling overhead dominates over copy time at QEMU speeds.

### Variant B — hicom

- **H₅a (no effect) may or may not hold depending on QEMU host load.** If the host machine has spare cores, vm-li stress-ng workers may not visibly delay vm-hi's vCPU threads, and hicom RTL will be flat. Under host CPU contention, some slope may appear.
- **H₅b (less steep than Variant A) is expected to be confirmed** in most host conditions: since vm-li does not participate in the hicom ping-pong, the dominant delay mechanism in Variant A (poll cycle preemption on the sender side) is absent.

### Conclusions to draw (both variants)

- Report N* for Variant A or state "no degradation threshold within N = 0–8."
- Quantify RTL growth: "at N = X, proxycomshm P99 RTL was Y μs (W× baseline); hicom P99 RTL was Z μs (V× baseline)."
- Report the isolation gap: "Y − Z μs of RTL degradation at N = 8 is directly attributable to vm-li's participation in the proxycomshm channel."
- Relate to the safety argument: budget the worst-case P99.9 RTL of the chosen channel as communication overhead in any safety function's WCET analysis.

**Potential follow-up:**
- Repeat on real EBcLfSA hardware: hicom interference via L3 cache will be physically present and potentially larger.
- Test with busy-polling on real hardware.
- Add a concurrent speedometer RT loop in vm-hi to measure intra-partition spinlock contention effects on hicom.
- Characterise maximum sustained throughput (pipelined mode) vs. stop-and-wait latency.
