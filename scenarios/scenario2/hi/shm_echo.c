#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdbool.h>
#include <signal.h>
#include <sys/mman.h>

#include "shmlib_hi_app.h"
#include "shmlib_logger.h"
#include "common.h"

static volatile int g_running = 1;

static void sigint_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    struct shmlib_hi_app *app = NULL;
    struct shmlib_shm shm = {0};

    /* Disable stdout buffering: musl performs an ioctl on write to a
     * buffered stdout which is blocked by the safety kernel. */
    setbuf(stdout, NULL);

    printf("========================================\n");
    printf("SHM Echo Server\n");
    printf("Scenario 2 - EB corbos Linux Evaluation\n");
    printf("========================================\n");
    printf("Message size: %zu B  Poll interval: %d us\n",
           sizeof(struct ping_message), POLL_INTERVAL_US);

    /* Initialise shared memory and register this app in the proxyshm
     * directory so shm_client on vm-li can discover it by UUID. */
    printf("Initializing shared memory...\n");
    int ret = shmlib_hi_app_create_new(&shm, SHM_ECHO_UUID, &app,
                                       NULL, NULL, true);
    if (ret) {
        printf("ERROR: Failed to initialize shared memory: %d\n", ret);
        /* As PID 1 we cannot exit; fall into idle loop below. */
        goto idle;
    }
    printf("Registered with UUID: %s\n", SHM_ECHO_UUID);

    /* Try to pre-lock memory to prevent page faults in the echo path.
     * This may fail on the safety kernel (ENOSYS); we continue anyway. */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        printf("Warning: mlockall failed: %s (errno=%d) - continuing\n",
               strerror(errno), errno);
    }

    signal(SIGINT, sigint_handler);

    printf("Echo server ready - waiting for pings...\n\n");

    /* Reusable message buffer — declared static to avoid stack allocation
     * in the hot path and ensure it is pre-faulted after mlockall. */
    static struct ping_message msg;

    const struct timespec poll_sleep = {
        .tv_sec  = 0,
        .tv_nsec = (long)POLL_INTERVAL_US * 1000L
    };

    uint64_t echo_count = 0;

    while (g_running) {
        ssize_t n = shmlib_ring_buffer_read(&app->li_to_hi,
                                            (char *)&msg, sizeof(msg));

        if (n == (ssize_t)sizeof(msg)) {
            /* Echo the message back unchanged.  The client uses seq and
             * send_time_ns to verify identity and compute RTL. */
            ret = shmlib_ring_buffer_write(&app->hi_to_li,
                                           (char *)&msg, sizeof(msg));
            if (ret < 0) {
                printf("Warning: echo write failed (seq=%llu): %d\n",
                       (unsigned long long)msg.seq, ret);
            } else {
                echo_count++;
                if (echo_count % 1000 == 0) {
                    printf("[echo] %llu messages echoed\n",
                           (unsigned long long)echo_count);
                }
            }
        } else if (n == -EAGAIN) {
            /* No data available — sleep and retry. */
            nanosleep(&poll_sleep, NULL);
        } else if (n > 0) {
            /* Partial read: the message is corrupt or the ring buffer
             * contains stale data from a previous run.  Discard and
             * continue; the client will time out and report a loss. */
            printf("Warning: partial read (%zd B, expected %zu B) — "
                   "discarding\n", n, sizeof(msg));
            nanosleep(&poll_sleep, NULL);
        } else {
            /* Unexpected error code */
            printf("Warning: ring buffer read error: %zd\n", n);
            nanosleep(&poll_sleep, NULL);
        }
    }

    printf("\nEcho server terminated.  Total messages echoed: %llu\n",
           (unsigned long long)echo_count);

idle:
    /* As PID 1 the kernel will panic if we return.  Enter an idle loop
     * so the hi VM stays alive while the experiment finishes. */
    if (getpid() == 1) {
        printf("Running as init (PID 1) - entering idle loop\n");
        while (1) {
            sleep(3600);
        }
    }

    return 0;
}
