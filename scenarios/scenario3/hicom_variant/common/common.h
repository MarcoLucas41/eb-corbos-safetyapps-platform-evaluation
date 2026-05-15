/**
 * Scenario 3 — hicom variant
 * Common definitions shared between hi_net_ping, hi_net_echo, and net_trigger.
 *
 * Architecture:
 *   net_trigger (vm-li) sends a UDP trigger to hi_net_ping (vm-hi, PID 1),
 *   specifying UDP or TCP.  hi_net_ping runs a ping-echo experiment with its
 *   child process hi_net_echo over the local network stack (127.0.0.1) and
 *   measures RTL = recv_time_ns − send_time_ns on its own CLOCK_MONOTONIC.
 *
 *   This mirrors the scenario2/hicom_variant pattern but uses UDP/TCP
 *   networking instead of hicom shared memory.
 */

#ifndef SCENARIO3_HICOM_COMMON_H
#define SCENARIO3_HICOM_COMMON_H

#include <stdint.h>

/* ── Network addresses ─────────────────────────────────────────────────── */

/** IP addresses for the inter-VM vnet_li_hi network (10.0.1.0/24). */
#define NET_HI_IP           "10.0.1.1"
#define NET_LI_IP           "10.0.1.2"

/** Port for trigger messages from net_trigger (vm-li) to hi_net_ping (vm-hi).
 *  Uses the inter-VM network (NET_HI_IP). */
#define NET_TRIGGER_PORT    5000

/** Port for ping-echo between hi_net_ping and hi_net_echo.
 *  Local to vm-hi (127.0.0.1) — no cross-VM traffic. */
#define NET_ECHO_PORT       5002

/** Local IP for hi-only echo traffic. */
#define NET_ECHO_IP         "127.0.0.1"

/** UDP port targeted by the optional background flood generator.
 *  Same as the normal scenario3 variant. */
#define NET_FLOOD_PORT      5001

/* ── Timing ────────────────────────────────────────────────────────────── */

/** Probe period in nanoseconds: 1 ms → 1000 Hz. */
#define SENDER_PERIOD_NS    1000000ULL

/** Receive timeout on the pinger waiting for an echo (milliseconds). */
#define RECV_TIMEOUT_MS     100

/* ── Payload ───────────────────────────────────────────────────────────── */

/** Payload bytes appended after the 16-byte header.
 *  Default 48 B → 64 B total struct.
 *  Override at CMake configure time via -DNET_PAYLOAD_BYTES=<N>. */
#ifndef NET_PAYLOAD_BYTES
#define NET_PAYLOAD_BYTES   48
#endif

/* ── Control packet sequence values ────────────────────────────────────── */

/** Trigger: net_trigger (vm-li) → hi_net_ping (vm-hi). */
#define NET_CTRL_SEQ_TRIGGER      UINT64_MAX
#define NET_CTRL_SEQ_TRIGGER_ACK  (UINT64_MAX - 1ULL)

/** HELLO handshake: hi_net_ping → hi_net_echo (local). */
#define NET_CTRL_SEQ_HELLO        (UINT64_MAX - 2ULL)
#define NET_CTRL_SEQ_HELLO_ACK    (UINT64_MAX - 3ULL)

/* ── Message struct ────────────────────────────────────────────────────── */

/**
 * Fixed-size message used for trigger, handshake, and ping-echo phases.
 *
 * Total size: 8 + 8 + NET_PAYLOAD_BYTES bytes (default: 64 B).
 *
 * TRIGGER PROTOCOL (net_trigger → hi_net_ping, always UDP on NET_TRIGGER_PORT):
 *   1. net_trigger sends:
 *        seq          = NET_CTRL_SEQ_TRIGGER
 *        send_time_ns = 0
 *        payload[0]   = protocol: 0 = UDP, 1 = TCP
 *        payload[1..8]= n_probes as little-endian uint64
 *   2. hi_net_ping replies:
 *        seq          = NET_CTRL_SEQ_TRIGGER_ACK
 *        send_time_ns = 0
 *        payload[0]   = echoed protocol indicator
 *
 * PING-ECHO HANDSHAKE (hi_net_ping → hi_net_echo, UDP on NET_ECHO_IP:NET_ECHO_PORT):
 *   1. HELLO:     seq = NET_CTRL_SEQ_HELLO, payload[0]=protocol, payload[1..8]=n_probes
 *   2. HELLO_ACK: seq = NET_CTRL_SEQ_HELLO_ACK, payload[0]=echoed protocol
 *   3. If TCP: hi_net_ping connects to NET_ECHO_IP:NET_ECHO_PORT
 *
 * PING-ECHO DATA:
 *   hi_net_ping stamps seq + send_time_ns (CLOCK_MONOTONIC) before send(),
 *   hi_net_echo echoes unchanged, hi_net_ping records recv_time_ns and computes:
 *     RTL = recv_time_ns − send_time_ns
 *     IAJ = |recv_time_ns[i] − recv_time_ns[i-1] − SENDER_PERIOD_NS|
 */
struct net_message {
    uint64_t seq;                        /* Sequence number (0-based per run) */
    uint64_t send_time_ns;               /* CLOCK_MONOTONIC on vm-hi before send() */
    uint8_t  payload[NET_PAYLOAD_BYTES]; /* payload[0] used in control msgs */
};

#endif /* SCENARIO3_HICOM_COMMON_H */
