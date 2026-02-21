# Speedometer Real-Time Application

High-criticality instrument cluster speedometer application for evaluating ISO 26262 Freedom From Interference (FFI) on EB corbos Linux for Safety Applications.

## Overview

This application emulates a safety-critical speedometer function in an automotive instrument cluster, designed to test real-time performance and isolation guarantees in mixed-criticality systems.

### Key Specifications

- **Criticality Level**: ASIL-B representative
- **Update Period**: 20ms (50Hz refresh rate)
- **Deadline**: 30ms (based on safety analysis for braking/acceleration functions at 250 km/h ≈ 2m detection distance)
- **Scheduling**: SCHED_FIFO with priority 90
- **Memory**: Pre-allocated and locked to prevent paging

## Features

- **Real-time periodic execution** using POSIX timers and `clock_nanosleep()`
- **SCHED_FIFO scheduling** with configurable priority
- **Memory locking** (`mlockall`) to prevent page faults
- **CPU affinity** support for core isolation
- **Comprehensive metrics collection**:
  - Execution time statistics (mean, std dev, percentiles)
  - Jitter measurements (period and execution)
  - Deadline miss detection and tracking
  - Detailed per-cycle CSV logging
- **Graceful shutdown** with signal handling (Ctrl+C)
- **Simulated speedometer behavior** with dynamic speed changes
- **Complete interference workload suite** for FFI testing

## Low-Criticality Interference Applications

For Scenario 1 testing, four interference workloads are provided:

### 1. CPU Workload
Generates temporal interference through compute-intensive operations.

**Features**:
- Matrix multiplication (256×256)
- Trigonometric calculations (sin, cos, tan, exp)
- Configurable number of threads

**Usage**:
```bash
./cpu_workload -t 4  # 4 CPU threads
```

### 2. Memory Workload
Generates spatial interference through memory pressure and cache contention.

**Features**:
- Large memory allocation (configurable)
- Random access patterns (maximize cache misses)
- Sequential memory bandwidth stress

**Usage**:
```bash
./memory_workload -m 2048  # 2GB allocation
```

### 3. I/O Workload
Generates resource interference through storage I/O operations.

**Features**:
- Continuous file writes with fsync
- Configurable write rate
- Maximum I/O system pressure

**Usage**:
```bash
./io_workload -r 50  # 50 MB/s write rate
```

### 4. Misbehaving Application
Simulates a faulty or compromised low-criticality component.

**Features**:
- Aggressive CPU consumption (all cores)
- Uncontrolled memory allocation
- Burst I/O operations
- Attempts privilege escalation (should fail)

**Usage**:
```bash
./misbehaving_app           # All attacks enabled
./misbehaving_app --cpu-only    # CPU attack only
```

## Building

### Option 1: Using Make (Recommended for Quick Build)

```bash
make  # Builds speedometer + all interference workloads
```

### Option 2: Using CMake

```bash
mkdir build
cd build
cmake ..
make
```

### Build Requirements

- C++17 compatible compiler (g++ or clang++)
- POSIX real-time extensions (librt)
- pthreads support

## Usage

### Basic Usage

```bash
sudo ./speedometer
```

**Note**: `sudo` or appropriate capabilities (`CAP_SYS_NICE`, `CAP_IPC_LOCK`) are required for:
- Real-time scheduling (SCHED_FIFO)
- Memory locking (mlockall)

### Command Line Options

```bash
./speedometer [options]

Options:
  -d, --duration <seconds>  Test duration in seconds (default: 60)
  -c, --core <cpu>          Pin to specific CPU core (optional)
  -o, --output <file>       Output CSV log file (default: speedometer_log.csv)
  -h, --help                Show help message
```

### Examples

```bash
# Run for 120 seconds
sudo ./speedometer -d 120

# Run pinned to CPU core 2
sudo ./speedometer -c 2

# Custom output file
sudo ./speedometer -o test1_baseline.csv

# Combine options
sudo ./speedometer -d 300 -c 3 -o longrun.csv
```

## System Configuration

### Recommended Linux Kernel Configuration

For optimal real-time performance on EB corbos Linux:

1. **CPU Isolation**: Isolate cores for real-time tasks
   ```bash
   # Add to kernel boot parameters
   isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
   ```

2. **IRQ Affinity**: Move interrupts away from RT cores
   ```bash
   # Set IRQ affinity to non-RT cores (0,1)
   echo 3 > /proc/irq/default_smp_affinity
   ```

3. **CPU Frequency Scaling**: Disable on RT cores
   ```bash
   cpupower frequency-set -g performance
   ```

4. **Real-time Throttling**: Disable RT bandwidth control (if needed)
   ```bash
   echo -1 > /proc/sys/kernel/sched_rt_runtime_us
   ```

### Required Capabilities

Instead of running as root, grant specific capabilities:

```bash
# Grant capabilities to the binary
sudo setcap cap_sys_nice,cap_ipc_lock+ep ./speedometer

# Verify
getcap ./speedometer
```

## Output

### Console Output

The application displays:
- Configuration summary (period, deadline, priority)
- Progress updates every ~10 seconds
- Final statistics summary with:
  - Total cycles and deadline misses
  - Execution time statistics (mean, std dev, min, max, percentiles)
  - Jitter measurements (mean, min, max)

Example:
```
========== SPEEDOMETER RT APPLICATION RESULTS ==========

Test Configuration:
  Period: 20.000 ms
  Deadline: 30.000 ms
  RT Priority: 90 (SCHED_FIFO)

Execution Statistics:
  Total cycles: 3000
  Deadline misses: 0 (0.000%)

Execution Time (μs):
  Mean: 2.345
  Std Dev: 0.123
  Min: 2.100
  Max: 3.890
  50th percentile: 2.320
  95th percentile: 2.560
  99th percentile: 2.780
  99.9th percentile: 3.120

Jitter (μs):
  Mean: 5.234
  Min: 0.120
  Max: 45.670

========================================================
```

### CSV Log File

Detailed per-cycle data is written to a CSV file with columns:
- `cycle`: Cycle number
- `execution_time_ns`: Execution time in nanoseconds
- `execution_time_us`: Execution time in microseconds
- `jitter_ns`: Absolute jitter in nanoseconds
- `jitter_us`: Absolute jitter in microseconds
- `deadline_miss`: 1 if deadline missed, 0 otherwise

## Test Scenarios

This application is designed for Scenario 1 test cases (TC1.1 - TC1.12):

### TC1.1: Baseline Execution
```bash
# Isolate system, run baseline
sudo ./speedometer -d 60 -c 2 -o baseline.csv
```

### TC1.2: CPU Interference - Single Thread
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_2_cpu_single.csv

# Terminal 2: Start CPU-intensive workload after 10s
sleep 10 && ./cpu_workload -t 1
```

### TC1.3: CPU Interference - Multi-Thread
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_3_cpu_multi.csv

# Terminal 2: Start CPU-intensive workload (4 threads)
sleep 10 && ./cpu_workload -t 4
```

### TC1.4: Memory Interference - Moderate Load
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_4_mem_moderate.csv

# Terminal 2: Start memory-intensive workload (512MB)
sleep 10 && ./memory_workload -m 512
```

### TC1.5: Memory Interference - Heavy Load
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_5_mem_heavy.csv

# Terminal 2: Start memory-intensive workload (2GB)
sleep 10 && ./memory_workload -m 2048
```

### TC1.6: I/O Interference - Moderate Rate
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_6_io_moderate.csv

# Terminal 2: Start I/O-intensive workload (10MB/s)
sleep 10 && ./io_workload -r 10
```

### TC1.7: I/O Interference - High Rate
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_7_io_high.csv

# Terminal 2: Start I/O-intensive workload (50MB/s)
sleep 10 && ./io_workload -r 50
```

### TC1.8: Combined Interference - Moderate
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 120 -c 2 -o tc1_8_combined_moderate.csv

# Terminal 2: Start all moderate interference workloads
sleep 10
./cpu_workload -t 2 &
sleep 10
./memory_workload -m 512 &
sleep 10
./io_workload -r 10 &
```

### TC1.9: Combined Interference - Heavy
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 120 -c 2 -o tc1_9_combined_heavy.csv

# Terminal 2: Start all heavy interference workloads simultaneously
sleep 10
./cpu_workload -t 4 &
./memory_workload -m 2048 &
./io_workload -r 50 &
```

### TC1.10: Misbehaving Application - Resource Exhaustion
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_10_misbehaving.csv

# Terminal 2: Start misbehaving application
sleep 10 && ./misbehaving_app
```

### TC1.11: Misbehaving Application - Priority Inversion
```bash
# Terminal 1: Start speedometer
sudo ./speedometer -d 90 -c 2 -o tc1_11_priority_attack.csv

# Terminal 2: Start misbehaving application
sleep 10 && ./misbehaving_app --cpu-only
```

## Success Criteria

Based on ISO 26262 requirements and test case specifications:

### Critical Requirements (Must Pass)
- ✅ **Zero deadline misses** (< 30ms execution time)
- ✅ **Worst-case jitter < 10ms** (33% of deadline margin)
- ✅ **Deterministic behavior** under all interference conditions

### Performance Requirements
- ✅ Jitter increase < 30% under combined interference
- ✅ Mean execution time < 5ms
- ✅ 99.9th percentile < 20ms

## Analysis Tools

### Post-Processing with Python

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load log file
df = pd.read_csv('speedometer_log.csv')

# Plot execution time distribution
df['execution_time_us'].hist(bins=100)
plt.xlabel('Execution Time (μs)')
plt.ylabel('Frequency')
plt.title('Execution Time Distribution')
plt.show()

# Calculate statistics
print(f"Deadline misses: {df['deadline_miss'].sum()}")
print(f"Max jitter: {df['jitter_us'].max()} μs")
```

### Using Linux perf

```bash
# Record performance counters
sudo perf record -e cycles,cache-misses,context-switches ./speedometer -d 60

# Analyze results
sudo perf report
```

## Architecture

### Key Components

1. **SpeedometerTask**: Simulates vehicle speed sensor reading and display update
2. **Real-time Loop**: Periodic execution using `clock_nanosleep()` with absolute time
3. **Statistics Collection**: Non-intrusive logging using pre-allocated vectors
4. **Signal Handling**: Graceful shutdown on SIGINT/SIGTERM

### Timing Model

```
Period (20ms)
├─ Wakeup (clock_nanosleep)
├─ Jitter Measurement
├─ Task Execution (speedometer.execute())
├─ Deadline Check (< 30ms)
└─ Statistics Update
```

## Limitations

- **MacOS Note**: This application requires Linux-specific features (SCHED_FIFO, `clock_nanosleep` with TIMER_ABSTIME). It will compile on macOS but real-time features will not function properly.
- **Simulation**: This is a simulated workload. Actual speedometer implementations would interface with real hardware sensors and display controllers.
- **Single-threaded**: This implementation uses a single periodic task. Real systems may have multiple concurrent tasks.

## References

- ISO 26262: Road vehicles — Functional safety
- POSIX.1-2017: Real-time extensions
- Mixed-criticality interference model (Arena et al.)
- EB corbos Linux for Safety Applications documentation

## License

This is evaluation software for academic/research purposes related to automotive safety platform assessment.

## Author

Created for MEI dissertation: Evaluation of EB corbos Linux for Safety Applications for next-generation automotive vehicles.
