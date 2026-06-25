/*
 * net_client.c — Scenario 3 
 *
 * Sends periodic probes to the echo server (net_receiver_hi) on vm-hi at
 * 1 ms intervals, then receives each echo and measures Round-Trip Time
 * (RTT) on its own CLOCK_MONOTONIC.  Both timestamps (send + recv) are on
 * vm-li's clock, so no cross-VM synchronisation is required.
 *
 *
 * Usage: net_client [-n <probes>] [-B <Mbps>] [-t] [-o <csv>]
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sched.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "common.h"

#define MAX_SAMPLES 10000

static volatile int g_running = 1;
static void sigint_handler(int sig) { (void)sig; g_running = 0; }

/* ── Timing ───────────────────────────────────────────────────────────────────── */

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

/* ── RT configuration ────────────────────────────────────────────────────────────── */

static void configure_realtime(void)
{
    struct sched_param param = { .sched_priority = 90 };
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0)
        printf("net_client: SCHED_FIFO FAILED (%s) — SCHED_OTHER\n", strerror(errno));
    else
        printf("net_client: SCHED_FIFO OK (priority 90)\n");
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

// static void write_csv(const char     *path,
//                       const uint64_t *seqs,
//                       const uint64_t *send_ns,
//                       const uint64_t *recv_ns,
//                       const uint64_t *rtl_ns,
//                       const uint64_t *iaj_ns,
//                       size_t          n)
// {
//     FILE *fp = fopen(path, "w");
//     if (!fp) { printf("WARNING: open CSV '%s': %s\n", path, strerror(errno)); return; }
//     fprintf(fp, "seq,send_time_ns,recv_time_ns,rtlatency_us,iat_us\n");
//     for (size_t i = 0; i < n; i++) {
//         fprintf(fp, "%llu,%llu,%llu,%.3f,%.3f\n",
//                 (unsigned long long)seqs[i],
//                 (unsigned long long)send_ns[i],
//                 (unsigned long long)recv_ns[i],
//                 (double)rtl_ns[i] / 1e3,
//                 i > 0 ? (double)iaj_ns[i - 1] / 1e3 : 0.0);
//     }
//     fclose(fp);
//     printf("CSV written to: %s\n", path);
// }

/* ── Background flood helper ────────────────────────────────────────────────────── */

static void start_background_flood(pid_t *flood_pid, int flood_mbps)
{
    char bw_str[32];
    snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);
    printf("Starting background flood → %s:%d at %d Mbps\n",
           NET_HI_IP, NET_FLOOD_PORT, flood_mbps);
    *flood_pid = fork();
    if (*flood_pid == 0) {
        execlp("net_flood", "net_flood",
               "-d", NET_HI_IP, "-p", "5001", "-b", bw_str, NULL);
        perror("execlp net_flood"); exit(1);
    } else if (*flood_pid < 0) {
        fprintf(stderr, "WARNING: fork net_flood: %s — continuing\n", strerror(errno));
        *flood_pid = -1;
    } else {
        printf("Waiting 2 s for flood to ramp up...\n\n");
        sleep(2);
    }
}

/* ── Main ───────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    bool        tcp_mode   = false;
    int         n_probes   = 5000;
    int         flood_mbps = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n_probes = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-t") == 0) {
            tcp_mode = true;
        // } else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
        //     csv_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("  -n <count>  Probes to send (default: 5000, max: %d)\n", MAX_SAMPLES);
            printf("  -t          Use TCP (default: UDP)\n");
            printf("  -o <file>   Output CSV (default: net_client_log.csv)\n");
            printf("  -h          Show this help\n");
            return 0;
        }
    }

    if (n_probes <= 0 || n_probes > MAX_SAMPLES) {
        fprintf(stderr, "ERROR: -n must be 1..%d\n", MAX_SAMPLES);
        return 1;
    }

    printf("========================================\n");
    printf("net_client (pinger) — Scenario 3 reversed\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("Metric: Round-Trip Latency (RTL)\n");
    printf("========================================\n");
    printf("Target:       %s:%d\n", NET_HI_IP, NET_SENDER_PORT);
    printf("Probes:       %d\n",    n_probes);
    printf("Protocol:     %s\n",    tcp_mode ? "TCP" : "UDP");
    printf("Period:       %llu ms\n",
           (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Msg size:     %zu B\n",   sizeof(struct net_message));
    printf("Flood rate:   %d Mbps\n", flood_mbps);
    printf("Echo timeout: %d ms\n\n", RECV_TIMEOUT_MS);

    configure_realtime();
    signal(SIGINT, sigint_handler);

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("net_client: mlockall: %s — continuing\n", strerror(errno));

    /* ── Create UDP socket for handshake ───────────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { fprintf(stderr, "ERROR: socket: %s\n", strerror(errno)); return 1; }

    int sndbuf = 262144;
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));
    int rcvbuf = 1 << 20;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    /* Bind to NET_LI_IP so the echo server can address our replies. */
    struct sockaddr_in src = {0};
    src.sin_family      = AF_INET;
    src.sin_port        = 0;  /* kernel assigns ephemeral port */
    src.sin_addr.s_addr = inet_addr(NET_LI_IP);
    if (bind(sock, (struct sockaddr *)&src, sizeof(src)) != 0)
        printf("net_client: WARNING: bind(%s): %s\n", NET_LI_IP, strerror(errno));

    /* connect() scopes send()/recv() to the echo server. */
    struct sockaddr_in dst = {0};
    dst.sin_family      = AF_INET;
    dst.sin_port        = htons(NET_SENDER_PORT);
    dst.sin_addr.s_addr = inet_addr(NET_HI_IP);
    if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
        fprintf(stderr, "ERROR: UDP connect to %s:%d: %s\n",
                NET_HI_IP, NET_SENDER_PORT, strerror(errno));
        close(sock);
        return 1;
    }

    /* ── HELLO handshake (UDP) ─────────────────────────────────────────────────────── */

    {
        struct timeval hello_tv = { .tv_sec = 0, .tv_usec = 250000 };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &hello_tv, sizeof(hello_tv));

        static struct net_message probe;
        static struct net_message reply;
        uint64_t n_probes_u64 = (uint64_t)n_probes;
        unsigned tries = 0;
        bool acked = false;

        printf("Sending HELLO to %s:%d (protocol=%s)...\n",
               NET_HI_IP, NET_SENDER_PORT, tcp_mode ? "TCP" : "UDP");

        while (!acked && g_running) {
            memset(&probe, 0, sizeof(probe));
            probe.seq        = NET_CTRL_SEQ_HELLO;
            probe.payload[0] = tcp_mode ? 1 : 0;
            memcpy(probe.payload + 1, &n_probes_u64, sizeof(uint64_t));

            send(sock, &probe, sizeof(probe), 0);

            ssize_t nb = recv(sock, &reply, sizeof(reply), 0);
            if (nb == (ssize_t)sizeof(reply) &&
                reply.seq == NET_CTRL_SEQ_HELLO_ACK) {
                tcp_mode = (reply.payload[0] != 0);
                acked = true;
                printf("HELLO_ACK received — protocol=%s\n",
                       tcp_mode ? "TCP" : "UDP");
            } else {
                tries++;
                if (tries % 10 == 0)
                    printf("Still waiting for HELLO_ACK (%u tries)...\n", tries);
            }
        }

        if (!acked) {
            printf("net_client: interrupted during handshake\n");
            close(sock);
            return 1;
        }
    }

    /* ── Switch to TCP if requested ──────────────────────────────────────────────────────────── */

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
        if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
            fprintf(stderr, "ERROR: TCP connect: %s\n", strerror(errno));
            close(sock);
            return 1;
        }
        printf("TCP connected (TCP_NODELAY set)\n\n");
    }

    /* Set echo-recv timeout for the ping loop. */
    {
        struct timeval tv = {
            .tv_sec  = RECV_TIMEOUT_MS / 1000,
            .tv_usec = (RECV_TIMEOUT_MS % 1000) * 1000,
        };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    }

    /* ── Background flood (optional) ──────────────────────────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0)
        start_background_flood(&flood_pid, flood_mbps);

    /* ── Static measurement arrays ─────────────────────────────────────────────────── */

    static uint64_t rtl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES];
    static uint64_t send_arr[MAX_SAMPLES];
    static uint64_t recv_arr[MAX_SAMPLES];

    size_t   n_stored     = 0;
    size_t   n_iaj        = 0;
    uint64_t n_lost       = 0;
    uint64_t prev_recv_ns = 0;
    bool     last_was_loss = false;

    static struct net_message msg;
    static struct net_message echo;

    printf("Starting ping-echo loop (%d probes at 1 ms period)...\n\n", n_probes);

    /* ── Ping-echo loop ────────────────────────────────────────────────────────── */

    uint64_t seq       = 0;
    uint64_t start_ns  = get_time_ns();
    uint64_t next_wake = start_ns + SENDER_PERIOD_NS;

    while (g_running && seq < (uint64_t)n_probes) {

        uint64_t now = get_time_ns();
        if (now < next_wake) {
            struct timespec wake_ts = ns_to_timespec(next_wake);
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake_ts, NULL);
        } else {
            next_wake = now; /* drop backlog: avoid catch-up burst after loss */
        }

        /* ── Send probe ──────────────────────────────────────────────────────── */

        memset(msg.payload, 0xCD, sizeof(msg.payload));
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
            n_lost++;
            last_was_loss = true;
            prev_recv_ns  = 0;
            seq++;
            next_wake += SENDER_PERIOD_NS;
            continue;
        }

        /* ── Record RTL and IAJ ──────────────────────────────────────────────── */

        uint64_t rtl = recv_time_ns - msg.send_time_ns;
        if (rtl > RECV_TIMEOUT_MS * 1000000ULL) {
            n_lost++;
            last_was_loss = true;
        }

        if (prev_recv_ns != 0 && !last_was_loss && n_iaj < MAX_SAMPLES) {
            uint64_t iat = recv_time_ns - prev_recv_ns;
            int64_t  dev = (int64_t)iat - (int64_t)SENDER_PERIOD_NS;
            iaj_ns[n_iaj++] = (uint64_t)(dev < 0 ? -dev : dev);
        }

        rtl_ns  [n_stored] = rtl;
        send_arr[n_stored] = msg.send_time_ns;
        recv_arr[n_stored] = recv_time_ns;
        n_stored++;

        prev_recv_ns  = recv_time_ns;
        last_was_loss = false;

        // if (n_stored % 1000 == 0)
        //     printf("Progress: %zu/%d  (lost: %llu)\n",
        //            n_stored, n_probes, (unsigned long long)n_lost);

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

    /* ── Results ───────────────────────────────────────────────────────────────────── */

    double elapsed = (double)(get_time_ns() - start_ns) / 1e9;

    printf("\n========== NET SENDER_LI (PINGER) RESULTS ==========\n");
    printf("Probes sent:       %5d\n",   n_probes);
    printf("Echoes received:   %5zu\n",  n_stored);
    printf("Lost (no echo):    %5llu (%.3f%%)\n",
           (unsigned long long)n_lost,
           n_probes > 0 ? (double)n_lost / (double)n_probes * 100.0 : 0.0);
    printf("Elapsed:           %.3f s\n", elapsed);

    print_metric("RTL", rtl_ns, n_stored);
    print_metric("IAJ", iaj_ns, n_iaj);

    printf("===CSV_START===\n");
    printf("index,send_time_ns,recv_time_ns,rtl_ns,iaj_ns\n");
    for (size_t i = 0; i < n_stored; i++) {
        printf("%zu,%llu,%llu,%llu,%llu\n", i, (unsigned long long)send_arr[i], (unsigned long long)recv_arr[i], (unsigned long long)rtl_ns[i], (unsigned long long)iaj_ns[i]);
    }
    printf("===CSV_END===\n");

    printf("===================================================\n");

    if (!tcp_mode) close(sock);
    return (n_lost == 0) ? 0 : 1;
}
