# Scenario 1: Isolation and Freedom From Interference - Test Cases

## Test Overview
This document defines test cases for evaluating Freedom From Interference (FFI) in EB corbos Linux for Safety Applications using a mixed-criticality system with a high-criticality instrument cluster speedometer application.

## System Under Test
- **Platform**: EB corbos Linux for Safety Applications
- **Deployment**: C++ applications on Ubuntu 22.04 images via SDK
- **High-Criticality Application**: Instrument Cluster Speedometer
  - **Criticality Level**: ASIL-B 
  - **Update Period**: 20ms (50Hz refresh rate)
  - **Deadline**: 30ms (based on safety analysis for braking/acceleration functions)
  - **Rationale**: At 250 km/h, 30ms allows detection within ~2 meters of travel distance

## Test Applications

### High-Criticality Application: Speedometer
**Purpose**: Emulate safety-critical instrument cluster speedometer functionality

**Implementation Requirements**:
- Periodic task with 20ms period
- Reads vehicle speed from simulated sensor input
- Updates display buffer with current speed value
- Logs timestamp for each update cycle
- Monitors and records execution jitter

**C++ Implementation Components**:
- Real-time periodic timer (POSIX timers or clock_nanosleep)
- Fixed-priority scheduling (SCHED_FIFO with priority 80-99)
- CPU affinity pinning to dedicated core(s)
- Memory pre-allocation and locking (mlockall)
- Execution time measurement (clock_gettime with CLOCK_MONOTONIC)

### Low-Criticality Applications

#### 1. CPU-Intensive Workload
**Purpose**: Generate computational interference

**Implementation**:
- Continuous mathematical operations (matrix multiplication, FFT)
- No I/O operations
- SCHED_OTHER scheduling policy
- Adjustable intensity (number of threads: 1, 2, 4)

#### 2. Memory-Intensive Workload
**Purpose**: Generate cache and memory bandwidth contention

**Implementation**:
- Large memory allocations (configurable: 512MB, 1GB, 2GB)
- Random memory access patterns to maximize cache misses
- Continuous read/write operations
- SCHED_OTHER scheduling policy

#### 3. I/O-Intensive Workload
**Purpose**: Generate storage subsystem interference

**Implementation**:
- Continuous file write operations
- Configurable write rate (1MB/s, 10MB/s, 50MB/s)
- Synchronous I/O (fsync) to maximize system impact
- SCHED_OTHER scheduling policy

#### 4. Misbehaving Application
**Purpose**: Simulate compromised or faulty component

**Implementation**:
- Aggressive resource consumption:
  - CPU: busy loops across all available cores
  - Memory: rapid allocation without bounds checking
  - I/O: burst write operations with high frequency
- Attempts to escalate privileges (if applicable)
- SCHED_OTHER scheduling policy

## Test Cases

### TC1.1: Baseline Execution (No Interference)
**Objective**: Establish baseline performance metrics for speedometer application

**Preconditions**:
- Only speedometer application running
- System idle except for OS services
- CPU governor set to performance mode

**Test Steps**:
1. Deploy speedometer application with RT priority (SCHED_FIFO, priority 90)
2. Execute for 60 seconds (3000 update cycles)
3. Collect execution metrics

**Expected Results**:
- Mean execution time: < 5ms
- Maximum jitter: < 1ms
- Deadline miss ratio: 0%
- CPU utilization: < 5%

**Pass Criteria**: Zero deadline misses, jitter within 1ms

---

### TC1.2: CPU Interference - Single Thread
**Objective**: Evaluate temporal interference from CPU-intensive workload

**Preconditions**:
- Speedometer application configured as baseline
- CPU-intensive workload ready (1 thread)

**Test Steps**:
1. Start speedometer application
2. After 10s, start CPU-intensive workload (1 thread)
3. Run both for 60 seconds
4. Stop CPU workload, continue speedometer for 10s
5. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 10% vs baseline
- Deadline miss ratio: 0%
- Speedometer CPU utilization: stable
- Background CPU utilization: 95-100% on one core

**Pass Criteria**: Zero deadline misses, jitter increase < 15%

---

### TC1.3: CPU Interference - Multi-Thread
**Objective**: Evaluate temporal interference from multi-threaded CPU workload

**Preconditions**:
- Speedometer application configured as baseline
- CPU-intensive workload ready (4 threads)

**Test Steps**:
1. Start speedometer application
2. After 10s, start CPU-intensive workload (4 threads)
3. Run both for 60 seconds
4. Stop CPU workload, continue speedometer for 10s
5. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 20% vs baseline
- Deadline miss ratio: 0%
- Speedometer maintains execution guarantees

**Pass Criteria**: Zero deadline misses, jitter increase < 25%

---

### TC1.4: Memory Interference - Moderate Load
**Objective**: Evaluate spatial interference from memory contention

**Preconditions**:
- Speedometer application configured as baseline
- Memory-intensive workload ready (512MB allocation)

**Test Steps**:
1. Start speedometer application with locked memory
2. After 10s, start memory-intensive workload
3. Run both for 60 seconds
4. Stop memory workload, continue speedometer for 10s
5. Collect metrics and cache statistics

**Expected Results**:
- Maximum jitter increase: < 15% vs baseline
- Deadline miss ratio: 0%
- Speedometer memory footprint: stable
- No swapping or page faults for speedometer

**Pass Criteria**: Zero deadline misses, no memory-related delays

---

### TC1.5: Memory Interference - Heavy Load
**Objective**: Evaluate spatial interference under aggressive memory pressure

**Preconditions**:
- Speedometer application configured as baseline
- Memory-intensive workload ready (2GB allocation, aggressive access)

**Test Steps**:
1. Start speedometer application with locked memory
2. After 10s, start memory-intensive workload
3. Run both for 60 seconds
4. Monitor system memory pressure
5. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 30% vs baseline
- Deadline miss ratio: 0%
- Speedometer maintains memory isolation
- System may swap background workload

**Pass Criteria**: Zero deadline misses, speedometer unaffected by swapping

---

### TC1.6: I/O Interference - Moderate Rate
**Objective**: Evaluate resource interference from I/O operations

**Preconditions**:
- Speedometer application configured as baseline
- I/O-intensive workload ready (10MB/s write rate)

**Test Steps**:
1. Start speedometer application
2. After 10s, start I/O-intensive workload
3. Run both for 60 seconds
4. Monitor disk I/O statistics
5. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 10% vs baseline
- Deadline miss ratio: 0%
- Speedometer execution time: stable
- I/O workload does not block speedometer

**Pass Criteria**: Zero deadline misses, minimal jitter increase

---

### TC1.7: I/O Interference - High Rate
**Objective**: Evaluate resource interference under heavy I/O load

**Preconditions**:
- Speedometer application configured as baseline
- I/O-intensive workload ready (50MB/s write rate with fsync)

**Test Steps**:
1. Start speedometer application
2. After 10s, start I/O-intensive workload
3. Run both for 60 seconds
4. Monitor system I/O wait time
5. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 20% vs baseline
- Deadline miss ratio: 0%
- Speedometer not blocked by I/O operations

**Pass Criteria**: Zero deadline misses, speedometer unaffected by I/O wait

---

### TC1.8: Combined Interference - Moderate
**Objective**: Evaluate FFI under multiple concurrent interference sources

**Preconditions**:
- Speedometer application configured as baseline
- All low-criticality workloads ready (moderate settings)

**Test Steps**:
1. Start speedometer application
2. After 10s, start CPU workload (2 threads)
3. After 20s, add memory workload (512MB)
4. After 30s, add I/O workload (10MB/s)
5. Run all for 60 seconds
6. Stop workloads sequentially, monitor recovery
7. Collect metrics

**Expected Results**:
- Maximum jitter increase: < 30% vs baseline
- Deadline miss ratio: 0%
- Speedometer maintains all timing guarantees
- System resources saturated but isolated

**Pass Criteria**: Zero deadline misses, bounded jitter degradation

---

### TC1.9: Combined Interference - Heavy
**Objective**: Evaluate FFI under aggressive multi-dimensional interference

**Preconditions**:
- Speedometer application configured as baseline
- All low-criticality workloads ready (maximum settings)

**Test Steps**:
1. Start speedometer application
2. Simultaneously start all interference workloads:
   - CPU: 4 threads
   - Memory: 2GB aggressive access
   - I/O: 50MB/s with fsync
3. Run all for 120 seconds
4. Collect metrics throughout execution

**Expected Results**:
- Maximum jitter: must remain below 10ms (50% of deadline margin)
- Deadline miss ratio: 0%
- Speedometer maintains deterministic behavior
- System under full load

**Pass Criteria**: Zero deadline misses, worst-case jitter < 10ms

---

### TC1.10: Misbehaving Application - Resource Exhaustion
**Objective**: Evaluate FFI against faulty/compromised component

**Preconditions**:
- Speedometer application configured as baseline
- Misbehaving application ready

**Test Steps**:
1. Start speedometer application
2. After 10s, start misbehaving application
3. Monitor system behavior for 60 seconds or until intervention
4. Observe OS containment mechanisms
5. Collect metrics

**Expected Results**:
- Speedometer deadline miss ratio: 0%
- OS detects and contains misbehaving application
- System remains stable
- Speedometer isolation maintained

**Pass Criteria**: Zero deadline misses, OS successfully isolates fault

---

### TC1.11: Misbehaving Application - Priority Inversion
**Objective**: Evaluate protection against priority manipulation attempts

**Preconditions**:
- Speedometer application configured as baseline
- Misbehaving application attempts priority escalation

**Test Steps**:
1. Start speedometer application (priority 90)
2. Start misbehaving application attempting to:
   - Request SCHED_FIFO scheduling
   - Acquire high priority (if allowed)
   - Compete for RT resources
3. Run for 60 seconds
4. Collect metrics

**Expected Results**:
- Priority escalation denied or contained
- Speedometer maintains priority guarantees
- No priority inversion occurs
- Deadline miss ratio: 0%

**Pass Criteria**: Speedometer unaffected, priority isolation enforced

---

### TC1.12: Long-Duration Stress Test
**Objective**: Evaluate FFI stability over extended operation

**Preconditions**:
- Speedometer application configured as baseline
- Combined interference workloads ready

**Test Steps**:
1. Start speedometer application
2. Start all interference workloads (moderate-to-heavy settings)
3. Run continuously for 8 hours
4. Collect metrics every 30 minutes
5. Analyze trends and anomalies

**Expected Results**:
- Consistent deadline miss ratio: 0%
- No performance degradation over time
- Stable jitter distribution
- No resource leaks or drift

**Pass Criteria**: Zero deadline misses maintained throughout duration

---

## Metrics Collection

### Per-Test Metrics
- **Execution Time Statistics**:
  - Mean, median, min, max, standard deviation
  - 95th, 99th, 99.9th percentile
- **Jitter Measurements**:
  - Period jitter (time between consecutive activations)
  - Execution jitter (variation in execution duration)
- **Deadline Adherence**:
  - Total activations
  - Deadline misses (count and percentage)
  - Worst-case response time
- **Resource Utilization**:
  - Per-core CPU usage
  - Memory usage (RSS, VSZ)
  - Cache statistics (L1/L2/L3 misses)
  - Context switches
  - Page faults

### System-Level Metrics
- Total CPU utilization across all cores
- System memory pressure and swap usage
- I/O wait time and throughput
- Scheduler statistics and latency traces

## Test Environment Configuration

### Hardware Requirements
- Multi-core processor (minimum 4 cores)
- Minimum 8GB RAM
- SSD storage for consistent I/O performance

### Software Configuration
- EB corbos Linux for Safety Applications
- Real-time kernel patches enabled
- CPU isolation (isolcpus) for speedometer cores
- IRQ affinity configured to avoid RT cores
- Disable frequency scaling on RT cores

### Isolation Mechanisms to Validate
- CFS bandwidth control (cgroups)
- Memory cgroups with limits
- CPU affinity and isolation
- I/O scheduling classes (CFQ/deadline)
- Real-time throttling configuration

## Success Criteria Summary

### Critical Requirements (Must Pass)
- **Zero deadline misses** in all test cases
- **Worst-case jitter < 10ms** (33% of deadline)
- **No memory interference** (locked pages remain resident)
- **Deterministic behavior** under all interference conditions

### Performance Requirements
- Jitter increase < 30% under combined interference
- Recovery to baseline within 5s after interference removal
- Consistent behavior over 8-hour duration

## Test Execution Tools

### Recommended C++ Implementation Components
- **Timing**: `clock_gettime()`, `clock_nanosleep()`
- **Scheduling**: `sched_setscheduler()`, `pthread_setschedparam()`
- **Memory**: `mlockall()`, `mlock()`, `mmap()`
- **Affinity**: `sched_setaffinity()`, `pthread_setaffinity_np()`
- **Logging**: Lock-free ring buffer for minimal interference

### Analysis Tools
- `cyclictest` - for baseline RT latency validation
- `stress-ng` - for generating interference workloads
- `perf` - for performance counter analysis
- Custom instrumentation in speedometer application
- Post-processing scripts for statistical analysis

## References
- ISO 26262: Road vehicles — Functional safety
- AUTOSAR Adaptive Platform specifications
- Mixed-criticality interference model (Arena et al.)
- Safety timing threshold: 30ms (based on HARA for braking/acceleration at 250 km/h = ~2m travel distance)
