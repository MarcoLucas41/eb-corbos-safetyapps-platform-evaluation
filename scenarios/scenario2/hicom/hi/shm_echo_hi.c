/**
 * shm_echo_hi — HI echo server (Scenario 2, hicom variant)
 *
 * Spawned as a child of shm_ping_hi via clone/execve.  Communicates
 * exclusively through the hicom shared memory region (vm-li has no access).
 *
 * Behaviour:
 *   1. Registers in proxyshm with HICOM_ECHO_UUID (init_proxyshm=false —
 *      shm_ping_hi already cleared the table).
 *   2. Obtains the hicom pointer and overlays hicom_channel — no memset
 *      (shm_ping_hi already initialised the struct).
 *   3. Loops: reads from pinger_to_echo, echoes unchanged to echo_to_pinger.
 *   4. Never exits (runs indefinitely alongside shm_ping_hi).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdbool.h>

#include "shmlib_hi_app.h"
#include "shmlib_logger.h"
#include "common.h"

int main(void)
{
    setbuf(stdout, NULL);

    struct shmlib_hi_app *app __attribute__((unused)) = NULL;
    struct shmlib_shm     shm     = {0};
    struct shmlib_shm_ptr hicom_ptr = {0};

    /* init_proxyshm=false: shm_ping_hi already initialised the proxyshm table.
     * We still register our own UUID so the orchestration script can confirm
     * that both HI processes are running. */
    int ret = shmlib_hi_app_create_new(&shm, HICOM_ECHO_UUID, &app,
                                       &hicom_ptr, NULL, false);
    if (ret) {
        printf("[echo] ERROR: shmlib_hi_app_create_new failed: %d\n", ret);
        /* Not PID 1, but we must not exit (it would leave the pinger waiting). */
        while (1) sleep(3600);
    }
    printf("[echo] Registered with UUID: %s\n", HICOM_ECHO_UUID);

    /* Overlay the same hicom memory region — no memset here.
     * shm_ping_hi already zeroed this before we were spawned. */
    struct hicom_channel *chan = (struct hicom_channel *)hicom_ptr.hicom;
    printf("[echo] hicom_channel at %p\n", (void *)chan);
    printf("[echo] Ready — polling pinger_to_echo ring buffer\n");

    const struct timespec poll_sleep = {
        .tv_sec  = 0,
        .tv_nsec = (long)POLL_INTERVAL_US * 1000L
    };

    static struct hicom_ping_message msg;
    uint64_t echo_count = 0;

    while (true) {
        ssize_t n = shmlib_ring_buffer_read(&chan->pinger_to_echo,
                                            (char *)&msg, sizeof(msg));
        if (n == (ssize_t)sizeof(msg)) {
            /* Echo the message back unchanged.
             * The pinger verifies seq and computes RTL from send_time_ns. */
            ret = shmlib_ring_buffer_write(&chan->echo_to_pinger,
                                           (char *)&msg, sizeof(msg));
            if (ret < 0) {
                printf("[echo] WARNING: echo write failed (seq=%llu): %d\n",
                       (unsigned long long)msg.seq, ret);
            } else {
                echo_count++;
                if (echo_count % 1000 == 0) {
                    printf("[echo] %llu messages echoed\n",
                           (unsigned long long)echo_count);
                }
            }
        } else if (n == -EAGAIN) {
            nanosleep(&poll_sleep, NULL);
        } else if (n > 0) {
            /* Partial read: corrupt or stale data — discard */
            printf("[echo] WARNING: partial read (%zd B, expected %zu B) — discarding\n",
                   n, sizeof(msg));
            nanosleep(&poll_sleep, NULL);
        } else {
            printf("[echo] WARNING: ring buffer read error: %zd\n", n);
            nanosleep(&poll_sleep, NULL);
        }
    }

    return 0;
}
