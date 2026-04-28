/*
 * net_sender_tcp.c — Scenario 3 (TCP variant)
 *
 * Periodic TCP sender running as PID 1 on vm-hi.
 * Acts as a TCP *server* because it starts before vm-li is ready.
 *
 * Protocol per run:
 *   1. bind/listen on NET_HI_IP:NET_SENDER_PORT
 *   2. accept() one connection from net_receiver_tcp on vm-li
 *   3. set TCP_NODELAY on the accepted socket to disable Nagle buffering —
 *      essential for preserving the 1 ms send cadence
 *   4. send net_message structs at SENDER_PERIOD_NS intervals until the
 *      client closes the connection (recv returns 0) or send() fails
 *   5. close the client socket, reset seq to 0, return to accept()
 *
 * Multiple runs per QEMU boot are supported: net_receiver_tcp disconnects
 * after collecting its target packet count, and net_sender_tcp re-accepts
 * the next connection automatically.
 *
 * Key differences from the UDP version (net_sender.c):
 *   - SOCK_STREAM instead of SOCK_DGRAM
 *   - TCP_NODELAY required to suppress Nagle algorithm
 *   - Server role (bind/listen/accept) instead of client role (connect)
 *   - seq resets to 0 for each new connection (= each measurement run)
 *   - send_exact() loop ensures all bytes are written even if send() is
 *     short (rare on loopback/virtio but required for correctness)
 */

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
#include <netinet/tcp.h>
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
        printf("net_sender_tcp: RT scheduling: FAILED (%s) — running as SCHED_OTHER\n",
               strerror(errno));
    } else {
        printf("net_sender_tcp: RT scheduling: OK (SCHED_FIFO priority 90)\n");
    }
}

/* ── Reliable send helper ───────────────────────────────────────────────── */
/*
 * TCP send() may write fewer bytes than requested (especially under memory
 * pressure). Loop until all bytes are sent or an error occurs.
 * Returns 0 on success, -1 on error/connection closed.
 */
static int send_exact(int fd, const void *buf, size_t len)
{
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, (const char *)buf + sent, len - sent, 0);
        if (n <= 0) return -1;  /* error or peer closed */
        sent += (size_t)n;
    }
    return 0;
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
    printf("net_sender_tcp — Scenario 3 (TCP)\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Listen:  %s:%d (TCP server)\n", NET_HI_IP, NET_SENDER_PORT);
    printf("Period:  %llu ms\n",
           (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Payload: %d B  (msg: %zu B)\n\n",
           NET_PAYLOAD_BYTES, sizeof(struct net_message));

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("net_sender_tcp: mlockall: %s — continuing\n", strerror(errno));

    configure_realtime();
    signal(SIGINT, sigint_handler);

    /* ── Create and bind the listening socket ─────────────────────────── */

    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        printf("net_sender_tcp: ERROR: socket() failed: %s\n", strerror(errno));
        goto idle;
    }
    printf("net_sender_tcp: socket created (fd=%d)\n", listen_fd);
    /* SO_REUSEADDR avoids "Address already in use" if the process restarts. */
    int reuse = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in srv = {0};
    srv.sin_family      = AF_INET;
    srv.sin_port        = htons(NET_SENDER_PORT);
    srv.sin_addr.s_addr = inet_addr(NET_HI_IP);

    if (bind(listen_fd, (struct sockaddr *)&srv, sizeof(srv)) != 0) {
        printf("net_sender_tcp: ERROR: bind(%s:%d) failed: %s\n",
               NET_HI_IP, NET_SENDER_PORT, strerror(errno));
        goto idle;
    }
    printf("net_sender_tcp: socket bound to %s:%d\n", NET_HI_IP, NET_SENDER_PORT);
    if (listen(listen_fd, 1) != 0) {
        printf("net_sender_tcp: ERROR: listen() failed: %s\n", strerror(errno));
        goto idle;
    }
    printf("net_sender_tcp: listening on %s:%d — waiting for net_receiver_tcp\n\n",
           NET_HI_IP, NET_SENDER_PORT);

    /* ── Accept loop: one connection = one measurement run ───────────── */

    int run_count = 0;

    while (g_running) {
        struct sockaddr_in cli = {0};
        socklen_t cli_len = sizeof(cli);

        int conn_fd = accept(listen_fd, (struct sockaddr *)&cli, &cli_len);
        if (conn_fd < 0) {
            if (errno == EINTR) continue;
            printf("net_sender_tcp: ERROR: accept() failed: %s\n",
                   strerror(errno));
            break;
        }
        run_count++;
        printf("net_sender_tcp: run #%d — client connected from %s\n",
               run_count, inet_ntoa(cli.sin_addr));

        /* Disable Nagle: without this the kernel batches small messages,
         * destroying the 1 ms send cadence. */
        int nodelay = 1;
        if (setsockopt(conn_fd, IPPROTO_TCP, TCP_NODELAY,
                       &nodelay, sizeof(nodelay)) != 0) {
            printf("net_sender_tcp: WARNING: TCP_NODELAY failed: %s\n",
                   strerror(errno));
        }

        /* ── Periodic send loop ──────────────────────────────────────── */

        static struct net_message msg;
        memset(msg.payload, 0xAB, sizeof(msg.payload));

        uint64_t seq       = 0;
        uint64_t start_ns  = get_time_ns();
        uint64_t next_wake = start_ns + SENDER_PERIOD_NS;

        while (g_running) {
            /* Relative sleep for the remaining time to the next period boundary.
             * nanosleep() is used instead of clock_nanosleep() because the hi VM
             * safety kernel restricts certain syscalls — nanosleep() is confirmed
             * to work (used by speedometer.c and shm_echo.c). */
            uint64_t now = get_time_ns();
            if (now < next_wake) {
                struct timespec sleep_ts = ns_to_timespec(next_wake - now);
                nanosleep(&sleep_ts, NULL);
            }

            /* Stamp send_time_ns as late as possible. */
            msg.seq          = seq;
            msg.send_time_ns = get_time_ns();

            if (send_exact(conn_fd, &msg, sizeof(msg)) != 0) {
                /* Client closed the connection or a network error occurred.
                 * This is normal end-of-run for net_receiver_tcp. */
                printf("net_sender_tcp: run #%d ended after %llu packets "
                       "(client disconnected or send error)\n",
                       run_count, (unsigned long long)seq);
                break;
            }

            seq++;
            next_wake += SENDER_PERIOD_NS;

            if (seq % 10000 == 0) {
                double elapsed = (double)(get_time_ns() - start_ns) / 1e9;
                printf("net_sender_tcp: run #%d [%llu pkts sent]  "
                       "elapsed=%.3f s\n",
                       run_count, (unsigned long long)seq, elapsed);
            }
        }

        close(conn_fd);
        printf("net_sender_tcp: run #%d complete — re-listening\n\n", run_count);
    }

    close(listen_fd);
    printf("net_sender_tcp: send loop terminated\n");

idle:
    if (getpid() == 1) {
        printf("net_sender_tcp: running as init (PID 1) — entering idle loop\n");
        while (1) sleep(3600);
    }
    return 0;
}
