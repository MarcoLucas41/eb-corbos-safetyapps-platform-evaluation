/**
 * Scenario 2: Inter-VM Communication Latency and Reliability
 * Common definitions shared between shm_echo (vm-hi) and shm_client (vm-li).
 */

#ifndef SCENARIO2_COMMON_H
#define SCENARIO2_COMMON_H

#include <stdint.h>

/* UUID used by shm_echo to register in the proxyshm directory */
#define SHM_ECHO_UUID "b2c3d4e5-f6a7-8901-bcde-f12345678901"

/* Poll interval for the echo server and ping client (microseconds).
 * Both sides sleep this long between ring-buffer poll attempts.
 * This floors the measurable RTL at approximately 2 × POLL_INTERVAL_US. */
#define POLL_INTERVAL_US 100

/* Timeout waiting for an echo response (milliseconds).
 * A ping is marked lost if no echo arrives within this window. */
#define RTL_TIMEOUT_MS 500

/* Payload size appended after the 16-byte header.
 * Can be overridden at CMake configure time via -DPING_PAYLOAD_BYTES=<N>
 * to test payload-size sensitivity (H4 sub-experiment).
 * Default (48 B) gives a total struct size of 64 B (8 + 8 + 48), which is
 * a clean multiple of 8 with no compiler-added padding. */
#ifndef PING_PAYLOAD_BYTES
#define PING_PAYLOAD_BYTES 48
#endif

/**
 * Fixed-size ping/echo message.
 *
 * Total size: 8 + 8 + PING_PAYLOAD_BYTES bytes (default: 64 B).
 *
 * Protocol:
 *   - shm_client fills seq + send_time_ns, writes sizeof(struct ping_message)
 *     bytes to li_to_hi.
 *   - shm_echo reads sizeof(struct ping_message) bytes, writes the unchanged
 *     struct back to hi_to_li.
 *   - shm_client reads sizeof(struct ping_message) bytes, records recv time,
 *     computes RTL = recv_time_ns - send_time_ns.
 *
 * The ring buffer holds up to SHMLIB_RING_BUF_MAX_DATA_LEN (4095) bytes.
 * With the default PING_PAYLOAD_BYTES=48 the message is 64 B; the maximum
 * supported payload is 4095 - 16 = 4079 B.
 */
struct ping_message {
    uint64_t seq;                       /* Sequence number (0-based)          */
    uint64_t send_time_ns;              /* CLOCK_MONOTONIC at send on vm-li   */
    uint8_t  payload[PING_PAYLOAD_BYTES]; /* Padding / payload for size tests */
};

#endif /* SCENARIO2_COMMON_H */
