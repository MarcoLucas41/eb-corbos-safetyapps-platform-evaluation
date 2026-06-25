#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdbool.h>
#include <math.h>

#include "shmlib_proxyshm.h"
#include "shmlib_ringbuffer.h"
#include "shmlib_hi_app.h"
#include "shmlib_shm.h"
#include "shmlib_logger.h"
#include "common.h"

/* Maximum RTL samples to store (caps memory at ~80 KB for default 10000). */
#define MAX_SAMPLES 10000

/* ── Timing helpers ────────────────────────────────────────────────────── */

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ── Statistics ────────────────────────────────────────────────────────── */

static int compare_uint64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a;
    uint64_t y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

static uint64_t percentile(const uint64_t *sorted, size_t n, double p)
{
    if (n == 0) return 0;
    size_t idx = (size_t)(p * (double)n);
    if (idx >= n) idx = n - 1;
    return sorted[idx];
}

static void print_and_save_stats(const uint64_t *rtl_ns, size_t n_stored,
                                  uint64_t n_sent, uint64_t n_lost)
{
    if (n_stored == 0) {
        printf("No RTL samples collected.\n");
        return;
    }

    /* Sort a copy for percentile computation. */
    static uint64_t sorted[MAX_SAMPLES];
    memcpy(sorted, rtl_ns, n_stored * sizeof(uint64_t));
    qsort(sorted, n_stored, sizeof(uint64_t), compare_uint64);

    /* Mean and standard deviation. */
    double sum = 0.0;
    for (size_t i = 0; i < n_stored; i++)
        sum += (double)rtl_ns[i];
    double mean = sum / (double)n_stored;

    double sq_sum = 0.0;
    for (size_t i = 0; i < n_stored; i++) {
        double diff = (double)rtl_ns[i] - mean;
        sq_sum += diff * diff;
    }
    double stddev = sqrt(sq_sum / (double)n_stored);

    double loss_pct = (n_sent > 0)
                      ? ((double)n_lost / (double)n_sent) * 100.0
                      : 0.0;
    double throughput = (mean > 0.0) ? (1e9 / mean) : 0.0;

    printf("\n========== SHM PING-PONG RESULTS ==========\n");
    printf("Messages sent:    %llu\n",  (unsigned long long)n_sent);
    printf("Messages lost:    %llu (%.3f%%)\n",
           (unsigned long long)n_lost, loss_pct);
    printf("RTL (us):\n");
    printf("  Mean:   %8.3f    Std dev: %.3f\n",
           mean / 1e3, stddev / 1e3);
    printf("  Min:    %8.3f    Max:     %.3f\n",
           sorted[0] / 1e3, sorted[n_stored - 1] / 1e3);
    printf("  P50:    %8.3f    P95:     %.3f\n",
           percentile(sorted, n_stored, 0.50) / 1e3,
           percentile(sorted, n_stored, 0.95) / 1e3);
    printf("  P99:    %8.3f    P99.9:   %.3f\n",
           percentile(sorted, n_stored, 0.99) / 1e3,
           percentile(sorted, n_stored, 0.999) / 1e3);
    printf("Throughput: %.0f msg/s\n", throughput);
    printf("============================================\n");

    printf("===CSV_START===\n");
    printf("Seq,RTT_ns\n");
    for (size_t i = 0; i < n_stored; i++)  {      
        printf("%zu,%llu\n", i + 1, (unsigned long long)rtl_ns[i]);
    }
    printf("===CSV_END===\n");
}

/* ── Ring-buffer drain ─────────────────────────────────────────────────── */

/* Discard any stale data left in a ring buffer from a previous run.
 * This prevents spurious echoes from being mistaken for responses. */
static void drain_ring_buffer(struct shmlib_ring_buffer *buf)
{
    static char discard[SHMLIB_RING_BUFFER_SIZE];
    while (shmlib_ring_buffer_read(buf, discard, sizeof(discard)) > 0)
        ;
}

/* ── Main ──────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int n_messages = 1000;
    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-n") == 0) && i + 1 < argc) {
            n_messages = atoi(argv[++i]);
        } 
        else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("  -n <count>  Ping messages to send (default: 1000)\n");
            printf("  -h          Show this help\n");
            printf("\nMessage size: %zu B  (PING_PAYLOAD_BYTES=%d)\n", sizeof(struct ping_message), PING_PAYLOAD_BYTES);
            return 0;
        }
    }

    if (n_messages <= 0 || n_messages > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be between 1 and %d\n", MAX_SAMPLES);
        return 1;
    }
    

    printf("========================================\n");
    printf("SHM Ping-Pong Client\n");
    printf("Scenario 2 - EB corbos Linux Evaluation\n");
    printf("========================================\n");
    printf("Messages:       %d\n", n_messages);
    printf("Message size:   %zu B\n", sizeof(struct ping_message));
    printf("Poll interval:  %d us\n", POLL_INTERVAL_US);
    printf("Echo timeout:   %d ms\n", RTL_TIMEOUT_MS);
    printf("\n");

    /* ── Initialise shared memory (li side) ───────────────────────────── */

    struct shmlib_shm shm;
    struct shmlib_shm_ptr proxyshm;
    struct shmlib_hi_app *echo_app = NULL;

    int ret = shmlib_shm_init(&shm);
    if (ret) {
        fprintf(stderr, "ERROR: shmlib_shm_init failed: %d\n", ret);
        return 1;
    }

    ret = shmlib_shm_get_proxyshm(&shm, &proxyshm);
    if (ret) {
        fprintf(stderr, "ERROR: shmlib_shm_get_proxyshm failed: %d\n", ret);
        return 1;
    }

    /* ── Wait for shm_echo to register ───────────────────────────────── */

    printf("Waiting for shm_echo to register (UUID: %s)...\n", SHM_ECHO_UUID);

    const struct timespec wait_sleep = { .tv_sec = 0, .tv_nsec = 100000000L }; /* 100 ms */
    while (echo_app == NULL) {
        shmlib_proxyshm_lock(&proxyshm);
        ret = shmlib_proxyshm_find_app(&proxyshm, SHM_ECHO_UUID, &echo_app);
        shmlib_proxyshm_unlock(&proxyshm);

        if (ret != 0 && ret != -ENOENT) {
            fprintf(stderr, "ERROR: proxyshm lookup failed: %d\n", ret);
            return 1;
        }
        if (echo_app == NULL)
            nanosleep(&wait_sleep, NULL);
    }
    printf("Found shm_echo!\n\n");

    /* ── Drain stale data from previous runs ──────────────────────────── */

    drain_ring_buffer(&echo_app->li_to_hi);
    drain_ring_buffer(&echo_app->hi_to_li);

    /* ── Ping-pong measurement loop ───────────────────────────────────── */

    static uint64_t rtl_ns[MAX_SAMPLES];
    size_t n_stored  = 0;
    uint64_t n_sent  = 0;
    uint64_t n_lost  = 0;

    static struct ping_message send_msg;
    static struct ping_message recv_msg;

    const struct timespec poll_sleep = {
        .tv_sec  = 0,
        .tv_nsec = (long)POLL_INTERVAL_US * 1000L
    };
    const uint64_t timeout_ns = (uint64_t)RTL_TIMEOUT_MS * 1000000ULL;

    for (int seq = 0; seq < n_messages; seq++) {
        /* Prepare message */
        send_msg.seq          = (uint64_t)seq;
        memset(send_msg.payload, 0xAB, sizeof(send_msg.payload));

        /* Stamp send time as late as possible before writing */
        send_msg.send_time_ns = get_time_ns();

        ret = shmlib_ring_buffer_write(&echo_app->li_to_hi,
                                       (char *)&send_msg, sizeof(send_msg));
        if (ret < 0) {
            printf("WARNING: write failed at seq %d: %d\n", seq, ret);
            n_lost++;
            continue;
        }
        n_sent++;

        /* Wait for echo (stop-and-wait: at most one outstanding ping) */
        uint64_t deadline = get_time_ns() + timeout_ns;
        bool got_echo = false;

        while (get_time_ns() < deadline) {
            ssize_t n = shmlib_ring_buffer_read(&echo_app->hi_to_li,
                                                (char *)&recv_msg,
                                                sizeof(recv_msg));
            if (n == (ssize_t)sizeof(recv_msg)) {
                uint64_t recv_time = get_time_ns();

                if (recv_msg.seq != send_msg.seq) {
                    /* Sequence mismatch: stale echo from a previous run or
                     * a bug.  Discard and keep waiting. */
                    printf("WARNING: seq mismatch (expected %llu, got %llu)\n",
                           (unsigned long long)send_msg.seq,
                           (unsigned long long)recv_msg.seq);
                    continue;
                }

                uint64_t rtl = recv_time - send_msg.send_time_ns;
                if (n_stored < MAX_SAMPLES)
                    rtl_ns[n_stored++] = rtl;

                got_echo = true;
                break;

            } else if (n != -EAGAIN) {
                /* Unexpected error — log and keep polling */
                printf("WARNING: echo read error at seq %d: %zd\n", seq, n);
            }

            nanosleep(&poll_sleep, NULL);
        }

        if (!got_echo) {
            // printf("WARNING: timeout waiting for echo at seq %d\n", seq);
            n_lost++;
            /* Drain any stale response that arrives late */
            drain_ring_buffer(&echo_app->hi_to_li);
        }

    }

    /* ── Report results ───────────────────────────────────────────────── */

    print_and_save_stats(rtl_ns, n_stored, n_sent, n_lost);

    return (n_lost == 0) ? 0 : 1;
}
