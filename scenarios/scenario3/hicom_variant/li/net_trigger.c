/*
 * net_trigger.c — Run trigger for the hicom variant (vm-li side)
 *
 * Sends a single UDP trigger message to hi_net_ping on vm-hi, specifying
 * the experiment protocol (UDP or TCP) and probe count.  Optionally starts
 * net_flood as a background process before sending the trigger, and waits
 * for hi_net_ping's experiment to complete before stopping it.
 *
 * Called by the orchestration script once per experimental run:
 *   ssh vm-li net_trigger [-n 5000] [-t] [-B 64]
 *
 * Usage: net_trigger [-n <count>] [-t] [-B <Mbps>] [-h]
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
#include <sys/socket.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "common.h"

int main(int argc, char *argv[])
{
    int         n_probes   = 5000;
    bool        tcp_mode   = false;
    int         flood_mbps = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n_probes = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-t") == 0) {
            tcp_mode = true;
        } else if (strcmp(argv[i], "-B") == 0 && i + 1 < argc) {
            flood_mbps = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-h") == 0 ||
                   strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("  -n <count>   Probes for hi_net_ping to send (default: 5000)\n");
            printf("  -t           Request TCP transport (default: UDP)\n");
            printf("  -B <Mbps>    Start net_flood at this rate before trigger\n");
            printf("  -h           Show this help\n");
            return 0;
        }
    }

    if (n_probes <= 0 || n_probes > 10000) {
        fprintf(stderr, "ERROR: -n must be 1..10000\n");
        return 1;
    }

    printf("========================================\n");
    printf("net_trigger — Scenario 3 hicom variant\n");
    printf("========================================\n");
    printf("Target:     %s:%d\n", NET_HI_IP, NET_TRIGGER_PORT);
    printf("Protocol:   %s\n",   tcp_mode ? "TCP" : "UDP");
    printf("Probes:     %d\n",   n_probes);
    printf("Flood rate: %d Mbps\n\n", flood_mbps);

    /* ── Optional background flood ─────────────────────────────────────── */

    pid_t flood_pid = -1;
    if (flood_mbps > 0) {
        char bw_str[32];
        snprintf(bw_str, sizeof(bw_str), "%d", flood_mbps);
        printf("Starting background flood → %s:%d at %d Mbps\n",
               NET_HI_IP, NET_FLOOD_PORT, flood_mbps);
        flood_pid = fork();
        if (flood_pid == 0) {
            execlp("net_flood", "net_flood",
                   "-d", NET_HI_IP, "-p", "5001", "-b", bw_str, NULL);
            perror("execlp net_flood");
            exit(1);
        } else if (flood_pid < 0) {
            fprintf(stderr, "WARNING: fork net_flood: %s — continuing\n",
                    strerror(errno));
            flood_pid = -1;
        } else {
            printf("Waiting 2 s for flood to ramp up...\n\n");
            sleep(2);
        }
    }

    /* ── Create UDP socket ─────────────────────────────────────────────── */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "ERROR: socket: %s\n", strerror(errno));
        return 1;
    }

    struct sockaddr_in hi_addr = {0};
    hi_addr.sin_family      = AF_INET;
    hi_addr.sin_port        = htons(NET_TRIGGER_PORT);
    hi_addr.sin_addr.s_addr = inet_addr(NET_HI_IP);

    /* ── Send trigger with retries ─────────────────────────────────────── */

    struct timeval tv = { .tv_sec = 0, .tv_usec = 500000 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    static struct net_message trig;
    static struct net_message reply;
    uint64_t n_probes_u64 = (uint64_t)n_probes;
    unsigned tries = 0;
    bool acked = false;

    printf("Sending TRIGGER to %s:%d (protocol=%s, n=%d)...\n",
           NET_HI_IP, NET_TRIGGER_PORT, tcp_mode ? "TCP" : "UDP", n_probes);

    while (!acked) {
        memset(&trig, 0, sizeof(trig));
        trig.seq        = NET_CTRL_SEQ_TRIGGER;
        trig.payload[0] = tcp_mode ? 1 : 0;
        memcpy(trig.payload + 1, &n_probes_u64, sizeof(uint64_t));

        sendto(sock, &trig, sizeof(trig), 0,
               (struct sockaddr *)&hi_addr, sizeof(hi_addr));

        ssize_t nb = recv(sock, &reply, sizeof(reply), 0);
        if (nb == (ssize_t)sizeof(reply) &&
            reply.seq == NET_CTRL_SEQ_TRIGGER_ACK) {
            acked = true;
            printf("TRIGGER_ACK received — experiment started on vm-hi\n");
        } else {
            tries++;
            if (tries % 10 == 0)
                printf("Still waiting for TRIGGER_ACK (%u tries)...\n", tries);
            if (tries > 120) {
                fprintf(stderr, "ERROR: timeout waiting for TRIGGER_ACK\n");
                close(sock);
                if (flood_pid > 0) { kill(flood_pid, SIGTERM); waitpid(flood_pid, NULL, 0); }
                return 1;
            }
        }
    }

    close(sock);

    /* ── Wait for experiment to complete ───────────────────────────────── */

    /* Estimate: n_probes * 1 ms period + overhead.  Sleep conservatively. */
    int wait_s = (n_probes / 1000) + 5;
    printf("Waiting ~%d s for hi_net_ping to complete %d probes...\n",
           wait_s, n_probes);
    sleep((unsigned)wait_s);

    /* ── Stop background flood ─────────────────────────────────────────── */

    if (flood_pid > 0) {
        printf("Stopping net_flood (PID %d)...\n", (int)flood_pid);
        kill(flood_pid, SIGTERM);
        waitpid(flood_pid, NULL, 0);
        printf("net_flood stopped.\n");
    }

    printf("Done.\n");
    return 0;
}
