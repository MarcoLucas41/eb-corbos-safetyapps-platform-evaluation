add_executable(shm_client shm_client.c)

target_include_directories(shm_client PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_compile_definitions(shm_client PRIVATE
  PING_PAYLOAD_BYTES=${PING_PAYLOAD_BYTES}
)

target_link_libraries(shm_client PRIVATE
  shmlib
  Threads::Threads
  m   # sqrt() for standard deviation
)

install(TARGETS shm_client DESTINATION ${CMAKE_INSTALL_BINDIR})
