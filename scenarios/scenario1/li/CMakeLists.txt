add_executable(speedometer_trigger speedometer_trigger.c)
target_include_directories(speedometer_trigger PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../common)
target_link_libraries(speedometer_trigger
PRIVATE
    Threads::Threads
    shmlib
)

add_executable(li_speedometer li_speedometer.c)
target_include_directories(li_speedometer PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../common)
target_link_libraries(li_speedometer
    PRIVATE
        Threads::Threads
        shmlib
        m   # Math library
        rt  # POSIX real-time extensions
)


install(TARGETS 
    speedometer_trigger
    li_speedometer
    DESTINATION ${CMAKE_INSTALL_BINDIR})


