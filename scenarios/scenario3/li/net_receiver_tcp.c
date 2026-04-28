/*
 * net_receiver_tcp.c — Scenario 3 (TCP variant)
 *
 * TCP client counterpart to net_sender_tcp on vm-hi.
 * Connects to net_sender_tcp, receives N messages, then closes the
 * connection to signal end-of-run to the server.
 *
 * Protocol per run:
 *   1. connect() to NET_HI_IP:NET_SENDER_PORT
 *   2. set TCP_NODELAY on the socket
 *   3. receive exactly sizeof(net_message) bytes per message using a
 *      framed recv loop (TCP is a byte stream — partial reads are possible)
 *   4. record recv_time_ns immediately after the full message is reassembled
 *   5. after n_target messages, close() the socket — this signals end-of-run
 *      to net_sender_tcp without a separate signalling message
 *
 * Background flood (net_flood, UDP) is started/stopped exactly as in the
 * UDP version.  The flood uses UDP regardless of this file's transport,
 * because its purpose is to saturate the shared virtual NIC, not to test
 * a specific protocol.
 *
 * Key differences from net_receiver.c (UDP):
 *   - SOCK_STREAM + connect() + TCP_NODELAY instead of bind() + recvfrom()
 *   - recv_exact() loop reassembles each net_message from the TCP stream
 *   - No SO_RCVTIMEO: blocking recv() is used; a connection timeout is
 *     applied via SO_RCVTIMEO to detect a stalled sender
 *   - Loss detection: sequence gaps should never appear with TCP; any gap
 *     indicates a software bug and is flagged as an error rather than a
 *     counted loss
 *   - Output format is identical to net_receiver.c so analyze_scenario3.py
 *     can parse both without modification
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
#include <signal.h>
#include <math.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "common.h"

#define MAX_SAMPLES 10000

/* ── Timing helper ──────────────────────────────────────────────────────── */

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ── Framed receive helper ──────────────────────────────────────────────── */
/*
 * Read exactly `len` bytes from fd.  TCP may deliver data in fragments;
 * loop until the complete struct is available.
 * Returns  len on success, 0 if the connection was closed cleanly, -1 on error.
 */
static ssize_t recv_exact(int fd, void *buf, size_t len)
{
    size_t received = 0;
    while (received < len) {
        ssize_t n = recv(fd, (char *)buf + received, len - received, 0);
        if (n == 0) return 0;   /* peer closed connection */
        if (n < 0)  return -1;  /* error */
        received += (size_t)n;
    }
    return (ssize_t)received;
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
    if (n == 0) { printf("%s: no samples\n", label); return; }

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

static void write_csv(const char     *path,
                      const uint64_t *seqs,
                      const uint64_t *send_ns,
                      const uint64_t *recv_ns,
                      const uint64_t *owl_ns,
                      const uint64_t *iaj_ns,
                      size_t          n)
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
    int         n_target   = 5000;
    int         flood_mbps = 0;
    const char *csv_path   = "net_receiver_tcp_log.csv";

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
            printf("  -o <file>    Output CSV (default: net_receiver_tcp_log.csv)\n");
            printf("  -h           Show this help\n");
            printf("\nTransport: TCP (connects to %s:%d)\n",
                   NET_HI_IP, NET_SENDER_PORT);
            printf("Message size: %zu B  (NET_PAYLOAD_BYTES=%d)\n",
                   sizeof(struct net_message), NET_PAYLOAD_BYTES);
            return 0;
        }
    }

    if (n_target <= 0 || n_target > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be between 1 and %d\n", MAX_SAMPLES);
        return 1;
    }

    printf("========================================\n");
    printf("net_receiver_tcp — Scenario 3 (TCP)\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Collecting:   %d packets\n",   n_target);
    printf("Message size: %zu B\n",        sizeof(struct net_message));
    printf("Flood rate:   %d Mbps (UDP)\n", flood_mbps);
    printf("Server:       %s:%d\n\n",      NET_HI_IP, NET_SENDER_PORT);

    /* ── Start background UDP flood (optional) ──────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0) {
        char bw_str[32];
        snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);

        printf("Starting background UDP flood → %s:%d at %d Mbps\n\n",
               NET_HI_IP, NET_FLOOD_PORT, flood_mbps);

        flood_pid = fork();
        if (flood_pid == 0) {
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
            printf("Waiting 2 s for flood to ramp up...\n\n");
            sleep(2);
        }
    }

    /* ── Connect to net_sender_tcp ──────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        fprintf(stderr, "ERROR: socket() failed: %s\n", strerror(errno));
        return 1;
    }

    /* Apply TCP_NODELAY on the client side as well for ACK timeliness. */
    int nodelay = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

    /* Increase receive buffer to absorb any kernel-level batching. */
    int rcvbuf = 1 << 20;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in srv = {0};
    srv.sin_family      = AF_INET;
    srv.sin_port        = htons(NET_SENDER_PORT);
    srv.sin_addr.s_addr = inet_addr(NET_HI_IP);

    printf("Connecting to %s:%d ...\n", NET_HI_IP, NET_SENDER_PORT);
    if (connect(sock, (struct sockaddr *)&srv, sizeof(srv)) != 0) {
        fprintf(stderr, "ERROR: connect() to %s:%d failed: %s\n",
                NET_HI_IP, NET_SENDER_PORT, strerror(errno));
        close(sock);
        if (flood_pid > 0) { kill(flood_pid, SIGTERM); waitpid(flood_pid, NULL, 0); }
        return 1;
    }
    printf("Connected. Collecting %d packets...\n\n", n_target);

    /* Apply a receive timeout so we don't block forever if the sender stalls. */
    struct timeval tv = {
        .tv_sec  = RECV_TIMEOUT_MS / 1000,
        .tv_usec = (RECV_TIMEOUT_MS % 1000) * 1000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    /* ── Measurement loop ───────────────────────────────────────────────── */

    static uint64_t owl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES];
    static uint64_t seq_arr [MAX_SAMPLES];
    static uint64_t send_arr[MAX_SAMPLES];
    static uint64_t recv_arr[MAX_SAMPLES];

    size_t   n_stored     = 0;
    size_t   n_iaj        = 0;
    uint64_t n_expected   = 0;
    uint64_t n_lost       = 0;   /* should remain 0 with TCP */
    uint64_t prev_recv_ns = 0;
    bool     first_pkt    = true;

    static struct net_message msg;

    while (n_stored < (size_t)n_target) {
        ssize_t nb = recv_exact(sock, &msg, sizeof(msg));

        if (nb == 0) {
            printf("WARNING: server closed the connection after %zu/%d packets\n",
                   n_stored, n_target);
            break;
        }
        if (nb < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                printf("WARNING: recv timeout — sender may have stalled "
                       "(collected %zu/%d so far)\n", n_stored, n_target);
                /* With TCP this is very unusual; count as a loss and break
                 * since the stream may be out of sync. */
                n_lost++;
                //break;
            }
            fprintf(stderr, "ERROR: recv_exact() failed: %s\n",
                    strerror(errno));
            break;
        }

        uint64_t recv_ns = get_time_ns();

        /* ── Sequence sanity check (should never trigger with TCP) ──── */
        if (!first_pkt && msg.seq != n_expected) {
            printf("ERROR: unexpected seq %llu (expected %llu) — "
                   "TCP stream may be corrupt\n",
                   (unsigned long long)msg.seq,
                   (unsigned long long)n_expected);
            /* Count the gap as lost and resync to the received seq. */
            if (msg.seq > n_expected)
                n_lost += msg.seq - n_expected;
        }
        n_expected = msg.seq + 1;
        first_pkt  = false;

        /* ── OWL ─────────────────────────────────────────────────────── */
        uint64_t owl = (recv_ns >= msg.send_time_ns)
                       ? (recv_ns - msg.send_time_ns)
                       : 0;

        /* ── IAJ ─────────────────────────────────────────────────────── */
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

        if (n_stored % 1000 == 0) {
            printf("Progress: %zu/%d  (seq errors so far: %llu)\n",
                   n_stored, n_target, (unsigned long long)n_lost);
        }
    }

    /* ── Close connection to signal end-of-run to net_sender_tcp ───────── */
    close(sock);

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
    printf("Transport:         TCP\n");
    printf("Packets expected:  %5llu\n", (unsigned long long)n_sent_total);
    printf("Packets received:  %5zu\n",  n_stored);
    /* With TCP, n_lost reflects seq errors (bugs), not network drops. */
    printf("Packets lost:      %5llu (%.3f%%) [seq errors — expected 0 with TCP]\n",
           (unsigned long long)n_lost,
           n_sent_total > 0 ? (double)n_lost / (double)n_sent_total * 100.0 : 0.0);

    print_metric("OWL", owl_ns, n_stored);
    print_metric("IAJ", iaj_ns, n_iaj);

    printf("==========================================\n");

    write_csv(csv_path, seq_arr, send_arr, recv_arr, owl_ns, iaj_ns, n_stored);

    return (n_lost == 0) ? 0 : 1;
}
