add_executable(net_client net_client.c)
target_include_directories(net_client PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)
target_compile_definitions(net_client PRIVATE
  NET_PAYLOAD_BYTES=${NET_PAYLOAD_BYTES}
)
target_link_libraries(net_client PRIVATE
  Threads::Threads
  m
  rt
)
install(TARGETS net_client DESTINATION ${CMAKE_INSTALL_BINDIR})
