/*
 * hi_net_ping.c — HI-side pinger (Scenario 3, hicom variant)
 *
 * Runs as PID 1 on vm-hi.  Spawns hi_net_echo as a child process, then
 * waits for trigger messages from net_trigger on vm-li.  Each trigger
 * starts a ping-echo experiment with hi_net_echo over the local network
 * stack (127.0.0.1), measuring RTL and IAJ entirely on vm-hi's
 * CLOCK_MONOTONIC.
 *
 * This mirrors the scenario2/hicom_variant pattern (shm_ping_hi) but
 * uses UDP/TCP networking instead of hicom shared memory.
 *
 * Lifecycle:
 *   1. Init, mlockall, spawn hi_net_echo via clone/execve.
 *   2. Create UDP socket on NET_HI_IP:NET_TRIGGER_PORT for triggers.
 *   3. Print "HI_NET_PING_READY".
 *   4. Wait for TRIGGER from net_trigger (vm-li).
 *   5. HELLO handshake with hi_net_echo (local).
 *   6. Ping-echo loop: send probe → recv echo → record RTL + IAJ.
 *   7. Print results, "HI_NET_PING_DONE run=<N>".
 *   8. Repeat from step 4.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sched.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "common.h"

/* ── Configuration ──────────────────────────────────────────────────────── */

#define DEFAULT_N_PROBES 5000
#define MAX_SAMPLES      10000

/* ── Timing helpers ─────────────────────────────────────────────────────── */

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

/* ── TCP helper ───────────────────────────────────────────────────────── */

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

/* ── Echo child spawning ────────────────────────────────────────────────── */

static int start_echo_child(void *arg __attribute__((unused)))
{
    char *const argv[] = {NULL};
    char *const envp[] = {NULL};
    execve("/hi_net_echo", argv, envp);
    printf("ERROR: execve /hi_net_echo failed: %s\n", strerror(errno));
    _exit(EXIT_FAILURE);
    return 0;
}

static void spawn_echo_child(void)
{
    enum { STACK_SIZE = 256 * 4096 };
    static char stack[STACK_SIZE] __attribute__((aligned(4096))) = {0};

    /* CLONE_VFORK suspends this process until the child calls execve,
     * guaranteeing hi_net_echo is running before we continue. */
    int ret = clone(start_echo_child, stack + STACK_SIZE,
                    CLONE_VFORK | CLONE_VM, NULL);
    if (ret == -1) {
        printf("ERROR: clone failed: %s\n", strerror(errno));
        /* Fatal on safety kernel — fall through to idle loop. */
    }
}

/* ── HELLO handshake with hi_net_echo ───────────────────────────────────── */

static int do_hello_handshake(bool tcp_mode, int n_probes)
{
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        printf("hi_net_ping: ERROR: UDP socket for HELLO: %s\n", strerror(errno));
        return -1;
    }

    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in echo_addr = {0};
    echo_addr.sin_family      = AF_INET;
    echo_addr.sin_port        = htons(NET_ECHO_PORT);
    echo_addr.sin_addr.s_addr = inet_addr(NET_ECHO_IP);

    /* Short timeout for HELLO retries. */
    struct timeval hello_tv = { .tv_sec = 0, .tv_usec = 500000 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &hello_tv, sizeof(hello_tv));

    static struct net_message probe;
    static struct net_message reply;
    uint64_t n_probes_u64 = (uint64_t)n_probes;
    unsigned tries = 0;
    bool acked = false;

    printf("hi_net_ping: sending HELLO to %s:%d (protocol=%s)...\n",
           NET_ECHO_IP, NET_ECHO_PORT, tcp_mode ? "TCP" : "UDP");

    while (!acked) {
        memset(&probe, 0, sizeof(probe));
        probe.seq        = NET_CTRL_SEQ_HELLO;
        probe.payload[0] = tcp_mode ? 1 : 0;
        memcpy(probe.payload + 1, &n_probes_u64, sizeof(uint64_t));

        sendto(sock, &probe, sizeof(probe), 0,
               (struct sockaddr *)&echo_addr, sizeof(echo_addr));

        ssize_t nb = recv(sock, &reply, sizeof(reply), 0);
        if (nb == (ssize_t)sizeof(reply) &&
            reply.seq == NET_CTRL_SEQ_HELLO_ACK) {
            acked = true;
            printf("hi_net_ping: HELLO_ACK received\n");
        } else {
            tries++;
            if (tries % 10 == 0)
                printf("hi_net_ping: still waiting for HELLO_ACK (%u tries)...\n",
                       tries);
        }
    }

    close(sock);
    return 0;
}

/* ── Single ping-echo run ───────────────────────────────────────────────── */

static void run_pings(bool tcp_mode, int n_probes, int run_idx)
{
    printf("HI_NET_PING_START run=%d\n", run_idx);
    printf("Probes: %d  |  Protocol: %s  |  Period: %llu ms  |  Msg: %zu B\n",
           n_probes, tcp_mode ? "TCP" : "UDP",
           (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL),
           sizeof(struct net_message));

    /* ── HELLO handshake with hi_net_echo ──────────────────────────────── */

    if (do_hello_handshake(tcp_mode, n_probes) < 0) {
        printf("HI_NET_PING_DONE run=%d (handshake failed)\n\n", run_idx);
        return;
    }

    /* ── Create data socket ───────────────────────────────────────────── */

    struct sockaddr_in echo_addr = {0};
    echo_addr.sin_family      = AF_INET;
    echo_addr.sin_port        = htons(NET_ECHO_PORT);
    echo_addr.sin_addr.s_addr = inet_addr(NET_ECHO_IP);

    int sock;
    if (tcp_mode) {
        sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) {
            printf("hi_net_ping: ERROR: TCP socket: %s\n", strerror(errno));
            printf("HI_NET_PING_DONE run=%d (socket failed)\n\n", run_idx);
            return;
        }
        int nodelay = 1;
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
        printf("hi_net_ping: connecting TCP to %s:%d...\n",
               NET_ECHO_IP, NET_ECHO_PORT);
        if (connect(sock, (struct sockaddr *)&echo_addr, sizeof(echo_addr)) != 0) {
            printf("hi_net_ping: ERROR: TCP connect: %s\n", strerror(errno));
            close(sock);
            printf("HI_NET_PING_DONE run=%d (TCP connect failed)\n\n", run_idx);
            return;
        }
        printf("hi_net_ping: TCP connected (TCP_NODELAY set)\n");
    } else {
        sock = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock < 0) {
            printf("hi_net_ping: ERROR: UDP socket: %s\n", strerror(errno));
            printf("HI_NET_PING_DONE run=%d (socket failed)\n\n", run_idx);
            return;
        }
        int rcvbuf = 1 << 20;
        setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));
        if (connect(sock, (struct sockaddr *)&echo_addr, sizeof(echo_addr)) != 0) {
            printf("hi_net_ping: ERROR: UDP connect: %s\n", strerror(errno));
            close(sock);
            printf("HI_NET_PING_DONE run=%d (UDP connect failed)\n\n", run_idx);
            return;
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

    /* ── Measurement arrays ───────────────────────────────────────────── */

    static uint64_t rtl_ns  [MAX_SAMPLES];
    static uint64_t iaj_ns  [MAX_SAMPLES];

    size_t   n_stored     = 0;
    size_t   n_iaj        = 0;
    uint64_t n_lost       = 0;
    uint64_t prev_recv_ns = 0;
    bool     last_was_loss = false;

    static struct net_message msg;
    static struct net_message echo;

    printf("hi_net_ping: starting ping-echo loop (%d probes at 1 ms)...\n\n",
           n_probes);

    /* ── Ping-echo loop ───────────────────────────────────────────────── */

    uint64_t seq       = 0;
    uint64_t next_wake = get_time_ns() + SENDER_PERIOD_NS;

    while (seq < (uint64_t)n_probes) {

        /* Absolute-time sleep. */
        uint64_t now = get_time_ns();
        if (now < next_wake) {
            struct timespec ts = ns_to_timespec(next_wake - now);
            nanosleep(&ts, NULL);
        } else {
            next_wake = now; /* drop backlog */
        }

        /* ── Send probe ───────────────────────────────────────────────── */

        memset(msg.payload, 0xAB, sizeof(msg.payload));
        msg.seq          = seq;
        msg.send_time_ns = get_time_ns();   /* vm-hi clock */

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

        /* ── Receive echo ─────────────────────────────────────────────── */

        ssize_t nb;
        if (tcp_mode)
            nb = recv_full(sock, &echo);
        else
            nb = recv(sock, &echo, sizeof(echo), 0);

        uint64_t recv_time_ns = get_time_ns();  /* vm-hi clock */

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

        /* ── Record RTL and IAJ ───────────────────────────────────────── */

        uint64_t rtl = recv_time_ns - echo.send_time_ns;

        if (prev_recv_ns != 0 && !last_was_loss && n_iaj < MAX_SAMPLES) {
            uint64_t iat = recv_time_ns - prev_recv_ns;
            int64_t  dev = (int64_t)iat - (int64_t)SENDER_PERIOD_NS;
            iaj_ns[n_iaj++] = (uint64_t)(dev < 0 ? -dev : dev);
        }

        if (n_stored < MAX_SAMPLES)
            rtl_ns[n_stored++] = rtl;

        prev_recv_ns  = recv_time_ns;
        last_was_loss = false;

        if ((seq + 1) % 1000 == 0)
            printf("Progress: %llu/%d  (lost: %llu)\n",
                   (unsigned long long)(seq + 1), n_probes,
                   (unsigned long long)n_lost);

        seq++;
        next_wake += SENDER_PERIOD_NS;
    }

    /* TCP: close connection to signal end-of-run to echo server. */
    close(sock);

    /* ── Print results ────────────────────────────────────────────────── */

    uint64_t n_sent = (uint64_t)n_probes;

    printf("\n========== HI_NET_PING RESULTS ==========\n");
    printf("Probes sent:       %5llu\n", (unsigned long long)n_sent);
    printf("Echoes received:   %5zu\n",  n_stored);
    printf("Lost (no echo):    %5llu (%.3f%%)\n",
           (unsigned long long)n_lost,
           n_sent > 0 ? (double)n_lost / (double)n_sent * 100.0 : 0.0);

    print_metric("RTL", rtl_ns, n_stored);
    print_metric("IAJ", iaj_ns, n_iaj);

    printf("=========================================\n");
    printf("HI_NET_PING_DONE run=%d\n\n", run_idx);
}

/* ── Main ────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int n_default = DEFAULT_N_PROBES;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc)
            n_default = atoi(argv[++i]);
        else if (strcmp(argv[i], "-h") == 0) {
            printf("Usage: hi_net_ping [-n <default_probes>]\n");
            printf("  -n <count>  Default probes per run (default: %d, max: %d)\n",
                   DEFAULT_N_PROBES, MAX_SAMPLES);
            printf("  (probe count can be overridden per-run by net_trigger)\n");
            return 0;
        }
    }
    if (n_default <= 0 || n_default > MAX_SAMPLES)
        n_default = DEFAULT_N_PROBES;

    setbuf(stdout, NULL);

    printf("===================================================\n");
    printf("hi_net_ping — Scenario 3 hicom variant\n");
    printf("EB corbos Linux Network Determinism (HI-side)\n");
    printf("===================================================\n");
    printf("Default probes:  %d\n", n_default);
    printf("Message size:    %zu B\n", sizeof(struct net_message));
    printf("Probe period:    %llu ms\n",
           (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Echo timeout:    %d ms\n", RECV_TIMEOUT_MS);
    printf("Trigger port:    %s:%d\n", NET_HI_IP, NET_TRIGGER_PORT);
    printf("Echo port:       %s:%d\n", NET_ECHO_IP, NET_ECHO_PORT);
    printf("\n");

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("hi_net_ping: mlockall: %s — continuing\n", strerror(errno));

    /* ── Spawn hi_net_echo child ─────────────────────────────────────── */

    spawn_echo_child();
    printf("hi_net_ping: hi_net_echo child spawned.\n");

    /* Give the child a moment to bind its socket and enter its loop. */
    sleep(1);

    /* ── Create trigger socket ───────────────────────────────────────── */

    int trig_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (trig_sock < 0) {
        printf("hi_net_ping: ERROR: trigger socket: %s\n", strerror(errno));
        goto idle;
    }

    int reuse = 1;
    setsockopt(trig_sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in trig_baddr = {0};
    trig_baddr.sin_family      = AF_INET;
    trig_baddr.sin_port        = htons(NET_TRIGGER_PORT);
    trig_baddr.sin_addr.s_addr = inet_addr(NET_HI_IP);

    if (bind(trig_sock, (struct sockaddr *)&trig_baddr, sizeof(trig_baddr)) != 0) {
        printf("hi_net_ping: ERROR: bind trigger port (%s:%d): %s\n",
               NET_HI_IP, NET_TRIGGER_PORT, strerror(errno));
        close(trig_sock);
        goto idle;
    }

    /* Heartbeat timeout while waiting for trigger. */
    struct timeval tv_trig = { .tv_sec = 5, .tv_usec = 0 };
    setsockopt(trig_sock, SOL_SOCKET, SO_RCVTIMEO, &tv_trig, sizeof(tv_trig));

    printf("\nHI_NET_PING_READY\n\n");
    printf("Waiting for trigger from net_trigger (vm-li) on %s:%d...\n",
           NET_HI_IP, NET_TRIGGER_PORT);

    /* ── Trigger-and-run loop ────────────────────────────────────────── */

    int run_index = 0;
    static struct net_message trig_msg;
    static struct net_message trig_ack;

    while (true) {
        struct sockaddr_in li_addr = {0};
        socklen_t li_len = sizeof(li_addr);

        ssize_t nb = recvfrom(trig_sock, &trig_msg, sizeof(trig_msg), 0,
                              (struct sockaddr *)&li_addr, &li_len);

        if (nb < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                continue;  /* timeout — keep waiting */
            printf("hi_net_ping: ERROR: trigger recvfrom: %s\n", strerror(errno));
            continue;
        }

        if (nb != (ssize_t)sizeof(trig_msg))          continue;
        if (trig_msg.seq != NET_CTRL_SEQ_TRIGGER)     continue;

        /* Extract protocol and n_probes from trigger. */
        bool     tcp_mode = (trig_msg.payload[0] != 0);
        uint64_t n_probes = 0;
        memcpy(&n_probes, trig_msg.payload + 1, sizeof(uint64_t));
        if (n_probes == 0 || n_probes > MAX_SAMPLES)
            n_probes = (uint64_t)n_default;

        printf("hi_net_ping: TRIGGER received — protocol=%s  n_probes=%llu\n",
               tcp_mode ? "TCP" : "UDP", (unsigned long long)n_probes);

        /* Reply with TRIGGER_ACK. */
        memset(&trig_ack, 0, sizeof(trig_ack));
        trig_ack.seq        = NET_CTRL_SEQ_TRIGGER_ACK;
        trig_ack.payload[0] = tcp_mode ? 1 : 0;

        if (sendto(trig_sock, &trig_ack, sizeof(trig_ack), 0,
                   (struct sockaddr *)&li_addr, li_len) < 0) {
            printf("hi_net_ping: WARNING: TRIGGER_ACK send: %s\n",
                   strerror(errno));
        } else {
            printf("hi_net_ping: TRIGGER_ACK sent\n");
        }

        /* ── Run the experiment ──────────────────────────────────────── */

        run_index++;
        run_pings(tcp_mode, (int)n_probes, run_index);

        printf("Waiting for next trigger...\n");
    }

idle:
    /* PID 1 must never return — the kernel would panic. */
    if (getpid() == 1) {
        printf("hi_net_ping: PID 1 — entering idle loop\n");
        while (1) sleep(3600);
    }
    return 1;
}
