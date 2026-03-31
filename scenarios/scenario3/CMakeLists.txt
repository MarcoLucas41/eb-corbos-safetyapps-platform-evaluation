cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(
  scenario3
  LANGUAGES C
)

include(GNUInstallDirs)

option(BUILD_HI
  "Build high-integrity application (net_sender); otherwise build low-integrity (net_receiver + net_flood)"
  OFF
)

# Payload size can be overridden at configure time for the H4 size-sensitivity
# sub-experiment.  Default 48 B gives a 64-byte total struct (8+8+48).
set(NET_PAYLOAD_BYTES "48" CACHE STRING "net_message payload size in bytes")

add_compile_options(-Wall -Wextra -Werror)

find_package(Threads REQUIRED)

if(BUILD_HI)
  add_subdirectory(hi)
else()
  add_subdirectory(li)
endif()
