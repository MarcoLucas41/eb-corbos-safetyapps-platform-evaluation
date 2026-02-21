cmake_minimum_required(VERSION 3.10)
project(SpeedometerRT VERSION 1.0 LANGUAGES CXX)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Compiler flags for optimization and warnings
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra -O2")
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")
set(CMAKE_CXX_FLAGS_DEBUG "-g -O0")

# Find required libraries
find_package(Threads REQUIRED)

# Main speedometer executable
add_executable(speedometer speedometer.cpp)
target_link_libraries(speedometer
    PRIVATE
        Threads::Threads
        rt  # POSIX real-time extensions
)

# Interference workload applications
add_executable(cpu_workload cpu_workload.cpp)
target_link_libraries(cpu_workload
    PRIVATE
        Threads::Threads
)

add_executable(memory_workload memory_workload.cpp)
target_link_libraries(memory_workload
    PRIVATE
        Threads::Threads
)

add_executable(io_workload io_workload.cpp)
target_link_libraries(io_workload
    PRIVATE
        Threads::Threads
)

add_executable(misbehaving_app misbehaving_app.cpp)
target_link_libraries(misbehaving_app
    PRIVATE
        Threads::Threads
)

# Installation
install(TARGETS 
    speedometer
    cpu_workload
    memory_workload
    io_workload
    misbehaving_app
    DESTINATION bin)

# Print build information
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "C++ standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "Compiler: ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
