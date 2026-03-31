#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdbool.h>
#include <signal.h>
#include <math.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "common.h"

/* Maximum packets stored in memory per run.  At 64 B/msg this is ~640 KB. */
#define MAX_SAMPLES 10000

/* ── Timing helper ──────────────────────────────────────────────────────── */

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ── Statistics ─────────────────────────────────────────────────────────── */

static int compare_uint64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a;
    uint64_t y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

static uint64_t pct(const uint64_t *sorted, size_t n, double p)
{
    if (n == 0) return 0;
    size_t idx = (size_t)(p * (double)n);
    if (idx >= n) idx = n - 1;
    return sorted[idx];
}

static void print_metric(const char *label, const uint64_t *samples, size_t n)
{
    if (n == 0) {
        printf("%s: no samples\n", label);
        return;
    }

    static uint64_t sorted[MAX_SAMPLES];
    memcpy(sorted, samples, n * sizeof(uint64_t));
    qsort(sorted, n, sizeof(uint64_t), compare_uint64);

    double sum = 0.0;
    for (size_t i = 0; i < n; i++) sum += (double)samples[i];
    double mean = sum / (double)n;

    double sq = 0.0;
    for (size_t i = 0; i < n; i++) {
        double d = (double)samples[i] - mean;
        sq += d * d;
    }
    double stddev = sqrt(sq / (double)n);

    printf("%s (us):\n", label);
    printf("  Mean: %8.3f    Std dev: %.3f\n", mean / 1e3, stddev / 1e3);
    printf("  Min:  %8.3f    Max:     %.3f\n",
           sorted[0] / 1e3, sorted[n - 1] / 1e3);
    printf("  P50:  %8.3f    P95:     %.3f\n",
           pct(sorted, n, 0.50) / 1e3, pct(sorted, n, 0.95) / 1e3);
    printf("  P99:  %8.3f    P99.9:   %.3f\n",
           pct(sorted, n, 0.99) / 1e3, pct(sorted, n, 0.999) / 1e3);
}

/* ── CSV writer ─────────────────────────────────────────────────────────── */

static void write_csv(const char      *path,
                      const uint64_t  *seqs,
                      const uint64_t  *send_ns,
                      const uint64_t  *recv_ns,
                      const uint64_t  *owl_ns,
                      const uint64_t  *iaj_ns,  /* n-1 entries */
                      size_t           n)
{
    FILE *fp = fopen(path, "w");
    if (!fp) {
        printf("WARNING: could not open CSV '%s': %s\n", path, strerror(errno));
        return;
    }
    fprintf(fp, "seq,send_time_ns,recv_time_ns,owlatency_us,iat_us\n");
    for (size_t i = 0; i < n; i++) {
        fprintf(fp, "%llu,%llu,%llu,%.3f,%.3f\n",
                (unsigned long long)seqs[i],
                (unsigned long long)send_ns[i],
                (unsigned long long)recv_ns[i],
                (double)owl_ns[i] / 1e3,
                i > 0 ? (double)iaj_ns[i - 1] / 1e3 : 0.0);
    }
    fclose(fp);
    printf("CSV written to: %s\n", path);
}

/* ── Main ───────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int         n_target  = 5000;
    int         flood_mbps = 0;
    const char *csv_path   = "net_receiver_log.csv";

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n_target = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-B") == 0 && i + 1 < argc) {
            flood_mbps = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            csv_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("  -n <count>   Packets to collect (default: 5000, max: %d)\n",
                   MAX_SAMPLES);
            printf("  -B <Mbps>    Background flood rate in Mbps (default: 0 = off)\n");
            printf("  -o <file>    Output CSV (default: net_receiver_log.csv)\n");
            printf("  -h           Show this help\n");
            printf("\nMessage size: %zu B  (NET_PAYLOAD_BYTES=%d)\n",
                   sizeof(struct net_message), NET_PAYLOAD_BYTES);
            return 0;
        }
    }

    if (n_target <= 0 || n_target > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be between 1 and %d\n", MAX_SAMPLES);
        return 1;
    }

    printf("========================================\n");
    printf("net_receiver — Scenario 3\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Collecting:   %d packets\n",   n_target);
    printf("Message size: %zu B\n",        sizeof(struct net_message));
    printf("Flood rate:   %d Mbps\n",      flood_mbps);
    printf("Recv timeout: %d ms\n\n",      RECV_TIMEOUT_MS);

    /* ── Create receive socket ──────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "ERROR: socket() failed: %s\n", strerror(errno));
        return 1;
    }

    /* Larger receive buffer absorbs short bursts without dropping. */
    int rcvbuf = 1 << 20; /* 1 MB */
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in baddr = {0};
    baddr.sin_family      = AF_INET;
    baddr.sin_port        = htons(NET_SENDER_PORT);
    baddr.sin_addr.s_addr = INADDR_ANY;
    if (bind(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) {
        fprintf(stderr, "ERROR: bind(port %d) failed: %s\n",
                NET_SENDER_PORT, strerror(errno));
        return 1;
    }

    /* SO_RCVTIMEO lets us detect a stalled sender without blocking forever. */
    struct timeval tv = {
        .tv_sec  = RECV_TIMEOUT_MS / 1000,
        .tv_usec = (RECV_TIMEOUT_MS % 1000) * 1000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    /* ── Start background flood (optional) ──────────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0) {
        char bw_str[32];
        snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);

        printf("Starting background flood → %s:%d at %d Mbps\n\n",
               NET_HI_IP, NET_FLOOD_PORT, flood_mbps);

        flood_pid = fork();
        if (flood_pid == 0) {
            /* Child: exec net_flood (expected in PATH after install) */
            execlp("net_flood", "net_flood",
                   "-d", NET_HI_IP,
                   "-p", "5001",
                   "-b", bw_str,
                   NULL);
            perror("execlp net_flood");
            exit(1);
        } else if (flood_pid < 0) {
            fprintf(stderr, "WARNING: fork for net_flood failed: %s — "
                    "continuing without flood\n", strerror(errno));
            flood_pid = -1;
        } else {
            /* Allow flood workers to ramp up before measurements start. */
            printf("Waiting 2 s for flood to ramp up...\n\n");
            sleep(2);
        }
    }

    /* ── Measurement loop ───────────────────────────────────────────────── */

    printf("Collecting packets...\n\n");

    /* Static arrays — declared static so they are pre-faulted and do not
     * hit the stack limit on vm-li. */
    static uint64_t owl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES]; /* MAX_SAMPLES-1 valid entries */
    static uint64_t seq_arr [MAX_SAMPLES];
    static uint64_t send_arr[MAX_SAMPLES];
    static uint64_t recv_arr[MAX_SAMPLES];

    size_t   n_stored     = 0;
    size_t   n_iaj        = 0;
    uint64_t n_expected   = 0;   /* running expected seq counter */
    uint64_t n_lost       = 0;
    uint64_t prev_recv_ns = 0;
    bool     first_pkt    = true;

    static struct net_message msg;

    while (n_stored < (size_t)n_target) {
        ssize_t nb = recv(sock, &msg, sizeof(msg), 0);

        if (nb < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                /* Timeout: sender may have stalled or the run ended early. */
                printf("WARNING: recv timeout — sender may have stopped "
                       "(collected %zu/%d so far)\n", n_stored, n_target);
                n_lost++;
                continue;
            }
            fprintf(stderr, "ERROR: recv() failed: %s\n", strerror(errno));
            break;
        }

        if (nb != (ssize_t)sizeof(msg)) {
            /* Unexpected size: stale data or mis-sized packet — discard. */
            printf("WARNING: unexpected packet size %zd (expected %zu)\n",
                   nb, sizeof(msg));
            continue;
        }

        uint64_t recv_ns = get_time_ns();

        /* ── Sequence-gap loss detection ──────────────────────────────── */
        if (!first_pkt && msg.seq > n_expected) {
            uint64_t gap = msg.seq - n_expected;
            printf("WARNING: seq gap — expected %llu got %llu "
                   "(%llu packet(s) lost)\n",
                   (unsigned long long)n_expected,
                   (unsigned long long)msg.seq,
                   (unsigned long long)gap);
            n_lost += gap;
        }
        n_expected = msg.seq + 1;
        first_pkt  = false;

        /* ── OWL: recv_time_li − send_time_hi ─────────────────────────── */
        uint64_t owl = (recv_ns >= msg.send_time_ns)
                       ? (recv_ns - msg.send_time_ns)
                       : 0; /* guard: should never be negative in QEMU */

        /* ── IAJ: |inter-arrival time − expected period| ──────────────── */
        if (prev_recv_ns != 0 && n_iaj < MAX_SAMPLES) {
            uint64_t iat = recv_ns - prev_recv_ns;
            int64_t  dev = (int64_t)iat - (int64_t)SENDER_PERIOD_NS;
            iaj_ns[n_iaj++] = (uint64_t)(dev < 0 ? -dev : dev);
        }
        prev_recv_ns = recv_ns;

        owl_ns  [n_stored] = owl;
        seq_arr [n_stored] = msg.seq;
        send_arr[n_stored] = msg.send_time_ns;
        recv_arr[n_stored] = recv_ns;
        n_stored++;

        /* Progress update every 1000 packets. */
        if (n_stored % 1000 == 0) {
            printf("Progress: %zu/%d  (lost so far: %llu)\n",
                   n_stored, n_target, (unsigned long long)n_lost);
        }
    }

    /* ── Stop background flood ──────────────────────────────────────────── */

    if (flood_pid > 0) {
        printf("\nStopping net_flood (PID %d)...\n", (int)flood_pid);
        kill(flood_pid, SIGTERM);
        waitpid(flood_pid, NULL, 0);
        printf("net_flood stopped.\n");
    }

    /* ── Print results ──────────────────────────────────────────────────── */

    uint64_t n_sent_total = n_stored + n_lost;

    printf("\n========== NET RECEIVER RESULTS ==========\n");
    printf("Packets expected:  %5llu\n", (unsigned long long)n_sent_total);
    printf("Packets received:  %5zu\n",  n_stored);
    printf("Packets lost:      %5llu (%.3f%%)\n",
           (unsigned long long)n_lost,
           n_sent_total > 0 ? (double)n_lost / (double)n_sent_total * 100.0 : 0.0);

    print_metric("OWL", owl_ns, n_stored);
    print_metric("IAJ", iaj_ns, n_iaj);

    printf("==========================================\n");

    write_csv(csv_path, seq_arr, send_arr, recv_arr, owl_ns, iaj_ns, n_stored);

    close(sock);
    return (n_lost == 0) ? 0 : 1;
}
