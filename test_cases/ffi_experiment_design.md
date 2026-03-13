1. Problem Statement
"To what extent does EBcLfSA's hypervisor-based partitioning ensure that the timing behaviour of a high-integrity periodic task (speedometer) remains within its specified deadline when subjected to varying levels and types of interference from a concurrent low-integrity workload (stressor) targeting shared hardware resources?”

3. Variables

Independent variables (factors):
•  Number of repetitions per scenario: 10 runs
•  Test duration per run: 30, 60s
•  Speedometer task period : 50ms (20 Hz); ...
•  Deadline: 5ms, 10ms...
•  Stress type: none (baseline); CPU; cache (L2 thrashing); memory bandwidth (stream); I/O; all
•  Stress intensity (number of worker instances per stressor): 0, 1, 2, 4

Baseline (golden run): 10 runs, 60s, 50ms, 10ms, none, 0

Dependent variables (metrics):
•  Wake-up jitter (μs) — deviation from the intended "Speedometer task period"
•  Execution time (μs) — time to complete the speedometer task
•  Response time (μs) — Wake-up jitter + Execution time  (from wakeup to task completion)
•  Deadline miss rate (%) — proportion of cycles where response time > DEADLINE


3. Hypotheses

H₀: The type and intensity of the low-integrity workload have no statistically significant effect on the jitter and execution time of the high-integrity task.

H₁: The type and intensity of the low-integrity workload have a statistically significant effect on the jitter and execution time of the high-integrity task.

H₂: Cache and memory bandwidth stressors cause significantly higher jitter and execution time compared to baseline and CPU-only stress, due to contention on the shared L2 cache and memory interconnect.

H₃: Increasing the number of stressor instances amplifies the observed jitter, indicating a proportional relationship between interference intensity and timing degradation.


4. Experimental Setup

Platform architecture:
•  Hypervisor: EB corbos Linux (L4/Fiasco.OC-based), running on QEMU (ARM virt machine)
•  CPU: Emulated ARM Cortex-A53 (ARMv8), 4 cores
  ◦  L1 caches: private per core
  ◦  L2 cache: shared across all cores (primary interference channel)
  ◦  Memory bus: shared AXI interconnect
•  VM partitioning:
  ◦  vm-hi: cores 2-3 (cpus: 0xc), 128MB RAM, sdk_enable, runs speedometer as init (PID 1)
  ◦  vm-li: cores 0-1 (cpus: 0x3), 512MB RAM, deploys stressor using stress-ng 
•  Inter-VM communication: shared memory ring buffers (proxycomshm, 2MB)
•  Storage: hi_VM root filesystem served via virtio_block through li_VM

Scenarios (experiment matrix):
| #   | Scenario                    | li_app invocation                       |
| --- | --------------------------- | --------------------------------------- |
| S0  | Baseline (no stress)        | `li_app -d 60`                          |
| S1  | CPU stress                  | `li_app -c 0 -d 60`                     |
| S2  | Cache stress (L2 thrashing) | `li_app -C 2 -d 60`                     |
| S3  | Memory bandwidth stress     | `li_app -S 2 -d 60`                     |
| S4  | I/O stress                  | `li_app -i 4 -d 60`                     |
| S5  | Cache + stream combined     | `li_app -C 2 -S 2 -d 60`                |
| S6  | Worst-case combined         | `li_app -c 0 -C 2 -S 2 -v 2 -i 2 -d 60` |

Each scenario is repeated N times (e.g., 10) to account for QEMU scheduling non-determinism.

5. Tools and Procedures

Tools developed:
•  Speedometer RT application (speedometer.c): periodic task running on vm-hi. Measures per-cycle execution time, wake-up jitter, and response time. Detects deadline misses (response_time > 10ms). Reports statistics via shared memory and console output. Supports multiple consecutive runs without image rebuild.
•  Stress workload orchestrator (stress_workload.c): runs on vm-li. Synchronizes with speedometer via shared memory (START/STOP commands). Invokes stress-ng with configurable stressors. Supports baseline mode (no stressors). Collects and displays results from the hi VM.
•  stress-ng (third-party): generates targeted system load on the li VM. Stressors used: --cpu, --cache, --stream, --vm, --io.

Metrics collected per run:
•  Execution time: mean, min, max, P50, P95, P99, P99.9
•  Jitter: mean, min, max
•  Deadline miss count and rate

Procedure for each scenario:
1. Boot the platform (QEMU + hypervisor)
2. vm-hi starts speedometer, which initializes shared memory and waits for START
3. On vm-li, invoke li_app with the scenario's parameters
4. li_app sends START → speedometer begins its RT loop
5. stress-ng runs (or baseline sleep) for the configured duration
6. li_app sends STOP → speedometer terminates loop, computes statistics, sends results via shared memory
7. li_app prints received statistics
8. Repeat (speedometer waits for the next START command)

6. Run Experiments and Collect Data

For each of the 7 scenarios (S0–S6), run 10 iterations. Record:
•  Console output (statistics summary)
•  Per-run aggregates: total cycles, deadline misses, mean/max jitter, mean/max execution time
•  stress-ng metrics (bogo ops/s) to confirm the stressor was active

This yields 70 total runs. The speedometer's re-run capability allows all iterations of a scenario to be executed without rebooting.

7. Perform Data Analysis and Test Hypothesis

For H₀ (no significant effect on jitter/execution time):
•  Compare jitter and execution time distributions across S0 (baseline) vs S1–S6 (stressed scenarios)
•  Use statistical tests (e.g., Kruskal-Wallis across all scenarios, followed by pairwise Mann-Whitney U tests with Bonferroni correction) to determine if the differences are statistically significant
•  If p < 0.05 for any scenario vs baseline, reject H₀ — interference is measurable

For H₁ (cache/memory stressors cause the most interference due to shared L2):
•  Compare the magnitude of jitter increase across stressor types: S1 (CPU) vs S2 (cache) vs S3 (stream) vs S4 (I/O) vs S5 (cache+stream)
•  Rank scenarios by median and max jitter increase relative to baseline
•  Confirm or reject whether cache and stream stressors (S2, S3, S5) cause significantly higher degradation than CPU-only (S1) and I/O-only (S4), which would confirm that the shared L2 cache and memory bus are the dominant interference channels

For H₂ (worst-case response time stays below 5ms):
•  Report the worst-case response time observed across all runs and scenarios
•  Compare against the 5ms deadline
•  Compute the safety margin: (deadline - worst_case_response_time) / deadline
•  If worst-case response time < 10ms in all scenarios, confirm H₂ — the platform provides sufficient FFI for this task

Visualization:
•  Box plots of max jitter per scenario (across N runs) — directly supports H₀ and H₁
•  Time series of per-cycle jitter for representative runs (baseline vs worst-case) — shows interference patterns
•  Bar chart of mean and max execution time by scenario — supports H₁ ranking
•  Summary table of worst-case response time and safety margin per scenario — supports H₂

8. Draw Conclusions

Based on the data analysis:
•  H₀: State whether interference is statistically measurable. If rejected, quantify by how much jitter/execution time increases under stress compared to baseline (e.g., "max jitter increased by X% under cache stress")
•  H₁: Identify which shared hardware resource contributes most to timing degradation. Confirm or reject whether cache and memory bandwidth stressors dominate over CPU and I/O stressors
•  H₂: State whether the deadline was met across all scenarios and report the safety margin. This is the core FFI conclusion
•  Overall FFI assessment: Even if H₀ is rejected (interference exists) and H₁ is confirmed (it comes from shared L2/memory bus), H₂ may still hold (the interference is bounded within safe limits). This would demonstrate that the platform provides adequate FFI despite imperfect hardware-level isolation
•  Limitations: QEMU does not accurately model L2 cache contention or memory bus arbitration — timing results reflect QEMU's virtual CPU scheduling, not true hardware behavior. SDK syscall restrictions prevented SCHED_FIFO, mlockall, and clock_nanosleep from functioning as intended, which affects the RT guarantees. Results are specific to this QEMU environment and should be validated on real hardware
•  Potential follow-up: Repeat on real hardware to get accurate cache/bus contention data; test with longer durations for higher statistical confidence; test with tighter deadlines to find the FFI breaking point; test with a more computationally intensive hi task to stress the execution time component; vary stressor intensity (worker count) to characterize the interference scaling relationship