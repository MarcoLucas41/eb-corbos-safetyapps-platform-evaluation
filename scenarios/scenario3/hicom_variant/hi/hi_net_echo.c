/*
 * hi_net_echo.c — Echo server child process (Scenario 3, hicom variant)
 *
 * Spawned by hi_net_ping via clone/execve on vm-hi.  Listens on
 * NET_ECHO_IP:NET_ECHO_PORT (127.0.0.1:5002) for probes from hi_net_ping
 * and echoes them back unchanged.  hi_net_ping measures RTL on its own
 * clock — no measurement logic here.
 *
 * Run lifecycle (supports multiple runs without restart):
 *   1. Create UDP socket bound to NET_ECHO_IP:NET_ECHO_PORT.
 *   2. Wait for HELLO from hi_net_ping.  Extract n_probes and protocol.
 *   3. Reply with HELLO_ACK.
 *   4. If TCP: accept one TCP connection from hi_net_ping.
 *   5. Echo loop: recv probe → send it back unchanged.
 *      Break on silence (UDP) or connection close (TCP).
 *   6. Close data socket, restart from step 1.
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
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

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
    (void)argc;
    (void)argv;

    setbuf(stdout, NULL);

    printf("[echo] ========================================\n");
    printf("[echo] hi_net_echo — Scenario 3 hicom variant\n");
    printf("[echo] Echo server on %s:%d\n", NET_ECHO_IP, NET_ECHO_PORT);
    printf("[echo] Msg size: %zu B\n\n", sizeof(struct net_message));

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        printf("[echo] mlockall: %s — continuing\n", strerror(errno));

    signal(SIGINT, sigint_handler);

    static struct net_message msg;
    static struct net_message ack;
    int run_count = 0;

    /* ── Per-run loop ──────────────────────────────────────────────── */

    while (g_running) {

        int sock = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock < 0) {
            printf("[echo] ERROR: socket: %s\n", strerror(errno));
            sleep(1);
            continue;
        }

        int reuse = 1;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        int rcvbuf = 1 << 20;
        setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

        struct sockaddr_in baddr = {0};
        baddr.sin_family      = AF_INET;
        baddr.sin_port        = htons(NET_ECHO_PORT);
        baddr.sin_addr.s_addr = inet_addr(NET_ECHO_IP);

        if (bind(sock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0) {
            printf("[echo] ERROR: bind(%s:%d): %s\n",
                   NET_ECHO_IP, NET_ECHO_PORT, strerror(errno));
            close(sock);
            sleep(1);
            continue;
        }

        /* Heartbeat timeout while waiting for HELLO. */
        struct timeval tv_wait = { .tv_sec = 5, .tv_usec = 0 };
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv_wait, sizeof(tv_wait));

        printf("[echo] waiting for HELLO from hi_net_ping...\n");

        struct sockaddr_in ping_addr = {0};
        socklen_t ping_len   = sizeof(ping_addr);
        uint64_t  n_probes   = 5000;
        bool      tcp_mode   = false;
        bool      got_hello  = false;

        /* ── Wait for HELLO ────────────────────────────────────────────── */

        while (g_running && !got_hello) {
            ssize_t nb = recvfrom(sock, &msg, sizeof(msg), 0,
                                  (struct sockaddr *)&ping_addr, &ping_len);
            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                printf("[echo] ERROR: recvfrom: %s\n", strerror(errno));
                break;
            }
            if (nb != (ssize_t)sizeof(msg))      continue;
            if (msg.seq != NET_CTRL_SEQ_HELLO)   continue;

            tcp_mode = (msg.payload[0] != 0);
            memcpy(&n_probes, msg.payload + 1, sizeof(uint64_t));
            if (n_probes == 0 || n_probes > 100000)
                n_probes = 5000;

            printf("[echo] HELLO — protocol=%s  n_probes=%llu\n",
                   tcp_mode ? "TCP" : "UDP", (unsigned long long)n_probes);

            memset(&ack, 0, sizeof(ack));
            ack.seq        = NET_CTRL_SEQ_HELLO_ACK;
            ack.payload[0] = tcp_mode ? 1 : 0;

            if (sendto(sock, &ack, sizeof(ack), 0,
                       (struct sockaddr *)&ping_addr, ping_len) < 0) {
                printf("[echo] WARNING: HELLO_ACK sendto: %s\n",
                       strerror(errno));
            } else {
                printf("[echo] HELLO_ACK sent\n");
            }
            got_hello = true;
        }

        if (!g_running || !got_hello) { close(sock); break; }

        /* ── Switch to TCP or prepare UDP echo ─────────────────────────── */

        int data_sock;
        if (tcp_mode) {
            close(sock);
            int lsock = socket(AF_INET, SOCK_STREAM, 0);
            if (lsock < 0) {
                printf("[echo] ERROR: TCP socket: %s\n", strerror(errno));
                continue;
            }
            setsockopt(lsock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
            if (bind(lsock, (struct sockaddr *)&baddr, sizeof(baddr)) != 0 ||
                listen(lsock, 1) != 0) {
                printf("[echo] ERROR: TCP bind/listen: %s\n", strerror(errno));
                close(lsock);
                continue;
            }
            printf("[echo] TCP listening — waiting for hi_net_ping...\n");
            struct sockaddr_in cli = {0};
            socklen_t cli_len = sizeof(cli);
            data_sock = accept(lsock, (struct sockaddr *)&cli, &cli_len);
            close(lsock);
            if (data_sock < 0) {
                printf("[echo] ERROR: TCP accept: %s\n", strerror(errno));
                continue;
            }
            int nodelay = 1;
            setsockopt(data_sock, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
            printf("[echo] TCP connection accepted (TCP_NODELAY set)\n");
        } else {
            /* UDP: stay unconnected and reply to the sender of each probe.
             * hi_net_ping uses a separate HELLO socket and a separate data
             * socket, so connecting to the HELLO source port would send echoes
             * to the wrong endpoint. */
            data_sock = sock;
        }

        /* Echo-phase recv timeout: 3 s — detect end-of-run via silence. */
        {
            struct timeval tv = { .tv_sec = 3, .tv_usec = 0 };
            setsockopt(data_sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        }

        run_count++;
        printf("[echo] run #%d — echoing probes\n", run_count);

        /* ── Echo loop ──────────────────────────────────────────────────── */

        uint64_t echoed = 0;

        while (g_running) {
            ssize_t nb;
            struct sockaddr_in peer_addr = {0};
            socklen_t peer_len = sizeof(peer_addr);

            if (tcp_mode) {
                nb = recv_full(data_sock, &msg);
                if (nb == 0) {
                    printf("[echo] TCP closed by pinger (%llu echoed)\n",
                           (unsigned long long)echoed);
                    break;
                }
            } else {
                nb = recvfrom(data_sock, &msg, sizeof(msg), 0,
                              (struct sockaddr *)&peer_addr, &peer_len);
            }

            if (nb < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    printf("[echo] run #%d — silence, run ended (%llu echoed)\n",
                           run_count, (unsigned long long)echoed);
                    break;
                }
                printf("[echo] ERROR: recv: %s\n", strerror(errno));
                break;
            }

            if (nb != (ssize_t)sizeof(msg))        continue;
            if (msg.seq >= NET_CTRL_SEQ_HELLO_ACK) continue; /* skip ctrl pkts */

            /* Echo back immediately — message is NOT modified. */
            if (tcp_mode) {
                if (send(data_sock, &msg, sizeof(msg), 0) < 0) {
                    printf("[echo] WARNING: echo send seq=%llu: %s\n",
                           (unsigned long long)msg.seq, strerror(errno));
                }
            } else if (sendto(data_sock, &msg, sizeof(msg), 0,
                               (struct sockaddr *)&peer_addr, peer_len) < 0) {
                printf("[echo] WARNING: echo send seq=%llu: %s\n",
                       (unsigned long long)msg.seq, strerror(errno));
            }
            echoed++;
        }

        printf("[echo] run #%d complete — %llu probes echoed\n",
               run_count, (unsigned long long)echoed);
        close(data_sock);

        if (g_running)
            printf("[echo] returning to wait state...\n");
    }

    printf("[echo] terminated\n");

    /* Child process — never exit while parent (PID 1) is still running. */
    while (1) sleep(3600);
    return 0;
}
