#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <math.h>
#include <unistd.h>
#include <sched.h>
#include <errno.h>
#include <stdbool.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/syscall.h>

/* Configuration constants */
#define RT_PRIORITY 99               /* Real-time priority */
#define TEST_DURATION_SEC 60         /* Test duration in seconds */
#define MAX_SAMPLES 100000           /* Maximum samples to store */
#define MAX_PERIOD_NS 1000000000ULL  /* 1s max period fallback */

/* Runtime-configurable timing (defaults match previous constants) */
static uint64_t period_ns = 50000000ULL;    /* default 50ms (20Hz) */
static uint64_t deadline_ns = 10000000ULL;   /* default 10ms */

/* Global control flag */
static volatile int g_running = 1;

/* SIGINT handler for clean shutdown */
static void sigint_handler(int sig) {
    (void)sig;
    g_running = 0;
}

/* Statistics with static allocation */
typedef struct {
    uint64_t total_cycles;
    uint64_t deadline_misses;
    uint64_t min_execution_ns;
    uint64_t max_execution_ns;
    uint64_t sum_execution_ns;
    uint64_t execution_times[MAX_SAMPLES];
    uint64_t response_times[MAX_SAMPLES];
    uint64_t jitter_values[MAX_SAMPLES];
    uint64_t timestamps_ns[MAX_SAMPLES];  /* relative to test start */
    size_t sample_count;
    uint64_t min_jitter_ns;
    uint64_t max_jitter_ns;
    uint64_t sum_jitter_ns;
    size_t jitter_count;
    uint64_t min_response_ns;
    uint64_t max_response_ns;
    uint64_t sum_response_ns;
    uint64_t start_time_ns;               /* test start timestamp */
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

// // /* Configure real-time scheduling */
// static int configure_realtime(int priority) {
//     struct sched_param param;
//     pid_t tid;
//     int current_policy;
//     struct sched_param current_param;

//     /* Get current thread ID */
//     tid = syscall(SYS_gettid);
//     printf("Configuring RT scheduling for thread TID=%d\n", tid);

//     /* Check current scheduling policy */
//     current_policy = sched_getscheduler(0);
//     sched_getparam(0, &current_param);
//     printf("Current policy: %s (priority: %d)\n",
//            current_policy == SCHED_FIFO ? "SCHED_FIFO" :
//            current_policy == SCHED_RR ? "SCHED_RR" :
//            current_policy == SCHED_OTHER ? "SCHED_OTHER" : "UNKNOWN",
//            current_param.sched_priority);

//     /* Set RT scheduling */
//     param.sched_priority = priority;

//     if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
//         printf("Warning: Failed to set SCHED_FIFO: %s (errno=%d)\n", strerror(errno), errno);
//         printf("  Running without real-time scheduling.\n");
//         return 0;
//     }

//     /* Verify it was set */
//     current_policy = sched_getscheduler(0);
//     sched_getparam(0, &current_param);
//     printf("RT scheduling configured: %s priority %d\n",
//            current_policy == SCHED_FIFO ? "SCHED_FIFO" : "OTHER",
//            current_param.sched_priority);

//     return 1;
// }

// // /* Set CPU affinity */
// static int set_cpu_affinity(int cpu_core) {
//     cpu_set_t cpuset;
//     CPU_ZERO(&cpuset);
//     CPU_SET(cpu_core, &cpuset);

//     if (sched_setaffinity(0, sizeof(cpu_set_t), &cpuset) != 0) {
//         printf("Warning: Failed to set CPU affinity to core %d: %s\n",
//                 cpu_core, strerror(errno));
//         return 0;
//     }

//     printf("CPU affinity set to core %d\n", cpu_core);
//     return 1;
// }

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
static void stats_init(statistics_t *stats) {
    memset(stats, 0, sizeof(statistics_t));
    stats->min_execution_ns = UINT64_MAX;
    stats->min_jitter_ns = UINT64_MAX;
    stats->min_response_ns = UINT64_MAX;
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
    printf("\n========== SPEEDOMETER RT APPLICATION RESULTS ==========\n\n");

    printf("Test Configuration:\n");
    printf("  Period: %.3f ms\n", period_ns / 1e6);
    printf("  Deadline: %.3f ms\n", deadline_ns / 1e6);
    printf("  RT Priority: %d (SCHED_FIFO)\n\n", RT_PRIORITY);

    printf("Execution Statistics:\n");
    printf("  Total cycles: %lu\n", (unsigned long)stats->total_cycles);
    printf("  Deadline misses: %lu (%.3f%%)\n",
           (unsigned long)stats->deadline_misses,
           stats->total_cycles > 0 ? (stats->deadline_misses * 100.0 / stats->total_cycles) : 0.0);

    if (stats->sample_count > 0) {
        /* Create sorted copy for percentile calculation */
        static uint64_t sorted_exec[MAX_SAMPLES];
        memcpy(sorted_exec, stats->execution_times, stats->sample_count * sizeof(uint64_t));
        qsort(sorted_exec, stats->sample_count, sizeof(uint64_t), compare_uint64);

        double mean_exec = stats->sum_execution_ns / (double)stats->sample_count;

        printf("\nExecution Time (μs):\n");
        printf("  Mean: %.3f\n", mean_exec / 1e3);
        printf("  Min: %.3f\n", stats->min_execution_ns / 1e3);
        printf("  Max: %.3f\n", stats->max_execution_ns / 1e3);
        printf("  50th percentile: %.3f\n", get_percentile(sorted_exec, stats->sample_count, 0.50) / 1e3);
        printf("  95th percentile: %.3f\n", get_percentile(sorted_exec, stats->sample_count, 0.95) / 1e3);
        printf("  99th percentile: %.3f\n", get_percentile(sorted_exec, stats->sample_count, 0.99) / 1e3);
        printf("  99.9th percentile: %.3f\n", get_percentile(sorted_exec, stats->sample_count, 0.999) / 1e3);
    }

    if (stats->jitter_count > 0) {
        /* Create sorted copy for jitter percentiles */
        static uint64_t sorted_jitter[MAX_SAMPLES];
        memcpy(sorted_jitter, stats->jitter_values, stats->jitter_count * sizeof(uint64_t));
        qsort(sorted_jitter, stats->jitter_count, sizeof(uint64_t), compare_uint64);

        double mean_jitter = stats->sum_jitter_ns / (double)stats->jitter_count;

        printf("\nJitter (μs):\n");
        printf("  Mean: %.3f\n", mean_jitter / 1e3);
        printf("  Min: %.3f\n", stats->min_jitter_ns / 1e3);
        printf("  Max: %.3f\n", stats->max_jitter_ns / 1e3);
    }

    if (stats->sample_count > 0) {
        /* Create sorted copy for response time percentiles */
        static uint64_t sorted_resp[MAX_SAMPLES];
        memcpy(sorted_resp, stats->response_times, stats->sample_count * sizeof(uint64_t));
        qsort(sorted_resp, stats->sample_count, sizeof(uint64_t), compare_uint64);

        double mean_resp = stats->sum_response_ns / (double)stats->sample_count;

        printf("\nResponse Time (μs):\n");
        printf("  Mean: %.3f\n", mean_resp / 1e3);
        printf("  Min: %.3f\n", stats->min_response_ns / 1e3);
        printf("  Max: %.3f\n", stats->max_response_ns / 1e3);
        printf("  50th percentile: %.3f\n", get_percentile(sorted_resp, stats->sample_count, 0.50) / 1e3);
        printf("  95th percentile: %.3f\n", get_percentile(sorted_resp, stats->sample_count, 0.95) / 1e3);
        printf("  99th percentile: %.3f\n", get_percentile(sorted_resp, stats->sample_count, 0.99) / 1e3);
        printf("  99.9th percentile: %.3f\n", get_percentile(sorted_resp, stats->sample_count, 0.999) / 1e3);
    }

    printf("\n========================================================\n");
}

/* Write a single CSV line */
static void write_csv_line(FILE *fp, size_t i, const statistics_t *stats) {
    double ts = (i < stats->sample_count) ? stats->timestamps_ns[i] / 1e9 : 0.0;
    double exec = (i < stats->sample_count) ? stats->execution_times[i] / 1e3 : 0.0;
    double resp = (i < stats->sample_count) ? stats->response_times[i] / 1e3 : 0.0;
    double jit  = (i < stats->jitter_count) ? stats->jitter_values[i] / 1e3 : 0.0;
    int miss    = (i < stats->sample_count && stats->response_times[i] > deadline_ns) ? 1 : 0;

    fprintf(fp, "%zu,%.6f,%.3f,%.3f,%.3f,%d\n",
            i + 1, ts, exec, jit, resp, miss);
}

#define CSV_HEADER "cycle,timestamp_s,execution_time_us,jitter_us,response_time_us,deadline_miss\n"

/* Dump per-cycle CSV to stdout */
static void dump_csv_to_stdout(const statistics_t *stats) {
    size_t max_rows = stats->sample_count > stats->jitter_count ?
                      stats->sample_count : stats->jitter_count;

    printf("===CSV_START===\n");
    printf(CSV_HEADER);
    for (size_t i = 0; i < max_rows; i++) {
        write_csv_line(stdout, i, stats);
    }
    printf("===CSV_END===\n");
}

/* Main real-time loop */
static void run_realtime_loop(int duration_sec) {
    static statistics_t stats;
    static speedometer_task_t speedometer;

    stats_init(&stats);
    speedometer_init(&speedometer);

    printf("\nStarting real-time loop (duration: %ds)...\n", duration_sec);
    printf("Press Ctrl+C to stop early\n\n");

    uint64_t start_time = get_time_ns();
    uint64_t end_time = start_time + (uint64_t)duration_sec * 1000000000ULL;
    uint64_t next_wakeup = start_time + period_ns;
    stats.start_time_ns = start_time;

    while (g_running) {
        uint64_t current_time = get_time_ns();

        if (current_time >= end_time) {
            break;
        }

        /* Sleep until next period */
        uint64_t now = get_time_ns();
        if (now < next_wakeup) {
            struct timespec ts = ns_to_timespec(next_wakeup - now);
            nanosleep(&ts, NULL);
        }

        uint64_t wakeup_time = get_time_ns();

        /* Calculate jitter */
        int64_t jitter_signed = (int64_t)wakeup_time - (int64_t)next_wakeup;
        uint64_t abs_jitter = (uint64_t)(jitter_signed < 0 ? -jitter_signed : jitter_signed);

        /* Execute speedometer task */
        uint64_t exec_start = get_time_ns();
        speedometer_execute(&speedometer);
        uint64_t exec_end = get_time_ns();

        uint64_t execution_time = exec_end - exec_start;

        /* Update statistics */
        stats.total_cycles++;
        stats.sum_execution_ns += execution_time;

        if (stats.sample_count < MAX_SAMPLES) {
            stats.execution_times[stats.sample_count] = execution_time;
            stats.timestamps_ns[stats.sample_count] = wakeup_time - start_time;
        }

        if (execution_time < stats.min_execution_ns) {
            stats.min_execution_ns = execution_time;
        }
        if (execution_time > stats.max_execution_ns) {
            stats.max_execution_ns = execution_time;
        }

        /* Track jitter */
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

        /* Response time from scheduled activation */
        uint64_t response_time = abs_jitter + execution_time;

        stats.sum_response_ns += response_time;
        if (response_time < stats.min_response_ns) {
            stats.min_response_ns = response_time;
        }
        if (response_time > stats.max_response_ns) {
            stats.max_response_ns = response_time;
        }

        if (stats.sample_count < MAX_SAMPLES) {
            stats.response_times[stats.sample_count] = response_time;
            stats.sample_count++;
        }

        if (response_time > deadline_ns) {
            stats.deadline_misses++;
        }

        next_wakeup += period_ns;
    }

    /* Display and dump results */
    calculate_statistics(&stats);
    dump_csv_to_stdout(&stats);
}

int main(int argc, char *argv[]) {
    setbuf(stdout, NULL);

    printf("========================================\n");
    printf("Speedometer RT Application (LI mode)\n");
    printf("========================================\n");

    /* Parse command line arguments */
    int duration = TEST_DURATION_SEC;
    int deadline_arg = -1;
    int hz_arg = -1;
    // int cpu_core = -1;

    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0) && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-dm") == 0 || strcmp(argv[i], "--deadline-ms") == 0) && i + 1 < argc) {
            deadline_arg = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-hz") == 0 || strcmp(argv[i], "--speedometer-hz") == 0) && i + 1 < argc) {
            hz_arg = atoi(argv[++i]);
        // } else if ((strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--core") == 0) && i + 1 < argc) {
        //     cpu_core = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -d, --duration <seconds>  Test duration (default: %d)\n", TEST_DURATION_SEC);
            printf("  -c, --core <cpu>          Pin to CPU core (optional)\n");
            printf("  -h, --help                Show this help message\n");
            printf("\n");
            return 0;
        }
    }

    /* Configure real-time environment */
    printf("\nConfiguring real-time environment...\n");

    /* Lock all memory to prevent page faults in RT path */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        printf("Warning: mlockall failed: %s\n", strerror(errno));
    }

    /* Install signal handler for clean shutdown */
    signal(SIGINT, sigint_handler);
    signal(SIGTERM, sigint_handler);

    /* Apply command-line overrides for deadline and hz if provided */
    if (deadline_arg > 0) {
        deadline_ns = (uint64_t)deadline_arg * 1000000ULL;
    }
    if (hz_arg > 0) {
        uint64_t calc = (uint64_t)(1000000000ULL / (uint64_t)hz_arg);
        period_ns = (calc == 0) ? 1ULL : calc;
        if (period_ns > MAX_PERIOD_NS) period_ns = MAX_PERIOD_NS;
    }

    // configure_realtime(RT_PRIORITY);

    // if (cpu_core >= 0) {
    //     set_cpu_affinity(cpu_core);
    // }

    /* Run once and exit */
    run_realtime_loop(duration);

    printf("\nSpeedometer (LI) completed.\n");
    return 0;
}
