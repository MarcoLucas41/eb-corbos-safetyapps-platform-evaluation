# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as permitted applications.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

add_executable(net_sender net_sender.c)

target_include_directories(net_sender PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_sender PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

# rt: clock_nanosleep, clock_gettime
target_link_libraries(net_sender PRIVATE rt)

target_enable_hi(net_sender)

# Install to / so the hi VM kernel boot parameter init=/net_sender finds it.
install(TARGETS net_sender DESTINATION /)