/*
 * net_receiver.c — Scenario 3 pinger / RTL measurer (vm-li)
 *
 * Sends periodic probes to the echo server (vm-hi) at 1 ms intervals and
 * measures Round-Trip Latency (RTL) on its own CLOCK_MONOTONIC.  Because
 * both timestamps (send and recv) are on the same clock there is no need
 * for cross-VM synchronisation.
 *
 * Usage: net_receiver [-n <count>] [-B <Mbps>] [-t] [-o <csv>]
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
#include <sched.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "common.h"

/* Maximum probes stored per run.  64 B/msg → ~640 KB. */
#define MAX_SAMPLES 10000

/* ── Timing helpers ───────────────────────────────────────────────────────────── */

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static inline struct timespec ns_to_timespec(uint64_t ns)
{
    struct timespec ts;
    ts.tv_sec  = (time_t)(ns / 1000000000ULL);
    ts.tv_nsec = (long)(ns % 1000000000ULL);
    return ts;
}

/* ── RT configuration ──────────────────────────────────────────────────────────── */

static void configure_realtime(void)
{
    struct sched_param param = { .sched_priority = 90 };
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        printf("net_receiver: SCHED_FIFO: FAILED (%s) — SCHED_OTHER\n",
               strerror(errno));
    } else {
        printf("net_receiver: SCHED_FIFO: OK (priority 90)\n");
    }
}

/* ── TCP helper ───────────────────────────────────────────────────────────── */

static ssize_t recv_full(int fd, struct net_message *msg)
{
    size_t received = 0;
    while (received < sizeof(*msg)) {
        ssize_t r = recv(fd, (char *)msg + received, sizeof(*msg) - received, 0);
        if (r == 0) return 0;
        if (r < 0)  return -1;
        received += (size_t)r;
    }
    return (ssize_t)received;
}

/* ── Statistics ─────────────────────────────────────────────────────────────────── */

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
    for (size_t i = 0; i < n; i++) { double d = (double)samples[i] - mean; sq += d*d; }
    double stddev = sqrt(sq / (double)n);

    printf("%s (us):\n", label);
    printf("  Mean: %8.3f    Std dev: %.3f\n", mean/1e3, stddev/1e3);
    printf("  Min:  %8.3f    Max:     %.3f\n", sorted[0]/1e3, sorted[n-1]/1e3);
    printf("  P50:  %8.3f    P95:     %.3f\n",
           pct(sorted,n,0.50)/1e3, pct(sorted,n,0.95)/1e3);
    printf("  P99:  %8.3f    P99.9:   %.3f\n",
           pct(sorted,n,0.99)/1e3, pct(sorted,n,0.999)/1e3);
}

/* ── CSV writer ─────────────────────────────────────────────────────────────────── */

static void write_csv(const char     *path,
                      const uint64_t *seqs,
                      const uint64_t *send_ns,
                      const uint64_t *recv_ns,
                      const uint64_t *rtl_ns,
                      const uint64_t *iaj_ns,
                      size_t          n)
{
    FILE *fp = fopen(path, "w");
    if (!fp) {
        printf("WARNING: could not open CSV '%s': %s\n", path, strerror(errno));
        return;
    }
    fprintf(fp, "seq,send_time_ns,recv_time_ns,rtlatency_us,iat_us\n");
    for (size_t i = 0; i < n; i++) {
        fprintf(fp, "%llu,%llu,%llu,%.3f,%.3f\n",
                (unsigned long long)seqs[i],
                (unsigned long long)send_ns[i],
                (unsigned long long)recv_ns[i],
                (double)rtl_ns[i] / 1e3,
                i > 0 ? (double)iaj_ns[i - 1] / 1e3 : 0.0);
    }
    fclose(fp);
    printf("CSV written to: %s\n", path);
}

/* ── Main ─────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int         n_target   = 5000;
    int         flood_mbps = 0;
    bool        tcp_mode   = false;
    const char *csv_path   = "net_receiver_log.csv";

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n_target = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-B") == 0 && i + 1 < argc) {
            flood_mbps = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-t") == 0) {
            tcp_mode = true;
        } else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            csv_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("  -n <count>   Probes to send (default: 5000, max: %d)\n", MAX_SAMPLES);
            printf("  -B <Mbps>    Background flood rate (default: 0 = off)\n");
            printf("  -t           Request TCP transport (default: UDP)\n");
            printf("  -o <file>    Output CSV (default: net_receiver_log.csv)\n");
            printf("  -h           Show this help\n");
            printf("\nMessage size: %zu B  (NET_PAYLOAD_BYTES=%d)\n",
                   sizeof(struct net_message), NET_PAYLOAD_BYTES);
            return 0;
        }
    }

    if (n_target <= 0 || n_target > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be 1..%d\n", MAX_SAMPLES);
        return 1;
    }

    printf("========================================\n");
    printf("net_receiver (pinger) — Scenario 3\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("Metric: Round-Trip Latency (RTL)\n");
    printf("========================================\n");
    printf("Probes:       %d\n",   n_target);
    printf("Protocol:     %s\n",   tcp_mode ? "TCP" : "UDP");
    printf("Period:       %llu ms\n",
           (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Msg size:     %zu B\n",   sizeof(struct net_message));
    printf("Flood rate:   %d Mbps\n", flood_mbps);
    printf("Echo timeout: %d ms\n\n", RECV_TIMEOUT_MS);

    configure_realtime();

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("net_receiver: mlockall: %s — continuing\n", strerror(errno));

    /* ── Create UDP control socket ───────────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { fprintf(stderr, "ERROR: socket: %s\n", strerror(errno)); return 1; }

    int rcvbuf = 1 << 20;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    /* Bind so the echo server can reply to us at a known port. */
    struct sockaddr_in baddr = {0};
    baddr.sin_family      = AF_INET;
    baddr.sin_port        = htons(NET_SENDER_PORT);
    baddr.sin_addr.s_addr = INADDR_ANY;
    if (bind(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) {
        fprintf(stderr, "ERROR: bind(port %d): %s\n", NET_SENDER_PORT, strerror(errno));
        return 1;
    }

    struct sockaddr_in hi_addr = {0};
    hi_addr.sin_family      = AF_INET;
    hi_addr.sin_port        = htons(NET_SENDER_PORT);
    hi_addr.sin_addr.s_addr = inet_addr(NET_HI_IP);

    /* ── HELLO handshake (UDP) ─────────────────────────────────────────────────── */

    {
        struct timeval tv_hello = { .tv_sec = 0, .tv_usec = 500000 };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv_hello, sizeof(tv_hello));

        static struct net_message probe;
        static struct net_message reply;
        uint64_t n_target_u64 = (uint64_t)n_target;
        unsigned tries = 0;

        printf("Sending HELLO to %s:%d (protocol=%s)...\n",
               NET_HI_IP, NET_SENDER_PORT, tcp_mode ? "TCP" : "UDP");

        bool acked = false;
        while (!acked) {
            memset(&probe, 0, sizeof(probe));
            probe.seq        = NET_CTRL_SEQ_HELLO;
            probe.payload[0] = tcp_mode ? 1 : 0;
            memcpy(probe.payload + 1, &n_target_u64, sizeof(uint64_t));

            sendto(sock, &probe, sizeof(probe), 0,
                   (struct sockaddr *)&hi_addr, sizeof(hi_addr));

            ssize_t nb = recv(sock, &reply, sizeof(reply), 0);
            if (nb == (ssize_t)sizeof(reply) &&
                reply.seq == NET_CTRL_SEQ_HELLO_ACK) {
                tcp_mode = (reply.payload[0] != 0); /* server-confirmed protocol */
                acked = true;
                printf("HELLO_ACK received — protocol=%s\n",
                       tcp_mode ? "TCP" : "UDP");
            } else {
                tries++;
                if (tries % 10 == 0)
                    printf("Still waiting for HELLO_ACK (%u tries)...\n", tries);
            }
        }
    }

    /* ── Switch to TCP if requested ─────────────────────────────────────────────── */

    if (tcp_mode) {
        close(sock);
        sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) {
            fprintf(stderr, "ERROR: TCP socket: %s\n", strerror(errno));
            return 1;
        }
        int nodelay = 1;
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
        printf("Connecting TCP to %s:%d...\n", NET_HI_IP, NET_SENDER_PORT);
        if (connect(sock, (struct sockaddr *)&hi_addr, sizeof(hi_addr)) != 0) {
            fprintf(stderr, "ERROR: TCP connect: %s\n", strerror(errno));
            return 1;
        }
        printf("TCP connection established (TCP_NODELAY set)\n\n");
    } else {
        /* UDP: connect() so send() and recv() are scoped to hi only. */
        if (connect(sock, (struct sockaddr *)&hi_addr, sizeof(hi_addr)) != 0) {
            fprintf(stderr, "ERROR: UDP connect to echo server: %s\n", strerror(errno));
            return 1;
        }
    }

    /* Set echo-recv timeout for the data phase. */
    {
        struct timeval tv = {
            .tv_sec  = RECV_TIMEOUT_MS / 1000,
            .tv_usec = (RECV_TIMEOUT_MS % 1000) * 1000,
        };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    }

    /* ── Background flood (optional) ──────────────────────────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0) {
        char bw_str[32];
        snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);
        printf("Starting background flood → %s:%d at %d Mbps\n",
               NET_HI_IP, NET_FLOOD_PORT, flood_mbps);
        flood_pid = fork();
        if (flood_pid == 0) {
            execlp("net_flood", "net_flood",
                   "-d", NET_HI_IP, "-p", "5000", "-b", bw_str, NULL);
            perror("execlp net_flood"); exit(1);
        } else if (flood_pid < 0) {
            fprintf(stderr, "WARNING: fork net_flood: %s\n", strerror(errno));
            flood_pid = -1;
        } else {
            printf("Waiting 2 s for flood to ramp up...\n\n");
            sleep(2);
        }
    }

    /* ── Static measurement arrays ─────────────────────────────────────────────────── */

    static uint64_t rtl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES];
    static uint64_t seq_arr [MAX_SAMPLES];
    static uint64_t send_arr[MAX_SAMPLES];
    static uint64_t recv_arr[MAX_SAMPLES];

    size_t   n_stored     = 0;
    size_t   n_iaj        = 0;
    uint64_t n_lost       = 0;
    uint64_t prev_recv_ns = 0;
    bool     last_was_loss = false;

    static struct net_message msg;
    static struct net_message echo;

    printf("Starting ping-echo loop (%d probes at 1 ms period)...\n\n", n_target);

    /* ── Ping-echo loop ────────────────────────────────────────────────────────── */

    uint64_t seq       = 0;
    uint64_t next_wake = get_time_ns() + SENDER_PERIOD_NS;

    while (seq < (uint64_t)n_target) {

        /* Absolute-time sleep.  If we are already past next_wake (e.g. after
         * a lost-echo timeout) reset to avoid a catch-up burst. */
        uint64_t now = get_time_ns();
        if (now < next_wake) {
            struct timespec ts = ns_to_timespec(next_wake - now);
            nanosleep(&ts, NULL);
        } else {
            next_wake = now; /* drop backlog */
        }

        /* ── Send probe ──────────────────────────────────────────────────────── */

        memset(msg.payload, 0xAB, sizeof(msg.payload));
        msg.seq          = seq;
        msg.send_time_ns = get_time_ns();   /* vm-li clock */

        if (send(sock, &msg, sizeof(msg), 0) < 0) {
            if (errno != ENOBUFS && errno != EAGAIN)
                printf("WARNING: send seq=%llu: %s\n",
                       (unsigned long long)seq, strerror(errno));
            n_lost++;
            last_was_loss = true;
            prev_recv_ns  = 0;
            seq++;
            next_wake += SENDER_PERIOD_NS;
            continue;
        }

        /* ── Receive echo ───────────────────────────────────────────────────── */

        ssize_t nb;
        if (tcp_mode)
            nb = recv_full(sock, &echo);
        else
            nb = recv(sock, &echo, sizeof(echo), 0);

        uint64_t recv_time_ns = get_time_ns();  /* vm-li clock */

        bool got_echo = (nb == (ssize_t)sizeof(echo) && echo.seq == seq);

        if (!got_echo) {
            if (nb < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
                printf("WARNING: recv echo seq=%llu: %s\n",
                       (unsigned long long)seq, strerror(errno));
            else if (nb >= 0 && nb != (ssize_t)sizeof(echo))
                printf("WARNING: echo wrong size %zd seq=%llu\n",
                       nb, (unsigned long long)seq);
            n_lost++;
            last_was_loss = true;
            prev_recv_ns  = 0;
            seq++;
            next_wake += SENDER_PERIOD_NS;
            continue;
        }

        /* ── Record RTL and IAJ ──────────────────────────────────────────────── */

        /* RTL = echo_recv_time − probe_send_time (both on vm-li CLOCK_MONOTONIC). */
        uint64_t rtl = recv_time_ns - echo.send_time_ns;

        /* IAJ: skip across any loss boundary. */
        if (prev_recv_ns != 0 && !last_was_loss && n_iaj < MAX_SAMPLES) {
            uint64_t iat = recv_time_ns - prev_recv_ns;
            int64_t  dev = (int64_t)iat - (int64_t)SENDER_PERIOD_NS;
            iaj_ns[n_iaj++] = (uint64_t)(dev < 0 ? -dev : dev);
        }

        rtl_ns  [n_stored] = rtl;
        seq_arr [n_stored] = seq;
        send_arr[n_stored] = echo.send_time_ns;
        recv_arr[n_stored] = recv_time_ns;
        n_stored++;

        prev_recv_ns  = recv_time_ns;
        last_was_loss = false;

        if (n_stored % 1000 == 0)
            printf("Progress: %zu/%d  (lost: %llu)\n",
                   n_stored, n_target, (unsigned long long)n_lost);

        seq++;
        next_wake += SENDER_PERIOD_NS;
    }

    /* TCP: close connection to signal end-of-run to echo server. */
    if (tcp_mode) close(sock);

    /* ── Stop background flood ──────────────────────────────────────────────────────── */

    if (flood_pid > 0) {
        printf("\nStopping net_flood (PID %d)...\n", (int)flood_pid);
        kill(flood_pid, SIGTERM);
        waitpid(flood_pid, NULL, 0);
        printf("net_flood stopped.\n");
    }

    /* ── Print results ──────────────────────────────────────────────────────────────── */

    uint64_t n_probes_total = (uint64_t)n_target;

    printf("\n========== NET RECEIVER (PINGER) RESULTS ==========\n");
    printf("Probes sent:       %5llu\n", (unsigned long long)n_probes_total);
    printf("Echoes received:   %5zu\n",  n_stored);
    printf("Lost (no echo):    %5llu (%.3f%%)\n",
           (unsigned long long)n_lost,
           n_probes_total > 0
               ? (double)n_lost / (double)n_probes_total * 100.0 : 0.0);

    print_metric("RTL", rtl_ns, n_stored);
    print_metric("IAJ", iaj_ns, n_iaj);

    printf("===================================================\n");

    write_csv(csv_path, seq_arr, send_arr, recv_arr, rtl_ns, iaj_ns, n_stored);

    if (!tcp_mode) close(sock);
    return (n_lost == 0) ? 0 : 1;
}
