/*
 * net_receiver_hi.c — Scenario 3 reversed variant: echo server (vm-hi)
 *
 * Runs as PID 1 on vm-hi.  Implements a ping-echo server:
 *   - Waits for HELLO from the pinger on vm-li.
 *   - Negotiates UDP/TCP transport via the handshake.
 *   - Echoes every received probe back to vm-li unchanged.
 *
 * vm-li measures Round-Trip Latency (RTL) on its own clock.
 * No OWL computation or clock calibration is performed here.
 *
 * Run lifecycle (supports multiple runs per QEMU boot):
 *   1. Create UDP socket bound to NET_HI_IP:NET_SENDER_PORT.
 *   2. Wait for HELLO.  Extract n_probes and protocol.
 *   3. Reply with HELLO_ACK.
 *   4. If TCP: accept one connection from pinger.
 *   5. Echo loop: recv probe → send it back unchanged.
 *      Break on silence (UDP) or connection close (TCP).
 *   6. Close socket, loop to step 1.
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
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <sys/mman.h>

#include "common.h"

static volatile int g_running = 1;
static void sigint_handler(int sig) { (void)sig; g_running = 0; }

/* ── TCP helper ──────────────────────────────────────────────────────────── */

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

/* ── Main ─────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    (void)argc; (void)argv;

    /* Disable stdout buffering: required on the hi VM safety kernel. */
    setbuf(stdout, NULL);

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("net_receiver_hi: mlockall: %s — continuing\n", strerror(errno));

    signal(SIGINT, sigint_handler);

    printf("========================================\n");
    printf("net_receiver_hi (echo server) — Scenario 3 reversed\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("RTL mode: echoes probes from vm-li unchanged\n");
    printf("Msg size: %zu B\n\n", sizeof(struct net_message));

    static struct net_message msg;
    static struct net_message ack;
    int sock = -1;
    int run_count = 0;

    while (g_running) {

        if (sock >= 0) { close(sock); sock = -1; }

        sock = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock < 0) {
            printf("net_receiver_hi: ERROR: socket: %s\n", strerror(errno));
            sleep(1); continue;
        }

        int reuse = 1;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        int rcvbuf = 1 << 20;
        setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

        struct sockaddr_in baddr = {0};
        baddr.sin_family      = AF_INET;
        baddr.sin_port        = htons(NET_SENDER_PORT);
        baddr.sin_addr.s_addr = inet_addr(NET_HI_IP);

        if (bind(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) {
            printf("net_receiver_hi: ERROR: bind(%s:%d): %s\n",
                   NET_HI_IP, NET_SENDER_PORT, strerror(errno));
            sleep(1); continue;
        }

        /* Heartbeat timeout while waiting for HELLO. */
        struct timeval tv_wait = { .tv_sec = 5, .tv_usec = 0 };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv_wait, sizeof(tv_wait));

        printf("\nnet_receiver_hi: waiting for HELLO from pinger (vm-li)...\n");

        struct sockaddr_in li_addr = {0};
        socklen_t li_len   = sizeof(li_addr);
        uint64_t  n_probes = 5000;
        bool      tcp_mode = false;
        bool      got_hello = false;

        /* ── Wait for HELLO ────────────────────────────────────────────────── */

        while (g_running && !got_hello) {
            ssize_t nb = recvfrom(sock, &msg, sizeof(msg), 0,
                                  (struct sockaddr *)&li_addr, &li_len);
            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    printf("net_receiver_hi: still waiting for HELLO...\n");
                    continue;
                }
                printf("net_receiver_hi: ERROR: recvfrom: %s\n", strerror(errno));
                break;
            }
            if (nb != (ssize_t)sizeof(msg))    continue;
            if (msg.seq != NET_CTRL_SEQ_HELLO) continue;

            tcp_mode = (msg.payload[0] != 0);
            memcpy(&n_probes, msg.payload + 1, sizeof(uint64_t));
            if (n_probes == 0 || n_probes > 100000) n_probes = 5000;

            printf("net_receiver_hi: HELLO — protocol=%s  n_probes=%llu\n",
                   tcp_mode ? "TCP" : "UDP", (unsigned long long)n_probes);

            memset(&ack, 0, sizeof(ack));
            ack.seq        = NET_CTRL_SEQ_HELLO_ACK;
            ack.payload[0] = tcp_mode ? 1 : 0;

            if (sendto(sock, &ack, sizeof(ack), 0,
                       (struct sockaddr *)&li_addr, li_len) < 0) {
                printf("net_receiver_hi: WARNING: HELLO_ACK sendto: %s\n",
                       strerror(errno));
            } else {
                printf("net_receiver_hi: HELLO_ACK sent\n");
            }
            got_hello = true;
        }

        if (!g_running || !got_hello) { close(sock); sock = -1; break; }

        /* ── Switch to TCP or prepare UDP echo socket ──────────────────────── */

        int data_sock;
        if (tcp_mode) {
            close(sock); sock = -1;
            int lsock = socket(AF_INET, SOCK_STREAM, 0);
            if (lsock < 0) {
                printf("net_receiver_hi: ERROR: TCP socket: %s\n", strerror(errno));
                break;
            }
            setsockopt(lsock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
            if (bind(lsock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0 ||
                listen(lsock, 1) != 0) {
                printf("net_receiver_hi: ERROR: TCP bind/listen: %s\n", strerror(errno));
                close(lsock);
                break;
            }
            printf("net_receiver_hi: TCP listening — waiting for pinger...\n");
            struct sockaddr_in cli = {0};
            socklen_t cli_len = sizeof(cli);
            data_sock = accept(lsock, (struct sockaddr *)&cli, &cli_len);
            close(lsock);
            if (data_sock < 0) {
                printf("net_receiver_hi: ERROR: TCP accept: %s\n", strerror(errno));
                break;
            }
            int nodelay = 1;
            setsockopt(data_sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
            printf("net_receiver_hi: TCP connection accepted\n");
        } else {
            /* UDP: connect() to pinger so send() works without recvfrom overhead. */
            struct sockaddr_in dst = li_addr;
            dst.sin_port = li_addr.sin_port; /* keep pinger's source port */
            if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
                printf("net_receiver_hi: ERROR: UDP connect to pinger: %s\n",
                       strerror(errno));
                continue;
            }
            data_sock = sock;
        }

        /* Echo-phase recv timeout: 3 s — detect end-of-run via silence. */
        {
            struct timeval tv = { .tv_sec = 3, .tv_usec = 0 };
            setsockopt(data_sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        }

        run_count++;
        printf("net_receiver_hi: run #%d — echoing probes\n", run_count);

        /* ── Echo loop ──────────────────────────────────────────────────────────── */

        uint64_t echoed = 0;

        while (g_running) {
            ssize_t nb;

            if (tcp_mode) {
                nb = recv_full(data_sock, &msg);
                if (nb == 0) {
                    printf("net_receiver_hi: TCP closed by pinger (%llu echoed)\n",
                           (unsigned long long)echoed);
                    break;
                }
            } else {
                nb = recv(data_sock, &msg, sizeof(msg), 0);
            }

            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    printf("net_receiver_hi: run #%d — silence, run ended "
                           "(%llu echoed)\n",
                           run_count, (unsigned long long)echoed);
                    break;
                }
                printf("net_receiver_hi: ERROR: recv: %s\n", strerror(errno));
                break;
            }

            if (nb != (ssize_t)sizeof(msg))        continue;
            if (msg.seq >= NET_CTRL_SEQ_HELLO_ACK) continue;

            /* Echo back immediately — message is NOT modified. */
            if (send(data_sock, &msg, sizeof(msg), 0) < 0) {
                printf("net_receiver_hi: WARNING: echo send seq=%llu: %s\n",
                       (unsigned long long)msg.seq, strerror(errno));
            }
            echoed++;
        }

        printf("net_receiver_hi: run #%d complete — %llu probes echoed\n",
               run_count, (unsigned long long)echoed);

        if (tcp_mode) { close(data_sock); }
        else          { close(sock); sock = -1; }

        if (g_running)
            printf("net_receiver_hi: returning to wait state...\n");
    }

    if (sock >= 0) close(sock);
    printf("net_receiver_hi: terminated\n");

    if (getpid() == 1) {
        printf("net_receiver_hi: PID 1 — entering idle loop\n");
        while (1) sleep(3600);
    }
    return 0;
}
