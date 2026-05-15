add_executable(net_trigger net_trigger.c)

target_include_directories(net_trigger PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_trigger PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

target_link_libraries(net_trigger PRIVATE rt)

install(TARGETS net_trigger DESTINATION ${CMAKE_INSTALL_BINDIR})

# net_flood is reused from the main scenario3 li directory.
# It is built and installed there — the orchestration script places it
# on PATH in the li VM image so net_trigger can exec it.
