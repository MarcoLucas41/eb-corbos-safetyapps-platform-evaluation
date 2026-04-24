/**
 * shm_trigger — run trigger for the hicom variant experiment (vm-li side)
 *
 * Sends a single HICOM_TRIGGER_GO byte to shm_ping_hi's li_to_hi proxyshm
 * channel, which causes shm_ping_hi to start one ping run.
 *
 * Called by the orchestration script once per experimental run:
 *   ssh vm-li shm_trigger
 *
 * shm_trigger does NOT start stress-ng — that is managed separately by the
 * orchestration script so that stress-ng is already ramped up before the
 * trigger is sent.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>

#include "shmlib_proxyshm.h"
#include "shmlib_ringbuffer.h"
#include "shmlib_hi_app.h"
#include "shmlib_shm.h"
#include "common.h"

/* Maximum time to wait for shm_ping_hi to register (seconds). */
#define WAIT_TIMEOUT_S 60

int main(void)
{
    struct shmlib_shm     shm      = {0};
    struct shmlib_shm_ptr proxyshm = {0};
    struct shmlib_hi_app *pinger   = NULL;

    int ret = shmlib_shm_init(&shm);
    if (ret) {
        fprintf(stderr, "ERROR: shmlib_shm_init: %d\n", ret);
        return 1;
    }

    ret = shmlib_shm_get_proxyshm(&shm, &proxyshm);
    if (ret) {
        fprintf(stderr, "ERROR: shmlib_shm_get_proxyshm: %d\n", ret);
        return 1;
    }

    /* Wait for shm_ping_hi to register in proxyshm (it may still be booting). */
    const struct timespec wait_sleep = { .tv_sec = 0, .tv_nsec = 100000000L };
    int elapsed_ds = 0;  /* tenths of seconds */

    while (pinger == NULL) {
        shmlib_proxyshm_lock(&proxyshm);
        ret = shmlib_proxyshm_find_app(&proxyshm, HICOM_PINGER_UUID, &pinger);
        shmlib_proxyshm_unlock(&proxyshm);

        if (ret != 0 && ret != -ENOENT) {
            fprintf(stderr, "ERROR: proxyshm_find_app: %d\n", ret);
            return 1;
        }

        if (pinger == NULL) {
            elapsed_ds++;
            if (elapsed_ds % 10 == 0) {
                printf("Waiting for shm_ping_hi to register... (%d s)\n",
                       elapsed_ds / 10);
            }
            if (elapsed_ds / 10 >= WAIT_TIMEOUT_S) {
                fprintf(stderr, "ERROR: timeout waiting for pinger (%d s)\n",
                        WAIT_TIMEOUT_S);
                return 1;
            }
            nanosleep(&wait_sleep, NULL);
        }
    }

    printf("shm_ping_hi found.  Sending GO trigger...\n");

    char trigger = (char)HICOM_TRIGGER_GO;
    ret = shmlib_ring_buffer_write(&pinger->li_to_hi, &trigger, 1);
    if (ret < 0) {
        fprintf(stderr, "ERROR: ring_buffer_write failed: %d\n", ret);
        return 1;
    }

    printf("GO sent.\n");
    return 0;
}
