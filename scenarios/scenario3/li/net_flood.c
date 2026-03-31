#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define DEFAULT_DEST_IP   "10.0.1.1"
#define DEFAULT_DEST_PORT 5001
#define DEFAULT_MBPS      64
#define DEFAULT_PKT_BYTES 1400  /* near Ethernet MTU for maximum bandwidth pressure */

static volatile sig_atomic_t g_running = 1;

static void sig_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

static inline uint64_t get_time_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

int main(int argc, char *argv[])
{
    const char *dest_ip   = DEFAULT_DEST_IP;
    int         dest_port = DEFAULT_DEST_PORT;
    int         mbps      = DEFAULT_MBPS;
    int         pkt_bytes = DEFAULT_PKT_BYTES;
    int         timeout_s = 0;  /* 0 = run until SIGTERM */

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-d") == 0 && i + 1 < argc)
            dest_ip   = argv[++i];
        else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
            dest_port = atoi(argv[++i]);
        else if (strcmp(argv[i], "-b") == 0 && i + 1 < argc)
            mbps      = atoi(argv[++i]);
        else if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
            pkt_bytes = atoi(argv[++i]);
        else if (strcmp(argv[i], "-t") == 0 && i + 1 < argc)
            timeout_s = atoi(argv[++i]);
        else if (strcmp(argv[i], "-h") == 0 ||
                 strcmp(argv[i], "--help") == 0) {
            printf("Usage: net_flood [options]\n");
            printf("  -d <ip>     Destination IP   (default: %s)\n",  DEFAULT_DEST_IP);
            printf("  -p <port>   Destination port (default: %d)\n",  DEFAULT_DEST_PORT);
            printf("  -b <Mbps>   Target bandwidth (default: %d)\n",  DEFAULT_MBPS);
            printf("  -s <bytes>  Packet size      (default: %d)\n",  DEFAULT_PKT_BYTES);
            printf("  -t <sec>    Timeout, 0=until SIGTERM (default: 0)\n");
            printf("  -h          Show this help\n");
            return 0;
        }
    }

    if (pkt_bytes < 8 || pkt_bytes > 65507) {
        fprintf(stderr, "ERROR: -s must be between 8 and 65507\n");
        return 1;
    }

    signal(SIGTERM, sig_handler);
    signal(SIGINT,  sig_handler);

    /* ── Socket setup ───────────────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    /* Large send buffer to sustain burst without ENOBUFS. */
    int sndbuf = 1 << 20; /* 1 MB */
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    struct sockaddr_in dst = {0};
    dst.sin_family      = AF_INET;
    dst.sin_port        = htons((uint16_t)dest_port);
    dst.sin_addr.s_addr = inet_addr(dest_ip);

    if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
        perror("connect");
        return 1;
    }

    /* Pre-fill a fixed payload buffer (0xCD pattern). */
    static uint8_t pkt_buf[65536];
    memset(pkt_buf, 0xCD, (size_t)pkt_bytes);

    /* ── Rate control ───────────────────────────────────────────────────── */
    /* Inter-packet sleep interval (ns) to achieve the target bit rate:
     *   sleep_ns = (pkt_bytes * 8 bits) / (mbps * 1e6 bits/s) * 1e9 ns/s
     *            = pkt_bytes * 8000 / mbps  ns
     * When mbps == 0 we send as fast as possible (stress test mode). */
    uint64_t sleep_ns = 0;
    if (mbps > 0)
        sleep_ns = ((uint64_t)pkt_bytes * 8000ULL) / (uint64_t)mbps;

    printf("net_flood: %s:%d  rate=%d Mbps  pkt=%d B  sleep=%llu ns\n",
           dest_ip, dest_port, mbps, pkt_bytes, (unsigned long long)sleep_ns);

    /* ── Send loop ──────────────────────────────────────────────────────── */

    uint64_t total_bytes = 0;
    uint64_t total_pkts  = 0;
    uint64_t start_ns    = get_time_ns();
    uint64_t end_ns      = timeout_s > 0
                           ? start_ns + (uint64_t)timeout_s * 1000000000ULL
                           : UINT64_MAX;

    while (g_running && get_time_ns() < end_ns) {
        ssize_t n = send(sock, pkt_buf, (size_t)pkt_bytes, MSG_DONTWAIT);
        if (n > 0) {
            total_bytes += (uint64_t)n;
            total_pkts++;
        }
        /* EAGAIN/ENOBUFS: send buffer full — back off briefly then retry. */

        if (sleep_ns > 0) {
            struct timespec ts = {
                .tv_sec  = (time_t)(sleep_ns / 1000000000ULL),
                .tv_nsec = (long)(sleep_ns % 1000000000ULL),
            };
            nanosleep(&ts, NULL);
        }
    }

    /* ── Summary ────────────────────────────────────────────────────────── */

    double elapsed  = (double)(get_time_ns() - start_ns) / 1e9;
    double eff_mbps = (elapsed > 0.0)
                      ? (double)total_bytes * 8.0 / elapsed / 1e6
                      : 0.0;

    printf("net_flood: done — %llu pkts / %.2f MB / %.3f s → %.1f Mbps effective\n",
           (unsigned long long)total_pkts,
           (double)total_bytes / 1e6,
           elapsed,
           eff_mbps);

    close(sock);
    return 0;
}
