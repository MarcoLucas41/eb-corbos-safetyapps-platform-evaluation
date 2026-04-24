# Scenario 2: Inter-VM Communication Latency and Reliability — Experiment Design

---

## 1. Problem Statement

**Research question:**
> "Does the inter-VM shared memory channel provided by the EB corbos hypervisor maintain bounded and predictable round-trip latency as the load on the sending partition increases, and does message delivery remain reliable under all tested load conditions?"

**Relationship to Scenario 1 and Scenario 3:**

The three scenarios cover three distinct communication layers of the same platform:

| Scenario | Property tested         | Layer                                    |
|----------|-------------------------|------------------------------------------|
| 1        | Freedom from interference | Intra-VM: task timing isolation        |
| 2        | Inter-VM channel quality  | Hypervisor: shared memory latency      |
| 3        | Network determinism       | External: socket I/O stack             |

Scenario 1 asks whether the safety partition's *computation* is temporally isolated. Scenario 2 asks whether the *communication channel* between partitions is predictable and reliable. These are independent properties: a system can have perfectly isolated computation but a non-deterministic communication channel, or vice versa.

**Motivation:**
In a mixed-criticality SDV platform, safety-relevant partitions do not operate in isolation — they exchange data with other partitions (sensor readings, actuator commands, status signals). The latency and reliability of the inter-partition communication channel directly affect the end-to-end response time of safety functions. If the channel has unpredictable latency under load, the safety case is undermined even if each partition individually meets its timing requirements.

The EB corbos hypervisor exposes inter-VM communication through a shared physical memory region (`hv_proxycomshm`) accessed via a spinlock-protected ring buffer pair (`shmlib_ring_buffer`, 4096 B). This scenario characterises how that channel behaves under increasing system load on the sender side.

---

## 2. Variables

### Independent variables

**Primary — Background load level N:**
Number of concurrent stress-ng worker pairs (`--cache 1 --stream 1`) running on vm-li alongside the ping client. These represent progressively consolidated low-criticality workloads competing with the channel for vm-li's CPU time and memory bandwidth.

Load levels: **N = 0, 1, 2, 4, 6, 8**

- N = 0: baseline — only the ping client runs; no background stress.
- N = 6, 8: heavy oversubscription of vm-li's 2 vCPUs.

Rationale for the `--cache --stream` profile: these stressors directly compete with the ring buffer access pattern (cache line reads/writes, memory bus transactions), making them the most disruptive interference for a shared memory channel.

**Secondary — Message payload size (baseline only, N = 0):**
Tested to isolate size effects from load effects.

Sizes: **8 B, 64 B, 512 B, 3072 B** (within the 4095 B ring buffer limit)

### Dependent variables (metrics)

- **Round-trip latency (RTL, μs)** — time from vm-li writing a ping to vm-li receiving the echo back. Measured entirely on vm-li using `CLOCK_MONOTONIC`; no cross-VM clock synchronisation required.
- **RTL jitter (μs)** — standard deviation of RTL per run. The primary indicator of channel temporal predictability.
- **Tail latency** — P95, P99, P99.9 RTL. Tail growth is more informative than mean growth for safety assessment.
- **Message loss rate (%)** — proportion of pings for which no echo was received within a timeout (500 ms). Tests reliability.
- **Throughput (messages/s)** — secondary metric, indicates channel saturation.

---

## 3. Hypotheses

**H₀ (null):** Load level N has no statistically significant effect on RTL or jitter. The hypervisor's VM partitioning fully isolates the shared memory channel from vm-li's background load.

**H₁:** Mean RTL and jitter increase monotonically with N. Background cache/stream stress on vm-li evicts ring buffer cache lines and delays the poll cycles of the ping client, increasing the time before vm-hi observes the incoming message.

**H₂ (tail growth):** P99.9 RTL grows faster than mean RTL as N increases. Occasional severe delays caused by cache misses or CPU preemption produce a heavy tail that is the most safety-relevant indicator of channel degradation.

**H₃ (reliability):** Message delivery remains 100% reliable (0% loss) at all load levels. The ring buffer's spinlock protection prevents data corruption under concurrent access; the channel may slow down but should not drop messages under a stop-and-wait protocol.

**H₄ (size independence):** At baseline (N = 0), RTL is insensitive to payload size across the tested range (8 B – 3072 B). This would confirm that latency is dominated by scheduling and polling overhead rather than the data copy, which is consistent with QEMU's shared memory model where cache coherency is automatic.

---

## 4. Experimental Setup

### Platform architecture (unchanged from Scenario 1)

- Hypervisor: EB corbos Linux (L4/Fiasco.OC-based), running on QEMU ARM virt machine
- CPU: Emulated ARM Cortex-A57 (ARMv8), 4 vCPUs total
- vm-hi: vCPUs 2–3 (cpus: 0xc), 128 MB RAM — runs `shm_echo` as PID 1
- vm-li: vCPUs 0–1 (cpus: 0x3), 512 MB RAM — runs `shm_client` + optional stress-ng background
- Inter-VM channel: `hv_proxycomshm` (2 MB), ring buffer size 4096 B per direction, spinlock-protected

### Polling strategy and its effect on measured RTL

The ring buffer has no blocking read primitive — consumers must busy-poll using `shmlib_ring_buffer_data_available()`. Measured RTL therefore includes the polling interval of both the echo server (vm-hi) and the client (vm-li):

| Poll strategy | Expected RTL | Trade-off |
|---------------|-------------|-----------|
| Busy-spin (0 sleep) | Minimal (~QEMU vCPU timeslice) | Consumes 100% of a vCPU; under QEMU, this can starve the peer vCPU thread on the host, inflating RTL artificially |
| Short sleep (100 μs between polls) | ~200–600 μs | Realistic; avoids host starvation; reflects how a real application would implement the channel |

This experiment uses **100 μs nanosleep between polls** on both sides. This is a deliberate choice: it models realistic application behaviour and avoids QEMU-specific host CPU starvation artifacts. All RTL measurements are therefore floored at ~200 μs; any values below this indicate measurement error. The poll interval is constant across all conditions, so relative comparisons between load levels remain valid.

### Message structure

```c
/* Defined in scenario2/common/common.h */
struct ping_message {
    uint64_t seq;           /* Sequence number — used to detect loss and reordering */
    uint64_t send_time_ns;  /* vm-li timestamp at send (CLOCK_MONOTONIC)            */
    uint16_t payload_size;  /* Declared payload size                                */
    uint8_t  payload[1];    /* Variable-length padding (up to 3072 B)               */
};
```

`send_time_ns` is stamped on vm-li immediately before `shmlib_ring_buffer_write`. RTL is computed as `t_recv - send_time_ns` where `t_recv` is sampled immediately after the corresponding `shmlib_ring_buffer_read` returns on vm-li. Both timestamps use the same `CLOCK_MONOTONIC` instance, so no synchronisation is needed.

Note: `shmlib_shm_copy_bytes` (byte-by-byte volatile copy) must be used for payload access to avoid SIGBUS on real hardware due to alignment restrictions, as documented in `shmlib_shm.h`.

### Experiment matrix

**Primary (varying N, fixed payload = 64 B, 1000 pings/run):**

| Condition | N workers | shm_client invocation                    | Runs |
|-----------|-----------|------------------------------------------|------|
| C0        | 0         | `shm_client -n 1000 -s 64`              | 10   |
| C1        | 1         | `shm_client -n 1000 -s 64 -C 1 -S 1`   | 10   |
| C2        | 2         | `shm_client -n 1000 -s 64 -C 2 -S 2`   | 10   |
| C3        | 4         | `shm_client -n 1000 -s 64 -C 4 -S 4`   | 10   |
| C4        | 6         | `shm_client -n 1000 -s 64 -C 6 -S 6`   | 10   |
| C5        | 8         | `shm_client -n 1000 -s 64 -C 8 -S 8`   | 10   |

**Secondary (varying payload size, N = 0, 1000 pings/run):**

| Condition | Payload | shm_client invocation           | Runs |
|-----------|---------|---------------------------------|------|
| P1        | 8 B     | `shm_client -n 1000 -s 8`      | 5    |
| P2        | 64 B    | Already covered by C0           | —    |
| P3        | 512 B   | `shm_client -n 1000 -s 512`    | 5    |
| P4        | 3072 B  | `shm_client -n 1000 -s 3072`   | 5    |

**Total: 60 + 15 = 75 runs.** QEMU is rebooted between conditions; multiple runs within the same condition share a single QEMU boot (shm_echo loops waiting for the next handshake).

---

## 5. New Applications

### `shm_echo` (vm-hi — PID 1 for this scenario)

A minimal echo server. It has no periodic task — its sole function is to echo ping messages as fast as possible.

Behaviour:
1. Initialises shared memory, registers with UUID `SHM_ECHO_UUID`.
2. Loops indefinitely: polls `li_to_hi` ring buffer every 100 μs.
3. On receipt of a `ping_message`: writes it back unchanged to `hi_to_li` (echo).
4. Does not accumulate statistics; all measurement is on vm-li.
5. If running as PID 1, enters an idle loop after echo phase (never exits).

### `shm_client` (vm-li)

A ping-pong client with configurable background stress.

Behaviour:
1. Initialises shared memory, waits for `shm_echo` to register (same proxyshm discovery mechanism as stress_workload).
2. Optionally spawns stress-ng background workers (`--cache N --stream N --timeout <duration>s`).
3. Sends `n_messages` pings sequentially (stop-and-wait — one outstanding ping at a time):
   - Stamps `send_time_ns`, writes `ping_message` to `li_to_hi`.
   - Polls `hi_to_li` every 100 μs until echo received or 500 ms timeout.
   - Records `rtl_ns = recv_time_ns - send_time_ns`; increments loss counter on timeout.
   - Verifies `seq` matches; logs reordering if not.
4. Kills stress-ng workers (via SIGTERM to child PID).
5. Prints statistics summary; writes per-message data to CSV.

Command-line interface:
```
shm_client [options]
  -n <count>    Number of ping messages (default: 1000)
  -s <bytes>    Payload size in bytes (default: 64, max: 3072)
  -C <workers>  stress-ng --cache workers (default: 0)
  -S <workers>  stress-ng --stream workers (default: 0)
  -o <file>     Output CSV (default: shm_client_log.csv)
  -h            Show help
```

Console output (captured by experiment script as `run_XX.txt`):
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

CSV columns: `seq, send_time_ns, recv_time_ns, rtl_us, lost`

---

## 6. Run Procedure

1. Build the image with `shm_echo` as the hi VM init binary (`task image`).
2. Start QEMU (`task qemu`), wait for boot and SSH readiness.
3. `shm_echo` starts automatically on vm-hi and begins polling.
4. SSH into vm-li; invoke `shm_client` with the condition's parameters.
5. `shm_client` discovers `shm_echo`, spawns stress-ng (if N > 0), runs 1000 pings, kills stress-ng, prints results.
6. Output captured to `run_XX.txt`; CSV saved to `run_XX.csv`.
7. Repeat steps 4–6 for remaining runs of this condition (no QEMU reboot between runs).
8. Stop QEMU; reboot for next condition.

---

## 7. Acceptance Criteria

**Reliability criterion:** message loss rate = 0% at all load levels (H₃).

**Predictability criteria** at load level N:
- P99 RTL ≤ 3× baseline (C0) P99 RTL.
- RTL jitter (stddev) ≤ 2× baseline jitter.

The **channel degradation threshold N\*** is the smallest N for which at least one predictability criterion is violated. If no criterion is violated up to N = 8, the channel is reported as "predictable across all tested load levels."

---

## 8. Data Analysis and Hypothesis Testing

### For H₀ (no effect of N):
- Kruskal-Wallis test on per-run mean RTL across all 6 conditions.
- If p < 0.05, reject H₀.

### For H₁ (monotonic RTL increase):
- Spearman rank correlation (ρ) between N and per-run mean RTL and jitter.
- Confirm H₁ if ρ > 0.8 and p < 0.05 for at least one metric.
- Line plot of mean RTL ± std vs N.

### For H₂ (tail growth faster than mean):
- Compute ratio P99.9 / mean at each load level.
- If this ratio increases with N, the tail grows disproportionately.
- Plot mean, P99, and P99.9 RTL on the same axis vs N.

### For H₃ (zero message loss):
- Report total lost messages per condition and per run.
- If any loss occurs: report the condition, run index, and sequence number of the first lost message, and diagnose whether it was a ring buffer overflow, timeout, or reordering.

### For H₄ (size independence):
- Plot mean RTL vs payload size at N = 0 (C0 + P1–P4).
- Fit a linear model; report slope (μs/byte) and R².
- If slope × 3072 B < 10% of baseline mean RTL, size effect is negligible and H₄ is confirmed.

### Visualisations:
- Line plot of mean RTL ± std vs N (primary trend)
- Multi-line plot of P50 / P99 / P99.9 RTL vs N (tail growth)
- Box plots of per-run mean RTL per condition (run-to-run variance)
- Bar chart of message loss rate per condition (expected: all zero)
- Scatter plot of RTL vs payload size at N = 0 (H₄)

---

## 9. Platform Constraints and Limitations

**QEMU's shared memory model differs from real hardware.** In QEMU, the proxycomshm region is a host-process mmap. Cache coherency is automatic (no real cache hierarchy between vCPUs), and there is no modelled AXI bus arbitration. On real EBcLfSA hardware, cross-VM shared memory accesses traverse the physical cache hierarchy and memory interconnect, introducing latencies not present in QEMU. RTL values here reflect host-OS vCPU thread scheduling, not physical memory system timing. This is a fundamental limitation that must be stated in the thesis.

**Polling interval floors measurable RTL at ~200 μs.** Any channel behaviour faster than this is not observable. On real hardware with busy-polling, RTL could be orders of magnitude lower. The 100 μs interval is a trade-off chosen for QEMU stability and realistic-application fidelity.

**No cross-VM clock synchronisation.** RTL is measured entirely on vm-li (same `CLOCK_MONOTONIC` for both send and receive timestamps), making it accurate. One-way latency in each direction cannot be separated without clock sync between VMs, which is not available in this environment.

**vm-hi load is not varied.** The effect of additional applications co-located within vm-hi (competing with shm_echo for the ring buffer spinlock) is not tested here. This is a relevant follow-up for characterising intra-partition communication overhead.

---

## 10. Expected Outcomes and Conclusions Framework

Given QEMU's vCPU-thread scheduling model:

- **H₁ is expected to be confirmed:** as N grows, vm-li's vCPUs are increasingly preempted by stress-ng workers, delaying the `shm_client`'s write and poll cycles, which increases RTL.
- **H₂ is expected to be confirmed:** occasional severe preemptions will produce a heavy RTL tail, particularly at N ≥ 4 where vm-li is oversubscribed.
- **H₃ is expected to hold:** the stop-and-wait protocol and spinlock-protected ring buffer should prevent loss; if loss occurs it indicates either a bug or an unexpectedly long preemption exceeding the 500 ms timeout.
- **H₄ is expected to hold:** at QEMU speeds, copying a few kilobytes through the ring buffer is dominated by the polling interval overhead.

**Conclusions to draw:**
- Report N* or state "no degradation threshold within N = 0–8."
- Quantify RTL growth: "at N = X, P99 RTL was Y μs, compared to Z μs at baseline — a W× increase."
- Report whether the channel is reliable under all conditions.
- Relate to the end-to-end safety argument: if the worst-case P99.9 RTL across all tested conditions is X μs, any safety function relying on this channel for data exchange must budget X μs of communication overhead in its worst-case execution time analysis.

**Potential follow-up:**
- Repeat on real EBcLfSA hardware for physically meaningful latency values.
- Test with busy-polling on real hardware (safe from host-starvation effects on bare metal).
- Add a concurrent speedometer RT loop in vm-hi to measure intra-partition co-location effects on channel latency.
- Characterise maximum sustained throughput (pipeline mode: many outstanding messages) vs. the stop-and-wait latency measured here.
