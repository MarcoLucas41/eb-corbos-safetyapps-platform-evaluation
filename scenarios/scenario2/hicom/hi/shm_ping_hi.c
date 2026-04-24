/**
 * shm_ping_hi — HI-only ping-pong client (Scenario 2, hicom variant)
 *
 * Runs as PID 1 on vm-hi.  Communicates with its child process (shm_echo_hi)
 * exclusively through the hicom shared memory region, which vm-li cannot access.
 *
 * Protocol:
 *   1. Init shmlib, overlay hicom_channel on hv_hicomshm, memset to 0.
 *   2. Spawn shm_echo_hi via clone/execve (CLONE_VFORK suspends us until exec).
 *   3. Print "HICOM_PING_READY" → orchestration script detects this.
 *   4. Wait for a single HICOM_TRIGGER_GO byte on the li_to_hi proxyshm channel
 *      (written by shm_trigger running on vm-li).
 *   5. Run N stop-and-wait pings through hicom, collect RTL stats, print results.
 *   6. Print "HICOM_PING_DONE run=<N>" → orchestration script extracts results.
 *   7. Repeat from step 4 (allows multiple runs per boot without restarting).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdbool.h>
#include <sched.h>
#include <math.h>
#include <sys/mman.h>

#include "shmlib_hi_app.h"
#include "shmlib_logger.h"
#include "common.h"

/* ── Configuration ──────────────────────────────────────────────────────── */

#define DEFAULT_N_MESSAGES 1000
#define MAX_SAMPLES        10000

/* ── Timing helpers ─────────────────────────────────────────────────────── */

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ── Statistics ─────────────────────────────────────────────────────────── */

static int compare_u64(const void *a, const void *b)
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

static void print_stats(const uint64_t *rtl_ns, size_t n_stored,
                        uint64_t n_sent, uint64_t n_lost)
{
    if (n_stored == 0) {
        printf("No RTL samples collected.\n");
        return;
    }

    static uint64_t sorted[MAX_SAMPLES];
    memcpy(sorted, rtl_ns, n_stored * sizeof(uint64_t));
    qsort(sorted, n_stored, sizeof(uint64_t), compare_u64);

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
                      ? ((double)n_lost / (double)n_sent) * 100.0 : 0.0;
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
}

/* ── Ring-buffer drain ──────────────────────────────────────────────────── */

static void drain_rb(struct shmlib_ring_buffer *rb)
{
    static char discard[SHMLIB_RING_BUFFER_SIZE];
    while (shmlib_ring_buffer_read(rb, discard, sizeof(discard)) > 0)
        ;
}

/* ── Echo child spawning ────────────────────────────────────────────────── */

static int start_echo_child(void *arg __attribute__((unused)))
{
    char *const argv[] = {NULL};
    char *const envp[] = {NULL};
    execve("/shm_echo_hi", argv, envp);
    /* execve should not return */
    printf("ERROR: execve /shm_echo_hi failed: %s\n", strerror(errno));
    exit(EXIT_FAILURE);
    return 0;
}

static void spawn_echo_child(void)
{
    enum { STACK_SIZE = 256 * 4096 };
    static char stack[STACK_SIZE] __attribute__((aligned(4096))) = {0};

    /* CLONE_VFORK suspends this process until the child calls execve,
     * guaranteeing the child image is running before we continue. */
    int ret = clone(start_echo_child, stack + STACK_SIZE,
                    CLONE_VFORK | CLONE_VM, NULL);
    if (ret == -1) {
        printf("ERROR: clone failed: %s\n", strerror(errno));
        exit(EXIT_FAILURE);
    }
}

/* ── Single ping experiment ─────────────────────────────────────────────── */

static void run_pings(struct hicom_channel *chan, int n_messages, int run_idx)
{
    static uint64_t rtl_ns[MAX_SAMPLES];
    size_t  n_stored = 0;
    uint64_t n_sent  = 0;
    uint64_t n_lost  = 0;

    static struct hicom_ping_message send_msg;
    static struct hicom_ping_message recv_msg;

    const struct timespec poll_sleep = {
        .tv_sec  = 0,
        .tv_nsec = (long)POLL_INTERVAL_US * 1000L
    };
    const uint64_t timeout_ns = (uint64_t)RTL_TIMEOUT_MS * 1000000ULL;

    /* Discard any stale echoes left by a previous run */
    drain_rb(&chan->echo_to_pinger);

    printf("HICOM_PING_START run=%d\n", run_idx);
    printf("Messages: %d  |  Payload: %zu B  |  Channel: hicom (HI-only)\n",
           n_messages, sizeof(struct hicom_ping_message));

    for (int seq = 0; seq < n_messages; seq++) {
        send_msg.seq = (uint64_t)seq;
        memset(send_msg.payload, 0xAB, sizeof(send_msg.payload));

        /* Stamp time as late as possible before the write */
        send_msg.send_time_ns = get_time_ns();

        int ret = shmlib_ring_buffer_write(&chan->pinger_to_echo,
                                           (char *)&send_msg, sizeof(send_msg));
        if (ret < 0) {
            printf("WARNING: write failed at seq %d: %d\n", seq, ret);
            n_lost++;
            continue;
        }
        n_sent++;

        /* Stop-and-wait: poll for echo */
        uint64_t deadline = get_time_ns() + timeout_ns;
        bool got_echo = false;

        while (get_time_ns() < deadline) {
            ssize_t n = shmlib_ring_buffer_read(&chan->echo_to_pinger,
                                                (char *)&recv_msg,
                                                sizeof(recv_msg));
            if (n == (ssize_t)sizeof(recv_msg)) {
                uint64_t recv_time = get_time_ns();

                if (recv_msg.seq != send_msg.seq) {
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
                printf("WARNING: echo read error at seq %d: %zd\n", seq, n);
            }

            nanosleep(&poll_sleep, NULL);
        }

        if (!got_echo) {
            printf("WARNING: timeout at seq %d\n", seq);
            n_lost++;
            drain_rb(&chan->echo_to_pinger);
        }

        if ((seq + 1) % 100 == 0) {
            printf("Progress: %d/%d (lost: %llu)\n",
                   seq + 1, n_messages, (unsigned long long)n_lost);
        }
    }

    print_stats(rtl_ns, n_stored, n_sent, n_lost);
    printf("HICOM_PING_DONE run=%d\n\n", run_idx);
}

/* ── Main ────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int n_messages = DEFAULT_N_MESSAGES;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc)
            n_messages = atoi(argv[++i]);
        else if (strcmp(argv[i], "-h") == 0) {
            printf("Usage: shm_ping_hi [-n <count>]\n");
            printf("  -n <count>  Pings per run (default: %d, max: %d)\n",
                   DEFAULT_N_MESSAGES, MAX_SAMPLES);
            return 0;
        }
    }
    if (n_messages <= 0 || n_messages > MAX_SAMPLES)
        n_messages = DEFAULT_N_MESSAGES;

    setbuf(stdout, NULL);

    printf("===================================================\n");
    printf("HI-only Ping-Pong via hicom\n");
    printf("Scenario 2 hicom variant — EB corbos Linux\n");
    printf("===================================================\n");
    printf("Pings per run:  %d\n", n_messages);
    printf("Message size:   %zu B\n", sizeof(struct hicom_ping_message));
    printf("Poll interval:  %d us\n", POLL_INTERVAL_US);
    printf("Echo timeout:   %d ms\n", RTL_TIMEOUT_MS);
    printf("\n");

    /* ── Initialise shmlib ──────────────────────────────────────────────── */

    struct shmlib_hi_app *app = NULL;
    struct shmlib_shm     shm = {0};
    struct shmlib_shm_ptr hicom_ptr = {0};

    /* init_proxyshm=true: we are the first HI app and must clear the table. */
    int ret = shmlib_hi_app_create_new(&shm, HICOM_PINGER_UUID, &app,
                                       &hicom_ptr, NULL, true);
    if (ret) {
        printf("ERROR: shmlib_hi_app_create_new failed: %d\n", ret);
        goto idle;
    }
    printf("Registered with UUID: %s\n", HICOM_PINGER_UUID);

    /* ── Overlay hicom memory with our channel struct ────────────────────── */

    if (hicom_ptr.size < sizeof(struct hicom_channel)) {
        printf("ERROR: hicom region (%zu B) too small for hicom_channel (%zu B)\n",
               hicom_ptr.size, sizeof(struct hicom_channel));
        goto idle;
    }

    struct hicom_channel *chan = (struct hicom_channel *)hicom_ptr.hicom;
    memset(chan, 0, sizeof(*chan));
    printf("hicom_channel initialised: %zu B at %p\n",
           sizeof(struct hicom_channel), (void *)chan);

    /* Optional: try to pre-fault our memory pages (ignore failure) */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("Note: mlockall failed (%s) — continuing\n", strerror(errno));

    /* ── Spawn shm_echo_hi child ─────────────────────────────────────────── */

    spawn_echo_child();
    printf("shm_echo_hi child spawned.\n");

    /* Give the child a moment to register in proxyshm and enter its loop */
    sleep(1);

    printf("\nHICOM_PING_READY\n\n");
    printf("Waiting for trigger from vm-li (HICOM_TRIGGER_GO on li_to_hi)...\n");

    /* ── Trigger-and-run loop ─────────────────────────────────────────────── */

    int run_index = 0;
    const struct timespec trigger_poll = {
        .tv_sec  = 0,
        .tv_nsec = 50L * 1000L * 1000L  /* 50 ms between trigger polls */
    };

    while (true) {
        char trigger = 0;
        ssize_t n = shmlib_ring_buffer_read(&app->li_to_hi, &trigger, 1);

        if (n == 1) {
            if ((uint8_t)trigger == HICOM_TRIGGER_GO) {
                run_index++;
                run_pings(chan, n_messages, run_index);
                printf("Waiting for next trigger...\n");
            } else {
                printf("INFO: unknown trigger byte 0x%02x — ignoring\n",
                       (unsigned)(uint8_t)trigger);
            }
        } else if (n != -EAGAIN) {
            printf("WARNING: li_to_hi read error: %zd\n", n);
        }

        nanosleep(&trigger_poll, NULL);
    }

idle:
    /* PID 1 must never return — the kernel would panic. */
    if (getpid() == 1) {
        printf("ERROR: entering idle loop (check above for failure reason)\n");
        while (1) sleep(3600);
    }
    return 1;
}
