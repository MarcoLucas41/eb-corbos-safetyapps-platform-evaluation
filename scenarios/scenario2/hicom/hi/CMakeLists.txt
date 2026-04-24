# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as trusted executables.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

# Both shm_ping_hi (PID 1 / pinger) and shm_echo_hi (child / echo server)
# are built here.  They share the same includes and shmlib dependency.
foreach(bin shm_ping_hi shm_echo_hi)
  add_executable(${bin} ${bin}.c)

  target_include_directories(${bin} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../common
  )

  target_compile_definitions(${bin} PRIVATE
    PING_PAYLOAD_BYTES=${PING_PAYLOAD_BYTES}
  )

  # shmlib: shared memory abstractions
  # m:      libm for sqrt() in shm_ping_hi (harmless for shm_echo_hi)
  target_link_libraries(${bin} PRIVATE shmlib m)

  target_enable_hi(${bin})

  # Install to the hi VM root filesystem so the hypervisor can find them.
  # shm_ping_hi is referenced by init= in the kernel cmdline.
  # shm_echo_hi is exec'd by shm_ping_hi at runtime.
  install(TARGETS ${bin} DESTINATION /)
endforeach()
