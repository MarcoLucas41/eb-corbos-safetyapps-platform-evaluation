/*
 * net_sender_li.c — Scenario 3 reversed UDP direction
 *
 * Runs on vm-li and sends periodic UDP packets to net_receiver_hi on vm-hi
 * (li → hi direction).  This is the counterpart to the standard Scenario 3
 * setup where vm-hi sends to vm-li.
 *
 * Advantages of running the sender on vm-li:
 *   - SCHED_FIFO is available: sender jitter is lower than on vm-hi.
 *   - clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME) works correctly on
 *     the standard Linux kernel, giving better period accuracy.
 *   - No PID 1 constraints: the process exits cleanly after the run.
 *
 * Usage:
 *   net_sender_li [-d <seconds>] [-n <packets>] [-o <csv>]
 *
 * The sender embeds send_time_ns (CLOCK_MONOTONIC on vm-li) in each packet.
 * net_receiver_hi computes OWL = recv_time_hi - send_time_ns.  This is valid
 * in QEMU because both VMs share the same host ARM virtual counter and their
 * CLOCK_MONOTONIC values are effectively synchronised.
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
#include <sys/socket.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "common.h"

#define DEFAULT_DURATION_SEC 30
#define MAX_PACKETS_PER_RUN 10000

static volatile int g_running = 1;

static void sigint_handler(int sig) { (void)sig; g_running = 0; }

/* ── Timing ─────────────────────────────────────────────────────────────── */

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
    struct sched_param param = { .sched_priority = 90 };
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        printf("net_sender_li: SCHED_FIFO: FAILED (%s) — running as "
               "SCHED_OTHER\n", strerror(errno));
    } else {
        printf("net_sender_li: SCHED_FIFO: OK (priority 90)\n");
    }
}

/* ── Receiver readiness handshake ───────────────────────────────────────── */
static bool wait_for_receiver_ready(int sock, uint64_t target_packets)
{
    static struct net_message probe;
    static struct net_message reply;
    unsigned tries = 0;

    memset(&probe, 0, sizeof(probe));
    probe.seq = NET_CTRL_SEQ_HELLO;
    probe.send_time_ns = target_packets;

    printf("li_net_sender: waiting for receiver readiness on %s:%d ...\n",
           NET_LI_IP, NET_SENDER_PORT);

    while (g_running) {
        ssize_t n = send(sock, &probe, sizeof(probe), 0);
        if (n < 0 && errno != ENOBUFS && errno != EAGAIN) {
            printf("li_net_sender: WARNING: HELLO send failed: %s\n",
                   strerror(errno));
        }

        n = recv(sock, &reply, sizeof(reply), 0);
        if (n == (ssize_t)sizeof(reply) && reply.seq == NET_CTRL_SEQ_HELLO_ACK) {
            if (reply.send_time_ns != target_packets) {
                printf("li_net_sender: WARNING: ACK target mismatch (sent=%llu ack=%llu)\n",
                       (unsigned long long)target_packets,
                       (unsigned long long)reply.send_time_ns);
            }
            if (reply.send_time_ns == 0) {
                printf("li_net_sender: ERROR: receiver ACK did not include a valid target\n");
                return false;
            }

            printf("li_net_sender: receiver acknowledged readiness for %llu packets.\n",
                   (unsigned long long)target_packets);
            return true;
        }

        if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            printf("li_net_sender: WARNING: HELLO wait failed: %s\n", strerror(errno));
        }

        tries++;
        if (tries % 10 == 0) {
            printf("li_net_sender: still waiting for receiver... (%u probes)\n", tries);
        }
    }

    return false;
}

static void start_background_flood(pid_t *flood_pid, int flood_mbps)
{
    char bw_str[32];
    snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);

    printf("Starting background flood → %s:%d at %d Mbps\n\n",
           NET_HI_IP, NET_FLOOD_PORT, flood_mbps);

    *flood_pid = fork();
    if (*flood_pid == 0) {
        /* Child: exec net_flood (expected in PATH after install) */
        execlp("net_flood", "net_flood",
               "-d", NET_HI_IP,
               "-p", "5001",
               "-b", bw_str,
               NULL);
        perror("execlp net_flood");
        exit(1);
    } else if (*flood_pid < 0) {
        fprintf(stderr, "WARNING: fork for net_flood failed: %s — "
                "continuing without flood\n", strerror(errno));
    } else {
        /* Allow flood workers to ramp up before measurements start. */
        printf("Waiting 2 s for flood to ramp up...\n\n");
        sleep(2);
    }
}

/* ── Main ───────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    int         n_packets  = 10000;
    int         flood_mbps = 0;
    const char *csv_path     = "net_sender_li_log.csv";

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n_packets = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-B") == 0 && i + 1 < argc) {
            flood_mbps = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            csv_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
                 printf("  -n <count> Packets to send per run (default: 10000, max: %d)\n",
                     MAX_PACKETS_PER_RUN);
            printf("  -o <file>  Send-side log CSV (default: "
                   "net_sender_li_log.csv)\n");
            printf("  -B <Mbps>  Flood rate in Mbps (default: 0 = off)\n");
            printf("  -h         Show this help\n");
            printf("\nSends to %s:%d at %llu ms period for the configured "
                   "packet count.\n",
                   NET_HI_IP, NET_SENDER_PORT,
                   (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
            return 0;
        }
    }

    if (n_packets <= 0 || n_packets > MAX_PACKETS_PER_RUN) {
        fprintf(stderr, "ERROR: -n must be between 1 and %d\n", MAX_PACKETS_PER_RUN);
        return 1;
    }

    printf("========================================\n");
    printf("net_sender —Scenario 3\n");
    printf("EB corbos Linux Network Determinism\n");
    printf("========================================\n");
    printf("Source:  %s (bind)\n",       NET_LI_IP);
    printf("Target:  %s:%d\n",           NET_HI_IP, NET_SENDER_PORT);
    printf("Period:  %llu ms\n",         (unsigned long long)(SENDER_PERIOD_NS / 1000000ULL));
    printf("Payload: %d B  (msg: %zu B)\n\n", NET_PAYLOAD_BYTES, sizeof(struct net_message));

    printf("Packet target: %d\n", n_packets);
    printf("============================================\n\n");

    
    configure_realtime();
    signal(SIGINT, sigint_handler);

    /* ── Create UDP socket ──────────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "ERROR: socket: %s\n", strerror(errno));
        return 1;
    }

    /* Larger send buffer reduces the probability of ENOBUFS under load. */
    int sndbuf = 262144; /* 256 KB */
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    /* Bind to our own address so outgoing packets are routed through the
     * vnet_li_hi interface rather than any other interface that may appear. */
    struct sockaddr_in src = {0};
    src.sin_family      = AF_INET;
    src.sin_port        = 0; /* kernel assigns ephemeral source port */
    src.sin_addr.s_addr = inet_addr(NET_LI_IP);
    if (bind(sock, (struct sockaddr *)&src, sizeof(src)) != 0) {
        /* Non-fatal: routing will still work once the interface is up. */
        printf("net_sender: WARNING: bind(%s) failed: %s\n",
               NET_LI_IP, strerror(errno));
    }

    /* connect() records the destination so we can use send() in the hot path. */
    struct sockaddr_in dst = {0};
    dst.sin_family      = AF_INET;
    dst.sin_port        = htons(NET_SENDER_PORT);
    dst.sin_addr.s_addr = inet_addr(NET_HI_IP);
    if (connect(sock, (struct sockaddr *)&dst, sizeof(dst)) != 0) {
        printf("net_sender: ERROR: connect(%s:%d) failed: %s\n",
               NET_HI_IP, NET_SENDER_PORT, strerror(errno));
        close(sock);
        return 1;
    }

    struct timeval hello_tv = {
        .tv_sec  = 0,
        .tv_usec = 250000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &hello_tv, sizeof(hello_tv));


    /* ── Start background flood (optional) ──────────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0) {
        start_background_flood(&flood_pid, flood_mbps);
    }

    /* ── Periodic send loop ─────────────────────────────────────────────── */

    static struct net_message msg;
    memset(msg.payload, 0xCD, sizeof(msg.payload));

    uint64_t target_packets = (uint64_t)n_packets;
    if (!wait_for_receiver_ready(sock, target_packets)) {
        printf("net_sender: interrupted before receiver became ready\n");
        close(sock);
        return 1;
    }

    printf("li_net_sender: receiver requested %llu packets\n",
           (unsigned long long)target_packets);
    printf("li_net_sender: ready — starting 1 ms send loop\n\n");

    uint64_t seq        = 0;
    uint64_t send_errs  = 0;
    uint64_t start_ns   = get_time_ns();
    uint64_t next_wake  = start_ns + SENDER_PERIOD_NS;

    /* Open a lightweight send-side log (period deviation) */
    FILE *fp_csv = fopen(csv_path, "w");
    if (fp_csv)
        fprintf(fp_csv, "seq,send_time_ns,period_dev_us\n");

    uint64_t prev_send_ns = 0;

    while (g_running && seq < target_packets) {
        uint64_t now = get_time_ns();

        /* Absolute-time sleep: clock_nanosleep works correctly on vm-li. */
        if (now < next_wake) {
            struct timespec wake_ts = ns_to_timespec(next_wake);
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake_ts, NULL);
        }

        msg.seq          = seq;
        msg.send_time_ns = get_time_ns();

        if (send(sock, &msg, sizeof(msg), 0) < 0) {
            if (errno != ENOBUFS && errno != EAGAIN) {
                fprintf(stderr, "WARNING: send seq=%llu: %s\n",
                        (unsigned long long)seq, strerror(errno));
            }
            send_errs++;
        } else if (fp_csv && seq > 0) {
            /* Log period deviation (actual send interval vs expected). */
            int64_t dev = (int64_t)(msg.send_time_ns - prev_send_ns)
                          - (int64_t)SENDER_PERIOD_NS;
            fprintf(fp_csv, "%llu,%llu,%.3f\n",
                    (unsigned long long)seq,
                    (unsigned long long)msg.send_time_ns,
                    (double)dev / 1e3);
        }

        prev_send_ns = msg.send_time_ns;
        seq++;
        next_wake += SENDER_PERIOD_NS;
    }

    if (fp_csv) fclose(fp_csv);

    /* ── Stop background flood ──────────────────────────────────────────── */

    if (flood_pid > 0) {
        printf("\nStopping net_flood (PID %d)...\n", (int)flood_pid);
        kill(flood_pid, SIGTERM);
        waitpid(flood_pid, NULL, 0);
        printf("net_flood stopped.\n");
    }

    double elapsed = (double)(get_time_ns() - start_ns) / 1e9;
    printf("\nli_net_sender: done.\n");
    printf("  Packets sent:   %llu\n",   (unsigned long long)seq);
    printf("  Send errors:    %llu\n",   (unsigned long long)send_errs);
    printf("  Elapsed:        %.3f s\n", elapsed);
    if (seq < target_packets)
        printf("  Stopped early:  %llu/%llu\n",
               (unsigned long long)seq,
               (unsigned long long)target_packets);
    if (csv_path)
        printf("  Send-side CSV:  %s\n", csv_path);
    printf("\nResults are on vm-hi console (qemu_serial.log).\n");

    close(sock);
    return 0;
}
