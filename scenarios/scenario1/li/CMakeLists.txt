#add_executable(li_app li_main.c)



#target_include_directories(li_app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../common)
#target_link_libraries(li_app shmlib)

#install(TARGETS li_demo DESTINATION ${CMAKE_INSTALL_BINDIR})


# # Interference workload applications
add_executable(cpu_workload cpu_workload.c)
target_link_libraries(cpu_workload
PRIVATE
    Threads::Threads
    m
 )

 install(TARGETS 
    cpu_workload
    DESTINATION ${CMAKE_INSTALL_BINDIR})

# add_executable(memory_workload memory_workload.cpp)
# target_link_libraries(memory_workload
#     PRIVATE
#         Threads::Threads
# )

# add_executable(io_workload io_workload.cpp)
# target_link_libraries(io_workload
#     PRIVATE
#         Threads::Threads
# )

# add_executable(misbehaving_app misbehaving_app.cpp)
# target_link_libraries(misbehaving_app
#     PRIVATE
#         Threads::Threads
# )

# # Installation
# install(TARGETS 
#     speedometer
#     cpu_workload
#     memory_workload
#     io_workload
#     misbehaving_app
#     DESTINATION bin)