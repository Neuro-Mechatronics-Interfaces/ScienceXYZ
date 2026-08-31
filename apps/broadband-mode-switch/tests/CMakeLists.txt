add_executable(broadband-mode-switch-control-tests
    ring_buffer_test.cpp
)

target_compile_features(broadband-mode-switch-control-tests PRIVATE cxx_std_20)
target_include_directories(broadband-mode-switch-control-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)

if(MSVC)
    target_compile_options(broadband-mode-switch-control-tests PRIVATE /W4 /WX)
else()
    target_compile_options(broadband-mode-switch-control-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
endif()

add_test(
    NAME broadband-mode-switch-control
    COMMAND broadband-mode-switch-control-tests
)
