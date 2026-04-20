# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

add_executable(shm_echo shm_echo.c)

target_include_directories(shm_echo PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(shm_echo PRIVATE
  PING_PAYLOAD_BYTES=${PING_PAYLOAD_BYTES}
)

target_link_libraries(shm_echo PRIVATE shmlib)

target_enable_hi(shm_echo)

# Install to root (/) so the hi VM uses it as its init process.
install(TARGETS shm_echo DESTINATION /)
