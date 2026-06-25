# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as trusted executables.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

# Both hi_shm_ping (PID 1 / pinger) and hi_shm_echo (child / echo server)
# are built here.  They share the same includes and shmlib dependency.
foreach(bin hi_shm_ping hi_shm_echo)
  add_executable(${bin} ${bin}.c)

  target_include_directories(${bin} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../common
  )

  target_compile_definitions(${bin} PRIVATE
    PING_PAYLOAD_BYTES=${PING_PAYLOAD_BYTES}
  )

  # shmlib: shared memory abstractions
  # m:      libm for sqrt() in hi_shm_ping (harmless for hi_shm_echo)
  target_link_libraries(${bin} PRIVATE shmlib m)

  target_enable_hi(${bin})

  # Install to the hi VM root filesystem so the hypervisor can find them.
  # hi_shm_ping is referenced by init= in the kernel cmdline.
  # hi_shm_echo is exec'd by hi_shm_ping at runtime.
  install(TARGETS ${bin} DESTINATION /)
endforeach()
