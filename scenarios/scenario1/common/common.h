/**
 * Common definitions for scenario1 synchronization
 */

#ifndef SCENARIO1_COMMON_H
#define SCENARIO1_COMMON_H

#include <stdint.h>

/* UUID for speedometer high-integrity application */
#define SPEEDOMETER_UUID "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

/* Message buffer size */
#define BUFFER_SIZE 256

/* Command messages for synchronization */
#define CMD_START "START"
#define CMD_STOP "STOP"
#define CMD_STATUS "STATUS"

/* Statistics structure passed via shared memory */
struct speedometer_stats {
    uint64_t total_cycles;
    uint64_t deadline_misses;
    uint64_t min_execution_ns;
    uint64_t max_execution_ns;
    double mean_execution_us;
    double mean_jitter_us;
    uint64_t max_jitter_ns;
};

#endif /* SCENARIO1_COMMON_H */
