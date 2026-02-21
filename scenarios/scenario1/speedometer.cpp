#include <iostream>
#include <fstream>
#include <cstring>
#include <ctime>
#include <cmath>
#include <csignal>
#include <atomic>
#include <thread>
#include <vector>
#include <iomanip>
#include <algorithm>
#include <unistd.h>
#include <sched.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/resource.h>

// Configuration constants
constexpr uint64_t PERIOD_NS = 20'000'000;        // 20ms period (50Hz)
constexpr uint64_t DEADLINE_NS = 30'000'000;      // 30ms deadline
constexpr int RT_PRIORITY = 90;                   // Real-time priority
constexpr int TEST_DURATION_SEC = 60;             // Test duration in seconds
constexpr size_t PRE_ALLOC_SIZE = 10 * 1024 * 1024; // 10MB pre-allocated memory

// Global control flag
std::atomic<bool> g_running(true);

// Statistics structure
struct Statistics {
    uint64_t total_cycles = 0;
    uint64_t deadline_misses = 0;
    uint64_t min_execution_ns = UINT64_MAX;
    uint64_t max_execution_ns = 0;
    uint64_t sum_execution_ns = 0;
    uint64_t sum_squared_execution_ns = 0;
    uint64_t min_jitter_ns = UINT64_MAX;
    uint64_t max_jitter_ns = 0;
    uint64_t sum_jitter_ns = 0;
    std::vector<uint64_t> execution_times;
    std::vector<uint64_t> jitter_values;
};

// Utility: Get current time in nanoseconds
inline uint64_t get_time_ns() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL + ts.tv_nsec;
}

// Utility: Convert nanoseconds to timespec
inline struct timespec ns_to_timespec(uint64_t ns) {
    struct timespec ts;
    ts.tv_sec = ns / 1'000'000'000ULL;
    ts.tv_nsec = ns % 1'000'000'000ULL;
    return ts;
}

// Signal handler for graceful shutdown
void signal_handler(int signum) {
    std::cout << "\nReceived signal " << signum << ", shutting down gracefully..." << std::endl;
    g_running = false;
}

// Configure real-time scheduling
bool configure_realtime(int priority) {
    struct sched_param param;
    param.sched_priority = priority;
    
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        std::cerr << "Failed to set SCHED_FIFO: " << strerror(errno) << std::endl;
        std::cerr << "Note: Run with appropriate privileges (e.g., sudo) or set CAP_SYS_NICE" << std::endl;
        return false;
    }
    
    std::cout << "Real-time scheduling configured: SCHED_FIFO, priority " << priority << std::endl;
    return true;
}

// Lock memory to prevent paging
bool lock_memory() {
    // Lock all current and future pages
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        std::cerr << "Failed to lock memory: " << strerror(errno) << std::endl;
        std::cerr << "Note: Run with appropriate privileges or increase locked memory limit" << std::endl;
        return false;
    }
    
    // Pre-fault stack
    unsigned char dummy[8192];
    memset(dummy, 0, sizeof(dummy));
    
    std::cout << "Memory locked successfully" << std::endl;
    return true;
}

// Set CPU affinity (optional - pin to specific core)
bool set_cpu_affinity(int cpu_core) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(cpu_core, &cpuset);
    
    if (sched_setaffinity(0, sizeof(cpu_set_t), &cpuset) != 0) {
        std::cerr << "Warning: Failed to set CPU affinity to core " << cpu_core 
                  << ": " << strerror(errno) << std::endl;
        return false;
    }
    
    std::cout << "CPU affinity set to core " << cpu_core << std::endl;
    return true;
}

// Speedometer task: simulate reading vehicle speed and updating display
class SpeedometerTask {
private:
    double current_speed_kmh;
    double target_speed_kmh;
    uint64_t update_count;
    
public:
    SpeedometerTask() : current_speed_kmh(0.0), target_speed_kmh(120.0), update_count(0) {}
    
    // Simulate sensor reading and display update
    void execute() {
        // Simulate speed changes (smooth acceleration/deceleration)
        const double acceleration = 0.5; // km/h per update
        
        if (std::abs(current_speed_kmh - target_speed_kmh) > acceleration) {
            if (current_speed_kmh < target_speed_kmh) {
                current_speed_kmh += acceleration;
            } else {
                current_speed_kmh -= acceleration;
            }
        } else {
            current_speed_kmh = target_speed_kmh;
            // Change target occasionally
            if (update_count % 200 == 0) {
                target_speed_kmh = 60.0 + (rand() % 120);
            }
        }
        
        // Simulate display buffer update (some computation)
        volatile double display_value = current_speed_kmh * 1.60934; // Convert to mph
        display_value = std::round(display_value * 10.0) / 10.0;
        
        // Simulate checking warning thresholds
        volatile bool overspeed = (current_speed_kmh > 130.0);
        volatile bool critical_speed = (current_speed_kmh > 200.0);
        (void)overspeed;      // Suppress unused warning
        (void)critical_speed; // Suppress unused warning
        
        update_count++;
    }
    
    uint64_t get_update_count() const { return update_count; }
    double get_current_speed() const { return current_speed_kmh; }
};

// Calculate statistics
void calculate_statistics(const Statistics& stats) {
    if (stats.total_cycles == 0) {
        std::cout << "No data collected" << std::endl;
        return;
    }
    
    double mean_execution = static_cast<double>(stats.sum_execution_ns) / stats.total_cycles;
    double variance = (static_cast<double>(stats.sum_squared_execution_ns) / stats.total_cycles) 
                      - (mean_execution * mean_execution);
    double std_dev = std::sqrt(variance);
    
    double mean_jitter = stats.total_cycles > 1 ? 
                         static_cast<double>(stats.sum_jitter_ns) / (stats.total_cycles - 1) : 0.0;
    
    // Calculate percentiles
    auto sorted_exec = stats.execution_times;
    std::sort(sorted_exec.begin(), sorted_exec.end());
    
    auto get_percentile = [&](double p) -> uint64_t {
        if (sorted_exec.empty()) return 0;
        size_t idx = static_cast<size_t>(p * sorted_exec.size());
        if (idx >= sorted_exec.size()) idx = sorted_exec.size() - 1;
        return sorted_exec[idx];
    };
    
    std::cout << "\n========== SPEEDOMETER RT APPLICATION RESULTS ==========" << std::endl;
    std::cout << std::fixed << std::setprecision(3);
    
    std::cout << "\nTest Configuration:" << std::endl;
    std::cout << "  Period: " << PERIOD_NS / 1000000.0 << " ms" << std::endl;
    std::cout << "  Deadline: " << DEADLINE_NS / 1000000.0 << " ms" << std::endl;
    std::cout << "  RT Priority: " << RT_PRIORITY << " (SCHED_FIFO)" << std::endl;
    
    std::cout << "\nExecution Statistics:" << std::endl;
    std::cout << "  Total cycles: " << stats.total_cycles << std::endl;
    std::cout << "  Deadline misses: " << stats.deadline_misses 
              << " (" << (100.0 * stats.deadline_misses / stats.total_cycles) << "%)" << std::endl;
    
    std::cout << "\nExecution Time (μs):" << std::endl;
    std::cout << "  Mean: " << mean_execution / 1000.0 << std::endl;
    std::cout << "  Std Dev: " << std_dev / 1000.0 << std::endl;
    std::cout << "  Min: " << stats.min_execution_ns / 1000.0 << std::endl;
    std::cout << "  Max: " << stats.max_execution_ns / 1000.0 << std::endl;
    std::cout << "  50th percentile: " << get_percentile(0.50) / 1000.0 << std::endl;
    std::cout << "  95th percentile: " << get_percentile(0.95) / 1000.0 << std::endl;
    std::cout << "  99th percentile: " << get_percentile(0.99) / 1000.0 << std::endl;
    std::cout << "  99.9th percentile: " << get_percentile(0.999) / 1000.0 << std::endl;
    
    if (stats.total_cycles > 1) {
        std::cout << "\nJitter (μs):" << std::endl;
        std::cout << "  Mean: " << mean_jitter / 1000.0 << std::endl;
        std::cout << "  Min: " << stats.min_jitter_ns / 1000.0 << std::endl;
        std::cout << "  Max: " << stats.max_jitter_ns / 1000.0 << std::endl;
    }
    
    std::cout << "\n========================================================" << std::endl;
}

// Write detailed log to CSV file
void write_log_file(const Statistics& stats, const std::string& filename) {
    std::ofstream logfile(filename);
    if (!logfile.is_open()) {
        std::cerr << "Failed to open log file: " << filename << std::endl;
        return;
    }
    
    logfile << "cycle,execution_time_ns,execution_time_us,jitter_ns,jitter_us,deadline_miss\n";
    
    for (size_t i = 0; i < stats.execution_times.size(); ++i) {
        uint64_t exec_time = stats.execution_times[i];
        uint64_t jitter = (i < stats.jitter_values.size()) ? stats.jitter_values[i] : 0;
        bool deadline_miss = (exec_time > DEADLINE_NS);
        
        logfile << i << ","
                << exec_time << ","
                << (exec_time / 1000.0) << ","
                << jitter << ","
                << (jitter / 1000.0) << ","
                << (deadline_miss ? 1 : 0) << "\n";
    }
    
    logfile.close();
    std::cout << "Detailed log written to: " << filename << std::endl;
}

// Main real-time loop
void run_realtime_loop(int duration_sec, const std::string& log_filename) {
    SpeedometerTask speedometer;
    Statistics stats;
    
    // Reserve space for statistics
    size_t expected_cycles = (duration_sec * 1'000'000'000ULL) / PERIOD_NS;
    stats.execution_times.reserve(expected_cycles);
    stats.jitter_values.reserve(expected_cycles);
    
    uint64_t start_time = get_time_ns();
    uint64_t next_wakeup = start_time + PERIOD_NS;
    uint64_t prev_wakeup = start_time;
    
    std::cout << "\nStarting real-time loop (duration: " << duration_sec << "s)..." << std::endl;
    std::cout << "Press Ctrl+C to stop early\n" << std::endl;
    
    while (g_running) {
        uint64_t current_time = get_time_ns();
        
        // Check if we should stop
        if (current_time - start_time >= static_cast<uint64_t>(duration_sec) * 1'000'000'000ULL) {
            break;
        }
        
        // Sleep until next period
        struct timespec ts = ns_to_timespec(next_wakeup);
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr);
        
        uint64_t wakeup_time = get_time_ns();
        
        // Calculate jitter (deviation from expected wakeup)
        int64_t jitter = static_cast<int64_t>(wakeup_time - next_wakeup);
        uint64_t abs_jitter = std::abs(jitter);
        
        // Execute speedometer task
        uint64_t exec_start = get_time_ns();
        speedometer.execute();
        uint64_t exec_end = get_time_ns();
        
        uint64_t execution_time = exec_end - exec_start;
        
        // Update statistics
        stats.total_cycles++;
        stats.sum_execution_ns += execution_time;
        stats.sum_squared_execution_ns += execution_time * execution_time;
        stats.execution_times.push_back(execution_time);
        
        if (execution_time < stats.min_execution_ns) {
            stats.min_execution_ns = execution_time;
        }
        if (execution_time > stats.max_execution_ns) {
            stats.max_execution_ns = execution_time;
        }
        
        // Track jitter
        if (stats.total_cycles > 1) {
            stats.sum_jitter_ns += abs_jitter;
            stats.jitter_values.push_back(abs_jitter);
            
            if (abs_jitter < stats.min_jitter_ns) {
                stats.min_jitter_ns = abs_jitter;
            }
            if (abs_jitter > stats.max_jitter_ns) {
                stats.max_jitter_ns = abs_jitter;
            }
        }
        
        // Check for deadline miss
        if (execution_time > DEADLINE_NS) {
            stats.deadline_misses++;
        }
        
        // Progress indicator (every 500 cycles = ~10 seconds at 50Hz)
        if (stats.total_cycles % 500 == 0) {
            double elapsed = (current_time - start_time) / 1e9;
            std::cout << "Progress: " << stats.total_cycles << " cycles, "
                      << std::setprecision(1) << elapsed << "s elapsed, "
                      << "Speed: " << std::setprecision(1) << speedometer.get_current_speed() << " km/h"
                      << std::endl;
        }
        
        // Calculate next wakeup time
        prev_wakeup = next_wakeup;
        next_wakeup += PERIOD_NS;
    }
    
    // Display and save results
    calculate_statistics(stats);
    write_log_file(stats, log_filename);
}

int main(int argc, char* argv[]) {
    std::cout << "========================================" << std::endl;
    std::cout << "Speedometer Real-Time Application" << std::endl;
    std::cout << "EB corbos Linux - ISO 26262 Evaluation" << std::endl;
    std::cout << "========================================" << std::endl;
    
    // Parse command line arguments
    int duration = TEST_DURATION_SEC;
    int cpu_core = -1; // -1 means no affinity setting
    std::string log_file = "speedometer_log.csv";
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-d" || arg == "--duration") {
            if (i + 1 < argc) {
                duration = std::atoi(argv[++i]);
            }
        } else if (arg == "-c" || arg == "--core") {
            if (i + 1 < argc) {
                cpu_core = std::atoi(argv[++i]);
            }
        } else if (arg == "-o" || arg == "--output") {
            if (i + 1 < argc) {
                log_file = argv[++i];
            }
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "\nUsage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  -d, --duration <seconds>  Test duration (default: 60)\n"
                      << "  -c, --core <cpu>          Pin to CPU core (optional)\n"
                      << "  -o, --output <file>       Output CSV log file (default: speedometer_log.csv)\n"
                      << "  -h, --help                Show this help message\n"
                      << std::endl;
            return 0;
        }
    }
    
    // Setup signal handlers
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    // Configure real-time environment
    std::cout << "\nConfiguring real-time environment..." << std::endl;
    
    if (!configure_realtime(RT_PRIORITY)) {
        std::cerr << "Warning: Running without real-time priority" << std::endl;
    }
    
    if (!lock_memory()) {
        std::cerr << "Warning: Running without locked memory" << std::endl;
    }
    
    if (cpu_core >= 0) {
        set_cpu_affinity(cpu_core);
    }
    
    // Pre-allocate memory
    std::vector<char> pre_alloc(PRE_ALLOC_SIZE);
    memset(pre_alloc.data(), 0, PRE_ALLOC_SIZE);
    
    // Run the real-time loop
    run_realtime_loop(duration, log_file);
    
    std::cout << "\nSpeedometer application completed successfully." << std::endl;
    
    return 0;
}
