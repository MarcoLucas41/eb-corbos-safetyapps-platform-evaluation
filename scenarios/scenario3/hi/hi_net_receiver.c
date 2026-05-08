/*
 * net_receiver_hi.c — Scenario 3 reversed UDP direction
 *
 * Runs as PID 1 on vm-hi.  Receives periodic UDP packets sent by
 * net_sender_li running on vm-li (li → hi direction).
 *
 * This is the inverse of the standard Scenario 3 setup (hi → li) and allows
 * direct comparison of OWL and IAJ in both traffic directions on the same
 * vnet_li_hi virtual link.
 *
 * Run lifecycle (supports multiple runs per QEMU boot):
 *   1. Wait indefinitely for the first packet of a new run.
 *   2. Once the first packet arrives, collect until END_OF_RUN_TIMEOUT_MS of
 *      silence (net_sender_li has finished).
 *   3. Print statistics to stdout (captured by qemu_serial.log).
 *   4. Write per-packet CSV to /tmp/ (tmpfs — always writable on hi).
 *   5. Reset and return to step 1.
 *
 * Key differences vs. net_receiver.c (li-side UDP receiver):
 *   - Runs on vm-hi: uses setbuf(stdout, NULL), nanosleep-safe, lisa-elf-enabler
 *   - No flood subprocess (receiver side; flood would need a separate binary)
 *   - Two-phase SO_RCVTIMEO: long timeout while idle, short timeout during run
 *   - PID 1 idle loop on exit
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

/* Maximum packets stored in memory per run.  At 64 B/msg this is ~640 KB. */
#define MAX_SAMPLES 10000

static volatile int g_running = 1;

static void sigint_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

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
    bool        tcp_mode   = false;
    int         nodelay   = -1;
    int         fallback_target = 5000;
    const char *csv_path   = "net_receiver_log.csv";

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            fallback_target = atoi(argv[++i]);
        } 
        else if (strcmp(argv[i], "-t") == 0) {
            tcp_mode = true;
        }
        else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            csv_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
                 printf("  -n <count>   Fallback packets if HELLO target invalid (default: 5000, max: %d)\n",
                   MAX_SAMPLES);
            printf("  -t           Use TCP instead of UDP\n");
            printf("  -o <file>    Output CSV (default: net_receiver_log.csv)\n");
            printf("  -h           Show this help\n");
            printf("\nMessage size: %zu B  (NET_PAYLOAD_BYTES=%d)\n",
                   sizeof(struct net_message), NET_PAYLOAD_BYTES);
            return 0;
        }
    }

    if (fallback_target <= 0 || fallback_target > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be between 1 and %d\n", MAX_SAMPLES);
        return 1;
    }

    signal(SIGINT, sigint_handler);

    printf("========================================\n");
    if(tcp_mode) {
        printf("net_receiver — Scenario 3 (TCP)\n");
    } else {
        printf("net_receiver — Scenario 3 (UDP)\n");
    }
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Collecting:   sender-defined target per run\n");
    printf("Message size: %zu B\n",        sizeof(struct net_message));
    printf("Recv timeout: %d ms\n\n",      RECV_TIMEOUT_MS);

    /* ── Create receive socket ──────────────────────────────────────────── */

    int sock = socket(AF_INET, tcp_mode ? SOCK_STREAM : SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "ERROR: socket() failed: %s\n", strerror(errno));
        return 1;
    }

    /* Apply TCP_NODELAY on the client side as well for ACK timeliness. */
    if(tcp_mode) {
        nodelay = 1;
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
    }

    /* Larger receive buffer absorbs short bursts without dropping. */
    int rcvbuf = 1 << 20; /* 1 MB */
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in baddr = {0};
    baddr.sin_family      = AF_INET;
    baddr.sin_port        = htons(NET_SENDER_PORT);
    baddr.sin_addr.s_addr = inet_addr(NET_HI_IP);

    if(tcp_mode)
    {
        if (connect(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) 
        {
            fprintf(stderr, "ERROR: connect() to %s:%d failed: %s\n",
                NET_HI_IP, NET_SENDER_PORT, strerror(errno));
            close(sock);
            return 1;
        }
    }
    else
    {
         if (bind(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) {
            fprintf(stderr, "ERROR: bind(port %d) failed: %s\n",
                    NET_SENDER_PORT, strerror(errno));
            return 1;
        }
    }

    /* SO_RCVTIMEO applies a receive timeout in case it detects a stalled sender */
    struct timeval tv = {
        .tv_sec  = RECV_TIMEOUT_MS / 1000,
        .tv_usec = (RECV_TIMEOUT_MS % 1000) * 1000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    /* Static arrays — declared static so they are pre-faulted and do not
     * hit the stack limit on vm-li. */
    static uint64_t owl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES]; /* MAX_SAMPLES-1 valid entries */
    static uint64_t seq_arr [MAX_SAMPLES];
    static uint64_t send_arr[MAX_SAMPLES];
    static uint64_t recv_arr[MAX_SAMPLES];

    static struct net_message msg;
    static struct net_message ack;
    uint64_t run_idx = 0;

    while (g_running) {
        struct sockaddr_in peer_addr = {0};
        socklen_t peer_len = sizeof(peer_addr);
        int n_target = 0;

        printf("\nWaiting for sender HELLO...\n");
        while (g_running) {
            ssize_t nb = recvfrom(sock, &msg, sizeof(msg), 0,
                                  (struct sockaddr *)&peer_addr, &peer_len);
            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                fprintf(stderr, "ERROR: recv() while waiting HELLO failed: %s\n", strerror(errno));
                close(sock);
                return 1;
            }

            if (nb != (ssize_t)sizeof(msg)) {
                continue;
            }

            if (msg.seq != NET_CTRL_SEQ_HELLO) {
                continue;
            }

            if (msg.send_time_ns > 0 && msg.send_time_ns <= MAX_SAMPLES) {
                n_target = (int)msg.send_time_ns;
            } else {
                n_target = fallback_target;
                printf("WARNING: HELLO target %llu invalid, using fallback %d\n",
                       (unsigned long long)msg.send_time_ns, n_target);
            }

            memset(&ack, 0, sizeof(ack));
            ack.seq = NET_CTRL_SEQ_HELLO_ACK;
            ack.send_time_ns = (uint64_t)n_target;
            if (sendto(sock, &ack, sizeof(ack), 0,
                       (struct sockaddr *)&peer_addr, peer_len) != (ssize_t)sizeof(ack)) {
                printf("WARNING: failed to send HELLO_ACK: %s\n", strerror(errno));
                continue;
            }
            break;
        }

        if (!g_running) {
            break;
        }

        run_idx++;
        printf("Run %llu started: target=%d packets\n",
               (unsigned long long)run_idx, n_target);

        size_t   n_stored     = 0;
        size_t   n_iaj        = 0;
        uint64_t n_expected   = 0;
        uint64_t n_lost       = 0;
        uint64_t prev_recv_ns = 0;
        bool     first_pkt    = true;

        while (g_running && n_stored < (size_t)n_target) {
            ssize_t nb = recvfrom(sock, &msg, sizeof(msg), 0,
                                  (struct sockaddr *)&peer_addr, &peer_len);

            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                fprintf(stderr, "ERROR: recv() failed: %s\n", strerror(errno));
                break;
            }

            if (nb != (ssize_t)sizeof(msg)) {
                printf("WARNING: unexpected packet size %zd (expected %zu)\n",
                       nb, sizeof(msg));
                continue;
            }

            if (msg.seq == NET_CTRL_SEQ_HELLO) {
                memset(&ack, 0, sizeof(ack));
                ack.seq = NET_CTRL_SEQ_HELLO_ACK;
                ack.send_time_ns = (uint64_t)n_target;
                sendto(sock, &ack, sizeof(ack), 0,
                       (struct sockaddr *)&peer_addr, peer_len);
                continue;
            }

            if (msg.seq == NET_CTRL_SEQ_HELLO_ACK) {
                continue;
            }

            uint64_t recv_ns = get_time_ns();

            if (!first_pkt && msg.seq > n_expected) {
                uint64_t gap = msg.seq - n_expected;
                printf("WARNING: seq gap — expected %llu got %llu (%llu packet(s) lost)\n",
                       (unsigned long long)n_expected,
                       (unsigned long long)msg.seq,
                       (unsigned long long)gap);
                n_lost += gap;
            }
            n_expected = msg.seq + 1;
            first_pkt = false;

            uint64_t owl = (recv_ns >= msg.send_time_ns)
                           ? (recv_ns - msg.send_time_ns)
                           : 0;

            if (prev_recv_ns != 0 && n_iaj < MAX_SAMPLES) {
                uint64_t iat = recv_ns - prev_recv_ns;
                int64_t dev = (int64_t)iat - (int64_t)SENDER_PERIOD_NS;
                iaj_ns[n_iaj++] = (uint64_t)(dev < 0 ? -dev : dev);
            }
            prev_recv_ns = recv_ns;

            owl_ns[n_stored] = owl;
            seq_arr[n_stored] = msg.seq;
            send_arr[n_stored] = msg.send_time_ns;
            recv_arr[n_stored] = recv_ns;
            n_stored++;

            if (n_stored % 1000 == 0) {
                printf("Run %llu progress: %zu/%d (lost=%llu)\n",
                       (unsigned long long)run_idx,
                       n_stored,
                       n_target,
                       (unsigned long long)n_lost);
            }
        }

        {
            uint64_t n_sent_total = n_stored + n_lost;

            printf("\n========== NET RECEIVER RESULTS (run %llu) ==========\n",
                   (unsigned long long)run_idx);
            printf("Packets expected:  %5llu\n", (unsigned long long)n_sent_total);
            printf("Packets received:  %5zu\n",  n_stored);
            printf("Packets lost:      %5llu (%.3f%%)\n",
                   (unsigned long long)n_lost,
                   n_sent_total > 0 ? (double)n_lost / (double)n_sent_total * 100.0 : 0.0);

            print_metric("OWL", owl_ns, n_stored);
            print_metric("IAJ", iaj_ns, n_iaj);
            printf("==========================================\n");
        }

        write_csv(csv_path, seq_arr, send_arr, recv_arr, owl_ns, iaj_ns, n_stored);

        if (g_running) {
            printf("Receiver returning to wait state for next run...\n");
        }
    }

    close(sock);
    return 0;
}
