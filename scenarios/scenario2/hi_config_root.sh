#!/bin/sh
# Make both HI binaries executable.
# shm_ping_hi is the init process (referenced by init= in the hi VM cmdline).
# shm_echo_hi is exec'd at runtime by shm_ping_hi.
chmod +x /shm_ping_hi /shm_echo_hi
