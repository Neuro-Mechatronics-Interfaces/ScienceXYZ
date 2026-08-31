add_executable(broadband-mode-switch-control-tests
    ring_buffer_test.cpp
)

add_executable(broadband-mode-switch-protocol-tests
    protocol_test.cpp
)

generate_protobufs(
    TARGET broadband-mode-switch-protocol-tests
    OUT_PROTO_DIR PROTO_OUT_DIR
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/gui_control.proto"
)

target_compile_features(broadband-mode-switch-control-tests PRIVATE cxx_std_20)
target_compile_features(broadband-mode-switch-protocol-tests PRIVATE cxx_std_20)
target_include_directories(broadband-mode-switch-control-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(broadband-mode-switch-protocol-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)

target_link_libraries(broadband-mode-switch-protocol-tests PRIVATE protobuf::libprotobuf)

if(MSVC)
    target_compile_options(broadband-mode-switch-control-tests PRIVATE /W4 /WX)
    target_compile_options(broadband-mode-switch-protocol-tests PRIVATE /W4 /WX)
else()
    target_compile_options(broadband-mode-switch-control-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(broadband-mode-switch-protocol-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
endif()

add_test(
    NAME broadband-mode-switch-control
    COMMAND broadband-mode-switch-control-tests
)
add_test(
    NAME broadband-mode-switch-protocol
    COMMAND broadband-mode-switch-protocol-tests
)
