add_executable(shm_trigger shm_trigger.c)

target_include_directories(shm_trigger PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../common
)

target_link_libraries(shm_trigger PRIVATE shmlib)

# Install to /usr/bin so it is on PATH in the li VM.
install(TARGETS shm_trigger DESTINATION /usr/bin)
