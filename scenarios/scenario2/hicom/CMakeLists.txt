cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(
  scenario2_hicom
  LANGUAGES C
)

include(GNUInstallDirs)

option(BUILD_HI
  "Build HI apps (shm_ping_hi + shm_echo_hi); OFF builds li app (shm_trigger)"
  OFF)

# Override payload size at configure time to test message size sensitivity.
set(PING_PAYLOAD_BYTES "48" CACHE STRING "Ping message payload size in bytes")

add_compile_options(-Wall -Wextra -Werror)

# Reuse the shared-memory library from the upstream ebclfsa-demo.
add_subdirectory(../../ebclfsa-demo/shmlib shmlib_build)

find_package(Threads REQUIRED)

if(BUILD_HI)
  add_subdirectory(hi)
else()
  add_subdirectory(li)
endif()
