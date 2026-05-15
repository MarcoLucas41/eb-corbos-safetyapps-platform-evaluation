# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as trusted executables.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

# Both hi_net_ping (PID 1 / pinger) and hi_net_echo (child / echo server)
# are built here.  They share the same includes and link flags.
foreach(bin hi_net_ping hi_net_echo)
  add_executable(${bin} ${bin}.c)

  target_include_directories(${bin} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../common
  )

  target_compile_definitions(${bin} PRIVATE
    NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
  )

  # rt: clock_gettime
  # m:  sqrt() in hi_net_ping's print_metric (harmless for hi_net_echo)
  target_link_libraries(${bin} PRIVATE rt m)

  target_enable_hi(${bin})

  # Install to / so the hi VM kernel boot parameter init=/hi_net_ping finds it,
  # and hi_net_ping can exec /hi_net_echo at runtime.
  install(TARGETS ${bin} DESTINATION /)
endforeach()
