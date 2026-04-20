cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(
  scenario2
  LANGUAGES C
)

include(GNUInstallDirs)

option(BUILD_HI "Build high-integrity application (shm_echo); otherwise build low-integrity (shm_client)" OFF)

# Allow overriding the ping message payload size at configure time.
# Default 48 B gives a 64-byte total struct (8+8+48, naturally aligned).
# Valid alternatives for H4 sub-experiment: 0 (16 B), 496 (512 B), 3056 (3072 B).
set(PING_PAYLOAD_BYTES "48" CACHE STRING "Ping message payload size in bytes")

add_compile_options(-Wall -Wextra -Werror)

# Reuse the shmlib from scenario1 — no duplication.
add_subdirectory(../scenario1/shmlib shmlib_build)

find_package(Threads REQUIRED)

if(BUILD_HI)
  add_subdirectory(hi)
else()
  add_subdirectory(li)
endif()
