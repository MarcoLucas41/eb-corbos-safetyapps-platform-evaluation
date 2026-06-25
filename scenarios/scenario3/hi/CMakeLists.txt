# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as permitted applications.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()


add_executable(net_echo net_echo.c)


target_include_directories(net_echo PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_echo PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

target_link_libraries(net_echo PRIVATE
  Threads::Threads
  rt   # clock_gettime
)

target_enable_hi(net_echo)
install(TARGETS net_echo DESTINATION /)  


