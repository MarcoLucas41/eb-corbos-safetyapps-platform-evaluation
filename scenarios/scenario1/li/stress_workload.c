#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#include <stdbool.h>
#include <sys/wait.h>
#include <signal.h>

/* Shared memory library */
#include "shmlib_proxyshm.h"
#include "shmlib_logger.h"
#include "shmlib_ringbuffer.h"
#include "shmlib_hi_app.h"
#include "common.h"

static volatile sig_atomic_t g_running = 1;

void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down stress workload...\n");
    g_running = 0;
}

int main(int argc, char *argv[]) {
    struct shmlib_shm_ptr proxyshm;
    struct shmlib_shm shm;
    struct shmlib_hi_app *speedometer = NULL;
    
    printf("========================================\n");
    printf("Stress-NG Workload (Synchronized)\n");
    printf("========================================\n");
    
    int cpu_workers = -1;     /* -1 = disabled, 0 = all CPUs */
    int vm_workers = 0;       /* Memory stressor workers */
    int io_workers = 0;       /* I/O stressor workers */
    int cache_workers = 0;    /* Cache thrashing stressor workers */
    int stream_workers = 0;   /* Memory bandwidth stressor workers */
    int duration = 60;        /* Default duration in seconds */
    int timeout = 0;          /* Timeout (0 = use duration) */
    char stress_method[64] = "all";  /* CPU stress method */
    
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--cpu") == 0) && i + 1 < argc) {
            cpu_workers = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--vm") == 0) && i + 1 < argc) {
            vm_workers = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--io") == 0) && i + 1 < argc) {
            io_workers = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-C") == 0 || strcmp(argv[i], "--cache") == 0) && i + 1 < argc) {
            cache_workers = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-S") == 0 || strcmp(argv[i], "--stream") == 0) && i + 1 < argc) {
            stream_workers = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0) && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--method") == 0) && i + 1 < argc) {
            strncpy(stress_method, argv[++i], sizeof(stress_method) - 1);
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -c, --cpu <workers>      CPU stressor workers (default: 0 = all CPUs)\n");
            printf("  -v, --vm <workers>       Memory stressor workers (default: 0 = disabled)\n");
            printf("  -i, --io <workers>       I/O stressor workers (default: 0 = disabled)\n");
            printf("  -C, --cache <workers>    Cache thrashing stressor workers (default: 0 = disabled)\n");
            printf("  -S, --stream <workers>   Memory bandwidth stressor workers (default: 0 = disabled)\n");
            printf("  -d, --duration <sec>     Test duration in seconds (default: 60)\n");
            printf("  -m, --method <method>    CPU stress method (default: all)\n");
            printf("                           Methods: all, ackermann, fibonacci, pi, matrixprod\n");
            printf("  -h, --help               Show this help message\n");
            printf("\nStress-NG wrapper synchronized with speedometer via shared memory.\n");
            printf("This will use stress-ng to generate system load.\n\n");
            printf("Examples:\n");
            printf("  %s -c 4 -d 30              # 4 CPU workers for 30s\n", argv[0]);
            printf("  %s -c 0 -v 2 -d 60         # All CPUs + 2 memory workers for 60s\n", argv[0]);
            printf("  %s -c 2 -m matrixprod      # 2 CPU workers using matrix multiply\n\n", argv[0]);
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    /* Initialize shared memory */
    printf("Initializing shared memory...\n");
    int ret = shmlib_shm_init(&shm);
    if (ret) {
        fprintf(stderr, "ERROR: Failed to initialize shared memory: %d\n", ret);
        return 1;
    }
    
    ret = shmlib_shm_get_proxyshm(&shm, &proxyshm);
    if (ret) {
        fprintf(stderr, "ERROR: Failed to get proxyshm: %d\n", ret);
        return 1;
    }
    
    /* Wait for speedometer to register */
    printf("Waiting for speedometer to register...\n");
    while (true) {
        struct timespec sleep_time = {.tv_sec = 0, .tv_nsec = 100000000}; /* 100ms */
        
        shmlib_proxyshm_lock(&proxyshm);
        if (!speedometer) {
            ret = shmlib_proxyshm_find_app(&proxyshm, SPEEDOMETER_UUID, &speedometer);
        }
        shmlib_proxyshm_unlock(&proxyshm);
        
        if (speedometer || ret != -ENOENT) {
            break;
        }
        nanosleep(&sleep_time, NULL);
    }
    
    if (ret) {
        fprintf(stderr, "ERROR: Failed to find speedometer: %d\n", ret);
        return 1;
    }
    
    printf("Found speedometer in shared memory!\n");
    
    /* Send START command */
    printf("Sending START command to speedometer...\n");
    ret = shmlib_ring_buffer_write(&speedometer->li_to_hi, CMD_START, strlen(CMD_START) + 1);
    if (ret < 0) {
        fprintf(stderr, "ERROR: Failed to send START command: %d\n", ret);
        return 1;
    }
    
    /* Check if any stressors are enabled */
    bool has_stressors = (cpu_workers >= 0 || vm_workers > 0 || io_workers > 0 ||
                          cache_workers > 0 || stream_workers > 0);
    
    if (has_stressors) {
        /* Build stress-ng command */
        char stress_cmd[1024];
        int offset = snprintf(stress_cmd, sizeof(stress_cmd), "stress-ng");
        
        if (cpu_workers >= 0) {
            offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset, 
                              " --cpu %d --cpu-method %s", cpu_workers, stress_method);
        }
        
        if (vm_workers > 0) {
            offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset,
                              " --vm %d --vm-bytes 128M", vm_workers);
        }
        
        if (io_workers > 0) {
            offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset,
                              " --io %d", io_workers);
        }
        
        if (cache_workers > 0) {
            offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset,
                              " --cache %d", cache_workers);
        }
        
        if (stream_workers > 0) {
            offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset,
                              " --stream %d", stream_workers);
        }
        
        offset += snprintf(stress_cmd + offset, sizeof(stress_cmd) - offset,
                          " --timeout %ds --metrics-brief", duration);
        
        printf("\nStarting stress-ng workload for %d seconds...\n", duration);
        printf("Command: %s\n\n", stress_cmd);
        
        /* Fork and execute stress-ng */
        pid_t pid = fork();
        if (pid == 0) {
            execl("/bin/sh", "sh", "-c", stress_cmd, NULL);
            perror("Failed to execute stress-ng");
            exit(1);
        } else if (pid < 0) {
            fprintf(stderr, "ERROR: Failed to fork: %s\n", strerror(errno));
            return 1;
        }
        
        int status;
        waitpid(pid, &status, 0);
        
        if (WIFEXITED(status)) {
            printf("\nStress-ng completed with exit code: %d\n", WEXITSTATUS(status));
        } else {
            printf("\nStress-ng terminated abnormally\n");
        }
    } else {
        /* Baseline mode: no stressors, just wait for the duration */
        printf("\nBaseline mode (no stressors) for %d seconds...\n", duration);
        struct timespec sleep_time = {.tv_sec = duration, .tv_nsec = 0};
        nanosleep(&sleep_time, NULL);
        printf("\nBaseline period completed.\n");
    }
    
    /* Send STOP command to speedometer */
    printf("Sending STOP command to speedometer...\n");
    ret = shmlib_ring_buffer_write(&speedometer->li_to_hi, CMD_STOP, strlen(CMD_STOP) + 1);
    if (ret < 0) {
        fprintf(stderr, "WARNING: Failed to send STOP command: %d\n", ret);
    }
    
    /* Wait for statistics from speedometer */
    printf("Waiting for statistics from speedometer...\n");
    struct speedometer_stats stats;
    int attempts = 0;
    bool got_stats = false;
    
    while (attempts < 100 && !got_stats) {  /* Try for 10 seconds */
        struct timespec sleep_time = {.tv_sec = 0, .tv_nsec = 100000000}; /* 100ms */
        
        ret = shmlib_ring_buffer_read(&speedometer->hi_to_li, (char*)&stats, sizeof(stats));
        if (ret > 0) {
            got_stats = true;
            break;
        } else if (ret < 0 && ret != -EAGAIN) {
            fprintf(stderr, "ERROR: Failed to read statistics: %d\n", ret);
            break;
        }
        
        nanosleep(&sleep_time, NULL);
        attempts++;
    }
    
    if (got_stats) {
        printf("\n========== RECEIVED SPEEDOMETER STATISTICS ==========\n");
        printf("Total cycles: %lu\n", (unsigned long)stats.total_cycles);
        printf("Deadline misses: %lu (%.2f%%)\n", 
               (unsigned long)stats.deadline_misses,
               stats.total_cycles > 0 ? (stats.deadline_misses * 100.0 / stats.total_cycles) : 0.0);
        printf("Execution time:\n");
        printf("  Mean: %.3f us\n", stats.mean_execution_us);
        printf("  Min: %.3f us\n", stats.min_execution_ns / 1e3);
        printf("  Max: %.3f us\n", stats.max_execution_ns / 1e3);
        printf("Jitter:\n");
        printf("  Mean: %.3f us\n", stats.mean_jitter_us);
        printf("  Max: %.3f us\n", stats.max_jitter_ns / 1e3);
        printf("Response time:\n");
        printf("  Mean: %.3f us\n", stats.mean_response_us);
        printf("  Min: %.3f us\n", stats.min_response_ns / 1e3);
        printf("  Max: %.3f us\n", stats.max_response_ns / 1e3);
        printf("====================================================\n");
    } else {
        printf("WARNING: Did not receive statistics from speedometer\n");
    }
    
    printf("\nStress workload terminated.\n");
    return 0;
}
