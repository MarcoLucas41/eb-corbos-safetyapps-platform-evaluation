# add_executable(net_receiver net_receiver.c)

# target_include_directories(net_receiver PRIVATE
#   ${CMAKE_CURRENT_SOURCE_DIR}/../common
# )

# target_compile_definitions(net_receiver PRIVATE
#   NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
# )

# target_link_libraries(net_receiver PRIVATE
#   Threads::Threads
#   m    # sqrt() in print_metric
#   rt   # clock_gettime
# )

# install(TARGETS net_receiver DESTINATION ${CMAKE_INSTALL_BINDIR})

# add_executable(net_flood net_flood.c)

# target_link_libraries(net_flood PRIVATE rt)

# install(TARGETS net_flood DESTINATION ${CMAKE_INSTALL_BINDIR})

# ── TCP variant ──────────────────────────────────────────────────────────────

add_executable(net_receiver_tcp net_receiver_tcp.c)

target_include_directories(net_receiver_tcp PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_receiver_tcp PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

target_link_libraries(net_receiver_tcp PRIVATE
  Threads::Threads
  m
  rt
)

install(TARGETS net_receiver_tcp DESTINATION ${CMAKE_INSTALL_BINDIR})


# ── Net sender on li ──────────────────────────────────────────────────────────────
# add_executable(li_net_sender li_net_sender.c)
# target_include_directories(li_net_sender PRIVATE
#   ${CMAKE_CURRENT_SOURCE_DIR}/../common
# )
# target_compile_definitions(li_net_sender PRIVATE
#   NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
# )
# target_link_libraries(li_net_sender PRIVATE
#   Threads::Threads
#   m
#   rt
# )
# install(TARGETS li_net_sender DESTINATION ${CMAKE_INSTALL_BINDIR})
