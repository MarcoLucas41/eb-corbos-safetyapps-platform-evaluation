cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(
  scenario3_hicom
  LANGUAGES C
)

include(GNUInstallDirs)

option(BUILD_HI
  "Build HI apps (hi_net_ping + hi_net_echo); OFF builds li app (net_trigger)"
  OFF
)

# Payload size can be overridden at configure time for the message-size
# sub-experiment.  Default 48 B gives a 64-byte total struct (8+8+48).
set(NET_PAYLOAD_BYTES "48" CACHE STRING "net_message payload size in bytes")

add_compile_options(-Wall -Wextra -Werror)

find_package(Threads REQUIRED)

if(BUILD_HI)
  add_subdirectory(hi)
else()
  add_subdirectory(li)
endif()
