#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "common.h"

static volatile int g_running = 1;

static void sigint_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

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

/* ── RT configuration ───────────────────────────────────────────────────── */

static void configure_realtime(void)
{
    struct sched_param param;
    param.sched_priority = 90;
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        /* Expected on the hi VM safety kernel (returns ENOSYS).
         * Logged for the thesis record; execution continues as SCHED_OTHER. */
        printf("net_sender: RT scheduling: FAILED (%s) — running as SCHED_OTHER\n",
               strerror(errno));
    } else {
        printf("net_sender: RT scheduling: OK (SCHED_FIFO priority 90)\n");
    }
}

/* ── Main ───────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    /* Disable stdout buffering: musl performs an ioctl on writes to a
     * buffered stdout which is blocked by the safety kernel. */
    setbuf(stdout, NULL);

    printf("========================================\n");
    printf("net_sender — Scenario 3\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Source:  %s (bind)\n",       NET_HI_IP);
    printf("Target:  %s:%d\n",           NET_LI_IP, NET_SENDER_PORT);
    printf("Period:  %llu ms\n",         (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Payload: %d B  (msg: %zu B)\n\n", NET_PAYLOAD_BYTES, sizeof(struct net_message));

    /* Lock memory to prevent page faults in the hot send path.
     * May fail with ENOSYS on the safety kernel — non-fatal. */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        printf("net_sender: mlockall: %s — continuing\n", strerror(errno));
    }

    configure_realtime();
    signal(SIGINT, sigint_handler);

    /* ── Create and configure UDP socket ─────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        printf("net_sender: ERROR: socket() failed: %s\n", strerror(errno));
        goto idle;
    }

    /* Larger send buffer reduces the probability of ENOBUFS under load. */
    int sndbuf = 262144; /* 256 KB */
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    /* Bind to our own address so outgoing packets are routed through the
     * vnet_li_hi interface rather than any other interface that may appear. */
    struct sockaddr_in src = {0};
    src.sin_family      = AF_INET;
    src.sin_port        = 0; /* kernel assigns ephemeral source port */
    src.sin_addr.s_addr = inet_addr(NET_HI_IP);
    if (bind(sock, (struct sockaddr *)&src, sizeof(src)) != 0) {
        /* Non-fatal: routing will still work once the interface is up. */
        printf("net_sender: WARNING: bind(%s) failed: %s\n",
               NET_HI_IP, strerror(errno));
    }

    /* connect() records the destination so we can use send() in the hot path. */
    struct sockaddr_in dst = {0};
    dst.sin_family      = AF_INET;
    dst.sin_port        = htons(NET_SENDER_PORT);
    dst.sin_addr.s_addr = inet_addr(NET_LI_IP);
    if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
        printf("net_sender: ERROR: connect(%s:%d) failed: %s\n",
               NET_LI_IP, NET_SENDER_PORT, strerror(errno));
        goto idle;
    }

    printf("net_sender: ready — starting 1 ms send loop\n\n");

    /* ── Periodic send loop ───────────────────────────────────────────── */

    static struct net_message msg;
    memset(msg.payload, 0xAB, sizeof(msg.payload));

    uint64_t seq        = 0;
    uint64_t start_ns   = get_time_ns();
    uint64_t next_wake  = start_ns + SENDER_PERIOD_NS;

    while (g_running) {
        /* Absolute-time sleep to the next period boundary. */
        struct timespec wake_ts = ns_to_timespec(next_wake);
        nanosleep(&wake_ts, NULL);

        /* Stamp send time as late as possible before the kernel call. */
        msg.seq          = seq;
        msg.send_time_ns = get_time_ns();

        ssize_t n = send(sock, &msg, sizeof(msg), 0);
        if (n < 0 && errno != ENOBUFS && errno != EAGAIN) {
            printf("net_sender: WARNING: send() seq=%llu: %s\n",
                   (unsigned long long)seq, strerror(errno));
        }

        seq++;
        next_wake += SENDER_PERIOD_NS;

        /* Progress line every 10 000 packets (~10 s). */
        if (seq % 10000 == 0) {
            double elapsed = (double)(get_time_ns() - start_ns) / 1e9;
            printf("net_sender: [%llu pkts sent]  elapsed=%.3f s\n",
                   (unsigned long long)seq, elapsed);
        }
    }

    printf("net_sender: loop terminated — total sent: %llu\n",
           (unsigned long long)seq);

idle:
    /* As PID 1 the kernel panics if we exit — stay in an idle loop. */
    if (getpid() == 1) {
        printf("net_sender: running as init (PID 1) — entering idle loop\n");
        while (1) {
            sleep(3600);
        }
    }

    return 0;
}
