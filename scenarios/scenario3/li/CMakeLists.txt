add_executable(net_receiver net_receiver.c)

target_include_directories(net_receiver PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(net_receiver PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)

target_link_libraries(net_receiver PRIVATE
  Threads::Threads
  m    # sqrt() in print_metric
  rt   # clock_gettime
)

add_executable(net_flood net_flood.c)

target_link_libraries(net_flood PRIVATE rt)

install(TARGETS net_receiver net_flood DESTINATION ${CMAKE_INSTALL_BINDIR})

