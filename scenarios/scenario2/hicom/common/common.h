/**
 * Scenario 2 — hicom variant
 * Common definitions shared between shm_ping_hi, shm_echo_hi, and shm_trigger.
 *
 * Key difference from scenario2 (proxycomshm variant):
 *   All communication between the pinger and echo processes goes through the
 *   hicom shared memory region (hv_hicomshm, 132 KB), which is only accessible
 *   to vm-hi. vm-li cannot read or write this region, so any latency effects
 *   observed are due to cross-VM resource contention (cache, memory bandwidth,
 *   QEMU host scheduling), not direct channel access from the low-integrity
 *   partition.
 */

#ifndef SCENARIO2_HICOM_COMMON_H
#define SCENARIO2_HICOM_COMMON_H

#include <stdint.h>
#include "shmlib_ringbuffer.h"

/* ── UUIDs ─────────────────────────────────────────────────────────────── */

/** UUID for shm_ping_hi (PID 1).  Registered in proxyshm so that
 *  shm_trigger on vm-li can send a run trigger via li_to_hi. */
#define HICOM_PINGER_UUID "c3d4e5f6-a7b8-9012-cdef-012345678901"

/** UUID for shm_echo_hi (child process).  Registered in proxyshm for
 *  observability; not used for communication by vm-li. */
#define HICOM_ECHO_UUID   "d4e5f6a7-b8c9-0123-def0-123456789012"

/* ── Timing constants ──────────────────────────────────────────────────── */

/** Sleep between ring-buffer poll attempts (microseconds).
 *  Floors measurable RTL at ~2 × POLL_INTERVAL_US (~200 µs). */
#define POLL_INTERVAL_US  100

/** Timeout for waiting for an echo response (milliseconds). */
#define RTL_TIMEOUT_MS    500

/* ── Message format ────────────────────────────────────────────────────── */

/** Payload bytes after the 16-byte header.  Default: 48 B → 64 B total.
 *  Override at cmake configure time via -DPING_PAYLOAD_BYTES=<N>. */
#ifndef PING_PAYLOAD_BYTES
#define PING_PAYLOAD_BYTES 48
#endif

/** Fixed-size ping message transmitted through the hicom ring buffers. */
struct hicom_ping_message {
    uint64_t seq;                        /**< Sequence number (0-based)        */
    uint64_t send_time_ns;               /**< CLOCK_MONOTONIC at send (vm-hi)  */
    uint8_t  payload[PING_PAYLOAD_BYTES]; /**< Padding for message size tests  */
};

/* ── hicom channel layout ──────────────────────────────────────────────── */

/**
 * Layout overlaid on the raw hicom memory region (hv_hicomshm, 132 KB).
 *
 * Two shmlib_ring_buffer structs (~4116 B each on aarch64) occupy the first
 * ~8232 B of the 132 KB region, leaving over 120 KB unused.
 *
 * IMPORTANT: shm_ping_hi (PID 1) must memset this struct to zero BEFORE
 * spawning shm_echo_hi.  shm_echo_hi must NOT memset — it overlays the
 * already-initialised shared memory.
 */
struct hicom_channel {
    struct shmlib_ring_buffer pinger_to_echo; /**< shm_ping_hi → shm_echo_hi */
    struct shmlib_ring_buffer echo_to_pinger; /**< shm_echo_hi → shm_ping_hi */
};

/* ── Trigger protocol ──────────────────────────────────────────────────── */

/** Single byte written by shm_trigger (vm-li) to shm_ping_hi's li_to_hi
 *  proxyshm channel to start a ping run. */
#define HICOM_TRIGGER_GO  0x47  /* ASCII 'G' */

#endif /* SCENARIO2_HICOM_COMMON_H */
