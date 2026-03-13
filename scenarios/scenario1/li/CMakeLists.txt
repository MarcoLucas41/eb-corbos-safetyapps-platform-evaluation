add_executable(stress_workload stress_workload.c)
target_include_directories(stress_workload PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../common)
target_link_libraries(stress_workload
PRIVATE
    Threads::Threads
    shmlib
)

install(TARGETS 
    stress_workload
    DESTINATION ${CMAKE_INSTALL_BINDIR})

