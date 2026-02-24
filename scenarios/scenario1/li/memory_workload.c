#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <unistd.h>

/* Configuration */
#define DEFAULT_ALLOC_MB 512
#define MIN_ALLOC_MB 100
#define MAX_ALLOC_MB 8192

/* Global control */
static volatile sig_atomic_t g_running = 1;

void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down memory workload...\n");
    g_running = 0;
}

/* Memory-intensive workload with random access patterns */
void memory_intensive_workload(size_t alloc_size_mb) {
    size_t buffer_size = (alloc_size_mb * 1024 * 1024) / sizeof(uint64_t);
    
    printf("Allocating %zu MB (%zu elements)...\n", alloc_size_mb, buffer_size);
    
    uint64_t *buffer = malloc(buffer_size * sizeof(uint64_t));
    if (!buffer) {
        fprintf(stderr, "Memory allocation failed\n");
        return;
    }
    
    printf("Memory allocated successfully\n");
    
    /* Initialize with pattern */
    for (size_t i = 0; i < buffer_size; i++) {
        buffer[i] = i * 0xDEADBEEF;
    }
    
    uint64_t iteration = 0;
    
    while (g_running) {
        /* Random access pattern to maximize cache misses */
        for (int i = 0; i < 100000; i++) {
            size_t idx1 = ((size_t)rand() * rand()) % buffer_size;
            size_t idx2 = ((size_t)rand() * rand()) % buffer_size;
            
            /* Read-modify-write operations */
            uint64_t temp = buffer[idx1];
            buffer[idx1] = buffer[idx2];
            buffer[idx2] = temp ^ 0xCAFEBABE;
            
            /* Additional memory operations */
            volatile uint64_t sum = buffer[idx1] + buffer[idx2];
            buffer[idx1] = sum * 31;
        }
        
        /* Sequential access (memory bandwidth stress) */
        volatile uint64_t checksum = 0;
        for (size_t i = 0; i < buffer_size; i += 64) {
            checksum += buffer[i];
        }
        
        /* Write pattern (pollute caches) */
        for (size_t i = 0; i < buffer_size; i += 128) {
            buffer[i] = checksum + i;
        }
        
        iteration++;
        if (iteration % 10 == 0) {
            printf("Memory workload: %lu iterations\n", (unsigned long)iteration);
        }
    }
    
    free(buffer);
}

int main(int argc, char *argv[]) {
    printf("Memory-Intensive Workload\n");
    printf("=========================\n");
    
    size_t alloc_size_mb = DEFAULT_ALLOC_MB;
    
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--memory") == 0) && i + 1 < argc) {
            alloc_size_mb = (size_t)atoi(argv[++i]);
            if (alloc_size_mb < MIN_ALLOC_MB) alloc_size_mb = MIN_ALLOC_MB;
            if (alloc_size_mb > MAX_ALLOC_MB) alloc_size_mb = MAX_ALLOC_MB;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -m, --memory <MB>  Memory allocation size in MB (default: %d, max: %d)\n",
                   DEFAULT_ALLOC_MB, MAX_ALLOC_MB);
            printf("  -h, --help         Show this help message\n");
            printf("\nGenerates memory interference through random access patterns\n");
            printf("and sequential operations to maximize cache pressure.\n\n");
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    printf("Target allocation: %zu MB\n", alloc_size_mb);
    
    memory_intensive_workload(alloc_size_mb);
    
    printf("Memory workload terminated.\n");
    return 0;
}
