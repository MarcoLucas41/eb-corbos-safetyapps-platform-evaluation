
# High integrity applications must be modified, for the linux kernel
# to recognize them as high integrity applications.
function(target_enable_hi target)
  add_custom_command(
    TARGET ${target}
    POST_BUILD
    COMMAND lisa-elf-enabler $<TARGET_FILE:${target}>
  )
endfunction()

add_executable(speedometer speedometer.c)

# Main speedometer executable
target_link_libraries(speedometer
    PRIVATE
        Threads::Threads
        m
        rt  # POSIX real-time extensions
)

target_enable_hi(speedometer)

install(TARGETS speedometer DESTINATION /)
