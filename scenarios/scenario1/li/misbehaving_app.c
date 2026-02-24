#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <pthread.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/resource.h>

/* Configuration */
#define NUM_CPU_THREADS 4
#define NUM_IO_THREADS 4
#define LARGE_ALLOC_SIZE (500 * 1024 * 1024)  /* 500MB per allocation */
#define NUM_ALLOCATIONS 4
#define MATRIX_SIZE 512
#define IO_BUFFER_SIZE (1024 * 1024)  /* 1MB */

/* Global control */
static volatile sig_atomic_t g_running = 1;

void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down misbehaving app...\n");
    g_running = 0;
}

/* Get current time in nanoseconds */
static inline uint64_t get_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* Aggressive CPU consumption with matrix operations */
void* cpu_attack_thread(void *arg) {
    (void)arg;
    double *matrix_a = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    double *matrix_b = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    double *result = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    
    if (!matrix_a || !matrix_b || !result) {
        fprintf(stderr, "CPU thread: Failed to allocate matrices\n");
        free(matrix_a);
        free(matrix_b);
        free(result);
        return NULL;
    }
    
    /* Initialize matrices */
    for (int i = 0; i < MATRIX_SIZE * MATRIX_SIZE; i++) {
        matrix_a[i] = (double)rand() / RAND_MAX;
        matrix_b[i] = (double)rand() / RAND_MAX;
    }
    
    while (g_running) {
        /* Matrix multiplication */
        for (int i = 0; i < MATRIX_SIZE; i++) {
            for (int j = 0; j < MATRIX_SIZE; j++) {
                double sum = 0.0;
                for (int k = 0; k < MATRIX_SIZE; k++) {
                    sum += matrix_a[i * MATRIX_SIZE + k] * matrix_b[k * MATRIX_SIZE + j];
                }
                result[i * MATRIX_SIZE + j] = sum;
            }
        }
    }
    
    free(matrix_a);
    free(matrix_b);
    free(result);
    return NULL;
}

/* Aggressive memory allocation and access */
void* memory_attack_thread(void *arg) {
    (void)arg;
    void *allocations[NUM_ALLOCATIONS] = {NULL};
    
    while (g_running) {
        /* Allocate large chunks */
        for (int i = 0; i < NUM_ALLOCATIONS && g_running; i++) {
            allocations[i] = malloc(LARGE_ALLOC_SIZE);
            if (allocations[i]) {
                /* Touch every page to ensure physical allocation */
                for (size_t j = 0; j < LARGE_ALLOC_SIZE; j += 4096) {
                    ((uint8_t*)allocations[i])[j] = (uint8_t)j;
                }
            }
        }
        
        /* Random access pattern */
        for (int iter = 0; iter < 100 && g_running; iter++) {
            for (int i = 0; i < NUM_ALLOCATIONS; i++) {
                if (allocations[i]) {
                    size_t offset = (rand() % (LARGE_ALLOC_SIZE / sizeof(uint64_t))) * sizeof(uint64_t);
                    volatile uint64_t *ptr = (uint64_t*)((uint8_t*)allocations[i] + offset);
                    *ptr = get_time_ns();
                }
            }
        }
        
        /* Free and repeat */
        for (int i = 0; i < NUM_ALLOCATIONS; i++) {
            free(allocations[i]);
            allocations[i] = NULL;
        }
        
        /* Small delay */
        struct timespec sleep_time = {.tv_sec = 0, .tv_nsec = 100000000};  /* 100ms */
        nanosleep(&sleep_time, NULL);
    }
    
    /* Cleanup */
    for (int i = 0; i < NUM_ALLOCATIONS; i++) {
        free(allocations[i]);
    }
    
    return NULL;
}

/* Aggressive I/O operations */
void* io_attack_thread(void *arg) {
    int thread_id = *(int*)arg;
    char filename[64];
    snprintf(filename, sizeof(filename), "misbehave_io_%d.dat", thread_id);
    
    uint8_t *buffer = malloc(IO_BUFFER_SIZE);
    if (!buffer) {
        fprintf(stderr, "I/O thread %d: Failed to allocate buffer\n", thread_id);
        return NULL;
    }
    
    /* Fill buffer */
    for (size_t i = 0; i < IO_BUFFER_SIZE; i++) {
        buffer[i] = (uint8_t)rand();
    }
    
    while (g_running) {
        int fd = open(filename, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) {
            fprintf(stderr, "I/O thread %d: Failed to open file\n", thread_id);
            break;
        }
        
        /* Write aggressively */
        for (int i = 0; i < 100 && g_running; i++) {
            ssize_t written = write(fd, buffer, IO_BUFFER_SIZE);
            if (written < 0) {
                break;
            }
            fsync(fd);
        }
        
        close(fd);
    }
    
    unlink(filename);
    free(buffer);
    return NULL;
}

/* Try to elevate priority (will fail without privileges) */
void attempt_priority_escalation(void) {
    struct sched_param param;
    param.sched_priority = 99;
    
    printf("Attempting to set SCHED_FIFO priority 99...\n");
    if (sched_setscheduler(0, SCHED_FIFO, &param) == 0) {
        printf("WARNING: Successfully set high priority! (should be prevented)\n");
    } else {
        printf("Priority escalation blocked (expected behavior)\n");
    }
}

/* Try to lock memory (should be limited by resource limits) */
void attempt_memory_locking(void) {
    printf("Attempting to lock all memory...\n");
    if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0) {
        printf("WARNING: Successfully locked memory! (should be limited)\n");
    } else {
        printf("Memory locking prevented (expected behavior)\n");
    }
}

int main(int argc, char *argv[]) {
    (void)argc;
    (void)argv;
    
    printf("Misbehaving Application (Multi-Dimensional Attack)\n");
    printf("===================================================\n");
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    /* Attempt privilege escalation (should fail) */
    attempt_priority_escalation();
    attempt_memory_locking();
    
    /* Create aggressive threads */
    pthread_t cpu_threads[NUM_CPU_THREADS];
    pthread_t mem_thread;
    pthread_t io_threads[NUM_IO_THREADS];
    int io_thread_ids[NUM_IO_THREADS];
    
    printf("\nLaunching attack threads:\n");
    printf("  - %d CPU attack threads\n", NUM_CPU_THREADS);
    printf("  - 1 memory attack thread\n");
    printf("  - %d I/O attack threads\n", NUM_IO_THREADS);
    
    /* Launch CPU threads */
    for (int i = 0; i < NUM_CPU_THREADS; i++) {
        pthread_create(&cpu_threads[i], NULL, cpu_attack_thread, NULL);
    }
    
    /* Launch memory thread */
    pthread_create(&mem_thread, NULL, memory_attack_thread, NULL);
    
    /* Launch I/O threads */
    for (int i = 0; i < NUM_IO_THREADS; i++) {
        io_thread_ids[i] = i;
        pthread_create(&io_threads[i], NULL, io_attack_thread, &io_thread_ids[i]);
    }
    
    printf("All attack threads running. Press Ctrl+C to stop.\n\n");
    
    /* Monitor and report */
    while (g_running) {
        sleep(5);
        if (g_running) {
            printf("Misbehaving app: Active resource attack ongoing...\n");
        }
    }
    
    /* Wait for threads to finish */
    printf("Stopping attack threads...\n");
    
    for (int i = 0; i < NUM_CPU_THREADS; i++) {
        pthread_join(cpu_threads[i], NULL);
    }
    pthread_join(mem_thread, NULL);
    for (int i = 0; i < NUM_IO_THREADS; i++) {
        pthread_join(io_threads[i], NULL);
    }
    
    printf("Misbehaving app terminated.\n");
    return 0;
}
