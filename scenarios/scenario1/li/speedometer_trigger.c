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
    printf("Signal Sender Workload (Synchronized)\n");
    printf("========================================\n");
    
    int duration = 60;        /* Default duration in seconds */
    int deadline_miss_threshold_ms = 10; /* Default deadline miss threshold */
    int speedometer_update_hz = 20; /* Default speedometer update frequency */
    
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0) && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-dm") == 0 || strcmp(argv[i], "--deadline-ms") == 0) && i + 1 < argc) {
            deadline_miss_threshold_ms = atoi(argv[++i]);
        } else if ((strcmp(argv[i], "-hz") == 0 || strcmp(argv[i], "--speedometer-hz") == 0) && i + 1 < argc) {
            speedometer_update_hz = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -d, --duration <seconds>  Duration to run the workload (default: 60s)\n");
            printf("  -dm, --deadline-ms <ms>   Set deadline miss threshold in ms (default: 10)\n");
            printf("  -hz, --speedometer-hz <hz> Set speedometer update frequency (default: 20)\n");
            printf("  -h, --help               Show this help message\n");
            printf("This program signals the speedometer to start/stop measurement.\n\n");
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
    
    /* Send START command with requested duration so both sides stop in sync */
    char start_cmd[BUFFER_SIZE];
    snprintf(start_cmd, sizeof(start_cmd), "%s %d;%d;%d", CMD_START_WITH_ARGS_PREFIX, duration, deadline_miss_threshold_ms, speedometer_update_hz);
    printf("Sending START command to speedometer: %s\n", start_cmd);
    ret = shmlib_ring_buffer_write(&speedometer->li_to_hi, start_cmd, strlen(start_cmd) + 1);
    if (ret < 0) {
        fprintf(stderr, "ERROR: Failed to send START command: %d\n", ret);
        return 1;
    }
    

    printf("\nRunning workload signal period for %d seconds...\n", duration);
    struct timespec sleep_time = {.tv_sec = duration, .tv_nsec = 0};
    nanosleep(&sleep_time, NULL);
    printf("\nWorkload signal period completed.\n");
    
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
    
    // if (got_stats) {
    //     printf("\n========== RECEIVED SPEEDOMETER STATISTICS ==========\n");
    //     printf("Total cycles: %lu\n", (unsigned long)stats.total_cycles);
    //     printf("Deadline misses: %lu (%.2f%%)\n", 
    //            (unsigned long)stats.deadline_misses,
    //            stats.total_cycles > 0 ? (stats.deadline_misses * 100.0 / stats.total_cycles) : 0.0);
    //     printf("Execution time:\n");
    //     printf("  Mean: %.3f us\n", stats.mean_execution_us);
    //     printf("  Min: %.3f us\n", stats.min_execution_ns / 1e3);
    //     printf("  Max: %.3f us\n", stats.max_execution_ns / 1e3);
    //     printf("Jitter:\n");
    //     printf("  Mean: %.3f us\n", stats.mean_jitter_us);
    //     printf("  Max: %.3f us\n", stats.max_jitter_ns / 1e3);
    //     printf("Response time:\n");
    //     printf("  Mean: %.3f us\n", stats.mean_response_us);
    //     printf("  Min: %.3f us\n", stats.min_response_ns / 1e3);
    //     printf("  Max: %.3f us\n", stats.max_response_ns / 1e3);
    //     printf("====================================================\n");
    // } else {
    //     printf("WARNING: Did not receive statistics from speedometer\n");
    // }
    
    printf("\nStress workload terminated.\n");
    return 0;
}
