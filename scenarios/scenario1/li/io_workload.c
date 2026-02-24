#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>

/* Configuration */
#define BUFFER_SIZE (1024 * 1024)  /* 1MB buffer */
#define DEFAULT_RATE_MBS 10
#define MIN_RATE 1
#define MAX_RATE 1000

/* Global control */
static volatile sig_atomic_t g_running = 1;

void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down I/O workload...\n");
    g_running = 0;
}

/* Get current time in nanoseconds */
static inline uint64_t get_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* I/O-intensive workload with continuous writes */
void io_intensive_workload(size_t write_rate_mbs) {
    uint8_t *buffer = malloc(BUFFER_SIZE);
    if (!buffer) {
        fprintf(stderr, "Failed to allocate buffer\n");
        return;
    }
    
    /* Fill buffer with random data */
    for (size_t i = 0; i < BUFFER_SIZE; i++) {
        buffer[i] = (uint8_t)rand();
    }
    
    const char *filename = "io_workload_temp.dat";
    uint64_t total_written = 0;
    uint64_t iteration = 0;
    
    printf("Target write rate: %zu MB/s\n", write_rate_mbs);
    printf("Writing to: %s\n", filename);
    
    while (g_running) {
        uint64_t start_time = get_time_ns();
        
        /* Open file for writing */
        int fd = open(filename, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) {
            fprintf(stderr, "Failed to open file: %s\n", filename);
            break;
        }
        
        /* Calculate how much to write this iteration */
        size_t bytes_to_write = write_rate_mbs * BUFFER_SIZE;
        size_t bytes_written_iteration = 0;
        
        while (bytes_written_iteration < bytes_to_write && g_running) {
            ssize_t written = write(fd, buffer, BUFFER_SIZE);
            if (written < 0) {
                fprintf(stderr, "Write error\n");
                break;
            }
            bytes_written_iteration += written;
            total_written += written;
            
            /* Force sync to disk periodically for maximum I/O pressure */
            if (bytes_written_iteration % (10 * BUFFER_SIZE) == 0) {
                fsync(fd);
            }
        }
        
        /* Final sync */
        fsync(fd);
        close(fd);
        
        iteration++;
        if (iteration % 10 == 0) {
            double mb_written = total_written / (1024.0 * 1024.0);
            printf("I/O workload: %lu iterations, %.2f MB written\n",
                   (unsigned long)iteration, mb_written);
        }
        
        /* Sleep to maintain target rate */
        uint64_t end_time = get_time_ns();
        uint64_t elapsed_ns = end_time - start_time;
        uint64_t target_ns = 1000000000ULL; /* 1 second per iteration */
        
        if (elapsed_ns < target_ns) {
            struct timespec sleep_time;
            uint64_t sleep_ns = target_ns - elapsed_ns;
            sleep_time.tv_sec = sleep_ns / 1000000000ULL;
            sleep_time.tv_nsec = sleep_ns % 1000000000ULL;
            nanosleep(&sleep_time, NULL);
        }
    }
    
    /* Cleanup */
    unlink(filename);
    free(buffer);
}

int main(int argc, char *argv[]) {
    printf("I/O-Intensive Workload\n");
    printf("======================\n");
    
    size_t write_rate_mbs = DEFAULT_RATE_MBS;
    
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--rate") == 0) && i + 1 < argc) {
            write_rate_mbs = (size_t)atoi(argv[++i]);
            if (write_rate_mbs < MIN_RATE) write_rate_mbs = MIN_RATE;
            if (write_rate_mbs > MAX_RATE) write_rate_mbs = MAX_RATE;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -r, --rate <MB/s>  Write rate in MB/s (default: %d, max: %d)\n",
                   DEFAULT_RATE_MBS, MAX_RATE);
            printf("  -h, --help         Show this help message\n");
            printf("\nGenerates I/O interference through continuous file writes with fsync.\n\n");
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    io_intensive_workload(write_rate_mbs);
    
    printf("I/O workload terminated.\n");
    return 0;
}
