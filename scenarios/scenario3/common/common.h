/**
 * Scenario 3: Network Communication Determinism
 * Common definitions shared between net_sender (vm-hi) and net_receiver (vm-li).
 */

#ifndef SCENARIO3_COMMON_H
#define SCENARIO3_COMMON_H

#include <stdint.h>

/* IP addresses for the inter-VM vnet_li_hi network (10.0.1.0/24).
 * vm-hi is configured statically via the kernel ip= boot parameter.
 * vm-li is configured by the experiment script after boot. */
#define NET_HI_IP           "10.0.1.1"
#define NET_LI_IP           "10.0.1.2"

/* UDP port on vm-li that net_sender delivers safety-critical packets to. */
#define NET_SENDER_PORT     5000

/* UDP port on vm-hi targeted by the background flood.
 * vm-hi has no listener here; packets are dropped by the kernel.
 * An iptables rule in hi_config_root.sh suppresses the ICMP responses. */
#define NET_FLOOD_PORT      5001

/* Sender period in nanoseconds: 1 ms → 1000 Hz. */
#define SENDER_PERIOD_NS    1000000ULL

/* Receive timeout on net_receiver (milliseconds).
 * If no packet arrives within this window the receiver counts a loss and
 * continues waiting; it will not block indefinitely. */
#define RECV_TIMEOUT_MS     500

/* Payload bytes appended after the 16-byte header.
 * Default 48 B → 64 B total struct, a clean multiple of 8 with no padding.
 * Can be overridden at CMake configure time via -DNET_PAYLOAD_BYTES=<N>
 * for the H4 payload-size sub-experiment. */
#ifndef NET_PAYLOAD_BYTES
#define NET_PAYLOAD_BYTES   48
#endif

/**
 * Fixed-size safety-critical message.
 *
 * Total size: 8 + 8 + NET_PAYLOAD_BYTES bytes (default: 64 B).
 *
 * Protocol:
 *   - net_sender stamps seq + send_time_ns immediately before sendto() and
 *     sends sizeof(struct net_message) bytes to NET_LI_IP:NET_SENDER_PORT.
 *   - net_receiver records recv_time_ns immediately after recvfrom() and
 *     computes:
 *       OWL = recv_time_ns - send_time_ns   (valid in QEMU: shared ARM
 *                                            virtual counter, drift < 1 µs)
 *       IAJ = |recv_time_ns[i] - recv_time_ns[i-1] - SENDER_PERIOD_NS|
 */
struct net_message {
    uint64_t seq;                        /* Sequence number (0-based, monotonic) */
    uint64_t send_time_ns;               /* CLOCK_MONOTONIC on vm-hi before sendto() */
    uint8_t  payload[NET_PAYLOAD_BYTES]; /* Fixed padding / payload for size tests  */
};

#endif /* SCENARIO3_COMMON_H */
