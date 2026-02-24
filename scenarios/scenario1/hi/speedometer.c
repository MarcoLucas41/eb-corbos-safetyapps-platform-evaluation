#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <sched.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <errno.h>

/* Configuration constants */
#define PERIOD_NS 20000000ULL        /* 20ms period (50Hz) */
#define DEADLINE_NS 30000000ULL      /* 30ms deadline */
#define RT_PRIORITY 90               /* Real-time priority */
#define TEST_DURATION_SEC 60         /* Test duration in seconds */
#define PRE_ALLOC_SIZE (10 * 1024 * 1024) /* 10MB pre-allocated memory */
#define MAX_SAMPLES 100000           /* Maximum samples to store */

/* Global control flag */
static volatile sig_atomic_t g_running = 1;

/* Statistics structure */
typedef struct {
    uint64_t total_cycles;
    uint64_t deadline_misses;
    uint64_t min_execution_ns;
    uint64_t max_execution_ns;
    uint64_t sum_execution_ns;
    uint64_t *execution_times;
    uint64_t *jitter_values;
    size_t sample_count;
    uint64_t min_jitter_ns;
    uint64_t max_jitter_ns;
    uint64_t sum_jitter_ns;
    size_t jitter_count;
} statistics_t;

/* Speedometer task state */
typedef struct {
    double current_speed_kmh;
    double target_speed_kmh;
    uint64_t update_count;
} speedometer_task_t;

/* Get current time in nanoseconds */
static inline uint64_t get_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* Convert nanoseconds to timespec */
static inline struct timespec ns_to_timespec(uint64_t ns) {
    struct timespec ts;
    ts.tv_sec = ns / 1000000000ULL;
    ts.tv_nsec = ns % 1000000000ULL;
    return ts;
}

/* Signal handler for graceful shutdown */
static void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down gracefully...\n");
    g_running = 0;
}

/* Configure real-time scheduling */
static int configure_realtime(int priority) {
    struct sched_param param;
    param.sched_priority = priority;
    
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        fprintf(stderr, "Failed to set SCHED_FIFO: %s\n", strerror(errno));
        fprintf(stderr, "Note: Run with appropriate privileges (e.g., sudo)\n");
        return 0;
    }
    
    printf("Real-time scheduling configured: SCHED_FIFO, priority %d\n", priority);
    return 1;
}

/* Lock memory to prevent paging */
static int lock_memory(void) {
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        fprintf(stderr, "Failed to lock memory: %s\n", strerror(errno));
        return 0;
    }
    
    /* Pre-fault stack */
    unsigned char dummy[8192];
    memset(dummy, 0, sizeof(dummy));
    
    printf("Memory locked successfully\n");
    return 1;
}

/* Set CPU affinity */
static int set_cpu_affinity(int cpu_core) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(cpu_core, &cpuset);
    
    if (sched_setaffinity(0, sizeof(cpu_set_t), &cpuset) != 0) {
        fprintf(stderr, "Warning: Failed to set CPU affinity to core %d: %s\n",
                cpu_core, strerror(errno));
        return 0;
    }
    
    printf("CPU affinity set to core %d\n", cpu_core);
    return 1;
}

/* Initialize speedometer task */
static void speedometer_init(speedometer_task_t *task) {
    task->current_speed_kmh = 0.0;
    task->target_speed_kmh = 120.0;
    task->update_count = 0;
}

/* Execute speedometer task */
static void speedometer_execute(speedometer_task_t *task) {
    const double acceleration = 0.5; /* km/h per update */
    
    /* Simulate speed changes */
    if (fabs(task->current_speed_kmh - task->target_speed_kmh) > acceleration) {
        if (task->current_speed_kmh < task->target_speed_kmh) {
            task->current_speed_kmh += acceleration;
        } else {
            task->current_speed_kmh -= acceleration;
        }
    } else {
        task->current_speed_kmh = task->target_speed_kmh;
        /* Change target occasionally */
        if (task->update_count % 200 == 0) {
            task->target_speed_kmh = 60.0 + (rand() % 120);
        }
    }
    
    /* Simulate display buffer update */
    volatile double display_value = task->current_speed_kmh * 1.60934; /* Convert to mph */
    display_value = round(display_value * 10.0) / 10.0;
    
    /* Simulate checking warning thresholds */
    volatile int overspeed = (task->current_speed_kmh > 130.0);
    volatile int critical_speed = (task->current_speed_kmh > 200.0);
    (void)overspeed;
    (void)critical_speed;
    
    task->update_count++;
}

/* Initialize statistics */
static int stats_init(statistics_t *stats) {
    memset(stats, 0, sizeof(statistics_t));
    stats->min_execution_ns = UINT64_MAX;
    stats->min_jitter_ns = UINT64_MAX;
    
    stats->execution_times = malloc(MAX_SAMPLES * sizeof(uint64_t));
    stats->jitter_values = malloc(MAX_SAMPLES * sizeof(uint64_t));
    
    if (!stats->execution_times || !stats->jitter_values) {
        free(stats->execution_times);
        free(stats->jitter_values);
        return 0;
    }
    
    return 1;
}

/* Free statistics */
static void stats_free(statistics_t *stats) {
    free(stats->execution_times);
    free(stats->jitter_values);
}

/* Comparison function for qsort */
static int compare_uint64(const void *a, const void *b) {
    uint64_t arg1 = *(const uint64_t*)a;
    uint64_t arg2 = *(const uint64_t*)b;
    
    if (arg1 < arg2) return -1;
    if (arg1 > arg2) return 1;
    return 0;
}

/* Get percentile from sorted array */
static uint64_t get_percentile(const uint64_t *sorted, size_t count, double p) {
    if (count == 0) return 0;
    size_t idx = (size_t)(p * count);
    if (idx >= count) idx = count - 1;
    return sorted[idx];
}

/* Calculate and print statistics */
static void calculate_statistics(const statistics_t *stats) {
    if (stats->total_cycles == 0) {
        printf("No data collected\n");
        return;
    }
    
    double mean_execution = (double)stats->sum_execution_ns / stats->total_cycles;
    double mean_jitter = stats->jitter_count > 0 ?
                        (double)stats->sum_jitter_ns / stats->jitter_count : 0.0;
    
    /* Sort execution times for percentiles */
    uint64_t *sorted = malloc(stats->sample_count * sizeof(uint64_t));
    if (sorted) {
        memcpy(sorted, stats->execution_times, stats->sample_count * sizeof(uint64_t));
        qsort(sorted, stats->sample_count, sizeof(uint64_t), compare_uint64);
    }
    
    printf("\n========== SPEEDOMETER RT APPLICATION RESULTS ==========\n");
    printf("\nTest Configuration:\n");
    printf("  Period: %.3f ms\n", PERIOD_NS / 1000000.0);
    printf("  Deadline: %.3f ms\n", DEADLINE_NS / 1000000.0);
    printf("  RT Priority: %d (SCHED_FIFO)\n", RT_PRIORITY);
    
    printf("\nExecution Statistics:\n");
    printf("  Total cycles: %lu\n", (unsigned long)stats->total_cycles);
    printf("  Deadline misses: %lu (%.3f%%)\n",
           (unsigned long)stats->deadline_misses,
           100.0 * stats->deadline_misses / stats->total_cycles);
    
    printf("\nExecution Time (μs):\n");
    printf("  Mean: %.3f\n", mean_execution / 1000.0);
    printf("  Min: %.3f\n", stats->min_execution_ns / 1000.0);
    printf("  Max: %.3f\n", stats->max_execution_ns / 1000.0);
    
    if (sorted) {
        printf("  50th percentile: %.3f\n", get_percentile(sorted, stats->sample_count, 0.50) / 1000.0);
        printf("  95th percentile: %.3f\n", get_percentile(sorted, stats->sample_count, 0.95) / 1000.0);
        printf("  99th percentile: %.3f\n", get_percentile(sorted, stats->sample_count, 0.99) / 1000.0);
        printf("  99.9th percentile: %.3f\n", get_percentile(sorted, stats->sample_count, 0.999) / 1000.0);
        free(sorted);
    }
    
    if (stats->jitter_count > 0) {
        printf("\nJitter (μs):\n");
        printf("  Mean: %.3f\n", mean_jitter / 1000.0);
        printf("  Min: %.3f\n", stats->min_jitter_ns / 1000.0);
        printf("  Max: %.3f\n", stats->max_jitter_ns / 1000.0);
    }
    
    printf("\n========================================================\n");
}

/* Write log file */
static void write_log_file(const statistics_t *stats, const char *filename) {
    FILE *fp = fopen(filename, "w");
    if (!fp) {
        fprintf(stderr, "Failed to open log file: %s\n", filename);
        return;
    }
    
    fprintf(fp, "cycle,execution_time_ns,execution_time_us,jitter_ns,jitter_us,deadline_miss\n");
    
    for (size_t i = 0; i < stats->sample_count; i++) {
        uint64_t exec_time = stats->execution_times[i];
        uint64_t jitter = (i < stats->jitter_count) ? stats->jitter_values[i] : 0;
        int deadline_miss = (exec_time > DEADLINE_NS) ? 1 : 0;
        
        fprintf(fp, "%zu,%lu,%.3f,%lu,%.3f,%d\n",
                i,
                (unsigned long)exec_time,
                exec_time / 1000.0,
                (unsigned long)jitter,
                jitter / 1000.0,
                deadline_miss);
    }
    
    fclose(fp);
    printf("Detailed log written to: %s\n", filename);
}

/* Main real-time loop */
static void run_realtime_loop(int duration_sec, const char *log_filename) {
    speedometer_task_t speedometer;
    statistics_t stats;
    
    if (!stats_init(&stats)) {
        fprintf(stderr, "Failed to initialize statistics\n");
        return;
    }
    
    speedometer_init(&speedometer);
    
    uint64_t start_time = get_time_ns();
    uint64_t next_wakeup = start_time + PERIOD_NS;
    //uint64_t prev_wakeup = start_time;
    
    printf("\nStarting real-time loop (duration: %ds)...\n", duration_sec);
    printf("Press Ctrl+C to stop early\n\n");
    
    while (g_running) {
        uint64_t current_time = get_time_ns();
        
        /* Check if we should stop */
        if (current_time - start_time >= (uint64_t)duration_sec * 1000000000ULL) {
            break;
        }
        
        /* Sleep until next period */
        struct timespec ts = ns_to_timespec(next_wakeup);
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
        
        uint64_t wakeup_time = get_time_ns();
        
        /* Calculate jitter */
        int64_t jitter_signed = (int64_t)wakeup_time - (int64_t)next_wakeup;
        uint64_t abs_jitter = (uint64_t)llabs(jitter_signed);
        
        /* Execute speedometer task */
        uint64_t exec_start = get_time_ns();
        speedometer_execute(&speedometer);
        uint64_t exec_end = get_time_ns();
        
        uint64_t execution_time = exec_end - exec_start;
        
        /* Update statistics */
        stats.total_cycles++;
        stats.sum_execution_ns += execution_time;
        
        if (stats.sample_count < MAX_SAMPLES) {
            stats.execution_times[stats.sample_count++] = execution_time;
        }
        
        if (execution_time < stats.min_execution_ns) {
            stats.min_execution_ns = execution_time;
        }
        if (execution_time > stats.max_execution_ns) {
            stats.max_execution_ns = execution_time;
        }
        
        /* Track jitter */
        if (stats.total_cycles > 1) {
            stats.sum_jitter_ns += abs_jitter;
            
            if (stats.jitter_count < MAX_SAMPLES) {
                stats.jitter_values[stats.jitter_count++] = abs_jitter;
            }
            
            if (abs_jitter < stats.min_jitter_ns) {
                stats.min_jitter_ns = abs_jitter;
            }
            if (abs_jitter > stats.max_jitter_ns) {
                stats.max_jitter_ns = abs_jitter;
            }
        }
        
        /* Check for deadline miss */
        if (execution_time > DEADLINE_NS) {
            stats.deadline_misses++;
        }
        
        /* Progress indicator */
        if (stats.total_cycles % 500 == 0) {
            double elapsed = (current_time - start_time) / 1e9;
            printf("Progress: %lu cycles, %.1fs elapsed, Speed: %.1f km/h\n",
                   (unsigned long)stats.total_cycles, elapsed, speedometer.current_speed_kmh);
        }
        
        //prev_wakeup = next_wakeup;
        next_wakeup += PERIOD_NS;
    }
    
    /* Display and save results */
    calculate_statistics(&stats);
    write_log_file(&stats, log_filename);
    
    stats_free(&stats);
}

int main(int argc, char *argv[]) {
    printf("========================================\n");
    printf("Speedometer Real-Time Application\n");
    printf("EB corbos Linux - ISO 26262 Evaluation\n");
    printf("========================================\n");
    
    /* Parse command line arguments */
    int duration = TEST_DURATION_SEC;
    int cpu_core = -1;
    const char *log_file = "speedometer_log.csv";
    
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0) && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--core") == 0) && i + 1 < argc) {
            cpu_core = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-o") == 0 || strcmp(argv[i], "--output") == 0) && i + 1 < argc) {
            log_file = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -d, --duration <seconds>  Test duration (default: 60)\n");
            printf("  -c, --core <cpu>          Pin to CPU core (optional)\n");
            printf("  -o, --output <file>       Output CSV log file (default: speedometer_log.csv)\n");
            printf("  -h, --help                Show this help message\n");
            printf("\n");
            return 0;
        }
    }
    
    /* Setup signal handlers */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    /* Configure real-time environment */
    printf("\nConfiguring real-time environment...\n");
    
    configure_realtime(RT_PRIORITY);
    lock_memory();
    
    if (cpu_core >= 0) {
        set_cpu_affinity(cpu_core);
    }
    
    /* Pre-allocate memory */
    void *pre_alloc = malloc(PRE_ALLOC_SIZE);
    if (pre_alloc) {
        memset(pre_alloc, 0, PRE_ALLOC_SIZE);
    }
    
    /* Run the real-time loop */
    run_realtime_loop(duration, log_file);
    
    free(pre_alloc);
    
    printf("\nSpeedometer application completed successfully.\n");
    
    return 0;
}
