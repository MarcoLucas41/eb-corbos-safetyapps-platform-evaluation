#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <signal.h>
#include <pthread.h>
#include <unistd.h>

/* Configuration */
#define MATRIX_SIZE 256
#define MAX_THREADS 16

/* Global control */
static volatile sig_atomic_t g_running = 1;

/* Thread argument structure */
typedef struct {
    int thread_id;
    uint64_t iteration_count;
} thread_arg_t;

void signal_handler(int signum) {
    (void)signum;
    printf("\nShutting down CPU workload...\n");
    g_running = 0;
}

/* CPU-intensive computation */
void* cpu_intensive_thread(void *arg) {
    thread_arg_t *targ = (thread_arg_t*)arg;
    int thread_id = targ->thread_id;
    
    /* Allocate matrices */
    double *matrix_a = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    double *matrix_b = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    double *matrix_c = malloc(MATRIX_SIZE * MATRIX_SIZE * sizeof(double));
    
    if (!matrix_a || !matrix_b || !matrix_c) {
        fprintf(stderr, "Thread %d: Memory allocation failed\n", thread_id);
        free(matrix_a);
        free(matrix_b);
        free(matrix_c);
        return NULL;
    }
    
    uint64_t iteration = 0;
    
    while (g_running) {
        /* Initialize matrices with random values */
        for (size_t i = 0; i < MATRIX_SIZE * MATRIX_SIZE; i++) {
            matrix_a[i] = (double)rand() / RAND_MAX * 2.0 - 1.0;
            matrix_b[i] = (double)rand() / RAND_MAX * 2.0 - 1.0;
        }
        
        /* Matrix multiplication */
        for (size_t i = 0; i < MATRIX_SIZE; i++) {
            for (size_t j = 0; j < MATRIX_SIZE; j++) {
                double sum = 0.0;
                for (size_t k = 0; k < MATRIX_SIZE; k++) {
                    sum += matrix_a[i * MATRIX_SIZE + k] * matrix_b[k * MATRIX_SIZE + j];
                }
                matrix_c[i * MATRIX_SIZE + j] = sum;
            }
        }
        
        /* FFT-like trigonometric operations */
        for (int i = 0; i < 1000; i++) {
            double x = (double)rand() / RAND_MAX * 2.0 - 1.0;
            volatile double result = sin(x) * cos(x) + tan(x / 2.0);
            result = sqrt(fabs(result)) + exp(result / 10.0);
        }
        
        iteration++;
        if (iteration % 10 == 0) {
            printf("CPU Thread %d: %lu iterations\n", thread_id, (unsigned long)iteration);
        }
    }
    
    targ->iteration_count = iteration;
    
    free(matrix_a);
    free(matrix_b);
    free(matrix_c);
    
    return NULL;
}

int main(int argc, char *argv[]) {
    printf("CPU-Intensive Workload\n");
    printf("======================\n");
    
    int num_threads = 1;
    
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--threads") == 0) && i + 1 < argc) {
            num_threads = atoi(argv[++i]);
            if (num_threads < 1) num_threads = 1;
            if (num_threads > MAX_THREADS) num_threads = MAX_THREADS;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("\nUsage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  -t, --threads <num>  Number of CPU threads (default: 1, max: %d)\n", MAX_THREADS);
            printf("  -h, --help           Show this help message\n");
            printf("\nGenerates CPU interference through matrix operations and trigonometric calculations.\n\n");
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    printf("Starting %d CPU-intensive thread(s)...\n", num_threads);
    
    /* Launch worker threads */
    pthread_t *threads = malloc(num_threads * sizeof(pthread_t));
    thread_arg_t *thread_args = malloc(num_threads * sizeof(thread_arg_t));
    
    if (!threads || !thread_args) {
        fprintf(stderr, "Failed to allocate thread structures\n");
        free(threads);
        free(thread_args);
        return 1;
    }
    
    for (int i = 0; i < num_threads; i++) {
        thread_args[i].thread_id = i;
        thread_args[i].iteration_count = 0;
        pthread_create(&threads[i], NULL, cpu_intensive_thread, &thread_args[i]);
    }
    
    /* Wait for all threads */
    for (int i = 0; i < num_threads; i++) {
        pthread_join(threads[i], NULL);
    }
    
    /* Print summary */
    printf("\nCPU workload summary:\n");
    for (int i = 0; i < num_threads; i++) {
        printf("  Thread %d: %lu iterations\n", i, (unsigned long)thread_args[i].iteration_count);
    }
    
    free(threads);
    free(thread_args);
    
    printf("CPU workload terminated.\n");
    return 0;
}
