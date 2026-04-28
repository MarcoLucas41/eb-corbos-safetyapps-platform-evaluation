# High-integrity binaries must be post-processed by lisa-elf-enabler so the
# safety kernel recognises them as permitted applications.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

# ── net sender ──────────────────────────────────────────────────────────────

# add_executable(net_sender net_sender.c)

# target_include_directories(net_sender PRIVATE
#   ${CMAKE_CURRENT_SOURCE_DIR}/../common
# )

# target_compile_definitions(net_sender PRIVATE
#   NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
# )

# # rt: clock_nanosleep, clock_gettime
# target_link_libraries(net_sender PRIVATE rt)

# target_enable_hi(net_sender)

# # Install to / so the hi VM kernel boot parameter init=/net_sender finds it.
# install(TARGETS net_sender DESTINATION /)

# ── TCP variant ──────────────────────────────────────────────────────────────

add_executable(net_sender_tcp net_sender_tcp.c)

target_include_directories(net_sender_tcp PRIVATE
   ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_sender_tcp PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

target_link_libraries(net_sender_tcp PRIVATE rt)

target_enable_hi(net_sender_tcp)

# Install to / so the hi VM can use init=/net_sender_tcp.
install(TARGETS net_sender_tcp DESTINATION /)

# ── net_receiver in hi ──────────────────────────────────────────────────────────────

# add_executable(hi_net_receiver hi_net_receiver.c)


# target_include_directories(hi_net_receiver PRIVATE
#   ${CMAKE_CURRENT_SOURCE_DIR}/../common
# )

# target_compile_definitions(hi_net_receiver PRIVATE
#   NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
# )

# target_link_libraries(hi_net_receiver PRIVATE
#   Threads::Threads
#   m    # sqrt() in print_metric
#   rt   # clock_gettime
# )

# target_enable_hi(hi_net_receiver)
# install(TARGETS hi_net_receiver DESTINATION /)  


