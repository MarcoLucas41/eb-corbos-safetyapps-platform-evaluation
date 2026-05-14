/**
 * Scenario 3: Network Communication Determinism
 * Common definitions shared between the vm-hi echo server and the vm-li pinger.
 *
 * Measurement method: Round-Trip Latency (RTL)
 *   vm-li sends a periodic probe, vm-hi echoes it back unchanged,
 *   vm-li measures RTL = recv_time_ns − send_time_ns on its own clock.
 *   No cross-VM clock synchronisation is required.
 */

#ifndef SCENARIO3_COMMON_H
#define SCENARIO3_COMMON_H

#include <stdint.h>

/* IP addresses for the inter-VM vnet_li_hi network (10.0.1.0/24).
 * vm-hi is configured statically via the kernel ip= boot parameter.
 * vm-li is configured by the experiment script after boot. */
#define NET_HI_IP           "10.0.1.1"
#define NET_LI_IP           "10.0.1.2"

/* Port used for all communication (control handshake + data). */
#define NET_SENDER_PORT     5000

/* UDP port targeted by the optional background flood generator.
 * vm-hi has no listener here; packets are silently dropped.
 * An iptables rule in hi_config_root.sh suppresses ICMP port-unreachable
 * responses so the flood does not perturb the measured link. */
#define NET_FLOOD_PORT      5001

/* Probe period in nanoseconds: 1 ms → 1000 Hz. */
#define SENDER_PERIOD_NS    1000000ULL

/* Receive timeout on the pinger waiting for an echo (milliseconds).
 * Must be >> expected RTL (< 1 ms in QEMU) and << probe period (1000 ms)
 * so a lost echo is declared quickly without stalling the send schedule. */
#define RECV_TIMEOUT_MS     100

/* Control packet sequence values — outside the 0-based data sequence. */
#define NET_CTRL_SEQ_HELLO        UINT64_MAX
#define NET_CTRL_SEQ_HELLO_ACK    (UINT64_MAX - 1ULL)

/* Payload bytes appended after the 16-byte header.
 * Default 48 B → 64 B total struct, a clean multiple of 8 with no padding.
 * Can be overridden at CMake configure time via -DNET_PAYLOAD_BYTES=<N>
 * for the payload-size sub-experiment. */
#ifndef NET_PAYLOAD_BYTES
#define NET_PAYLOAD_BYTES   48
#endif

/**
 * Fixed-size probe / echo message.
 *
 * Total size: 8 + 8 + NET_PAYLOAD_BYTES bytes (default: 64 B).
 *
 * RTL Measurement Protocol
 * ─────────────────────────
 * HANDSHAKE (always over UDP):
 *   1. Pinger (vm-li) sends HELLO to echo server (vm-hi:NET_SENDER_PORT):
 *        seq          = NET_CTRL_SEQ_HELLO
 *        send_time_ns = 0  (unused in RTL mode)
 *        payload[0]   = protocol: 0 = UDP, 1 = TCP
 *        payload[1..8]= n_target as little-endian uint64
 *   2. Echo server replies with HELLO_ACK:
 *        seq          = NET_CTRL_SEQ_HELLO_ACK
 *        send_time_ns = 0
 *        payload[0]   = echoed protocol indicator
 *   3. If TCP: pinger opens TCP connection to echo server.
 *              Echo server listens/accepts on the same port.
 *
 * DATA PHASE (ping-echo loop):
 *   - Pinger stamps seq + send_time_ns (CLOCK_MONOTONIC on vm-li) just
 *     before send(), then immediately blocks waiting for the echo.
 *   - Echo server receives the probe and sends the struct back UNCHANGED.
 *   - Pinger records recv_time_ns (CLOCK_MONOTONIC on vm-li) right after
 *     recv() and computes:
 *       RTL = recv_time_ns − send_time_ns   (same clock, no sync needed)
 *       IAJ = |recv_time_ns[i] − recv_time_ns[i-1] − SENDER_PERIOD_NS|
 *
 * No clock-offset calibration is required: both timestamps are taken on
 * vm-li's CLOCK_MONOTONIC, so RTL is always non-negative and accurate.
 */
struct net_message {
    uint64_t seq;                        /* Sequence number (0-based per run) */
    uint64_t send_time_ns;               /* CLOCK_MONOTONIC on vm-li before send() */
    uint8_t  payload[NET_PAYLOAD_BYTES]; /* payload[0] used in control msgs */
};

#endif /* SCENARIO3_COMMON_H */
