add_executable(broadband-mode-switch-control-tests
    ring_buffer_test.cpp
)

add_executable(broadband-mode-switch-protocol-tests
    protocol_test.cpp
)

add_executable(broadband-mode-switch-control-state-tests
    control_state_test.cpp
)

add_executable(broadband-mode-switch-mlp-tests
    mlp_test.cpp
    ../src/mlp.cpp
)

add_executable(broadband-mode-switch-fit-worker-tests
    fit_worker_test.cpp
    ../src/mlp.cpp
)

generate_protobufs(
    TARGET broadband-mode-switch-protocol-tests
    OUT_PROTO_DIR PROTO_OUT_DIR
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/gui_control.proto"
)

target_compile_features(broadband-mode-switch-control-tests PRIVATE cxx_std_20)
target_compile_features(broadband-mode-switch-protocol-tests PRIVATE cxx_std_20)
target_compile_features(broadband-mode-switch-control-state-tests PRIVATE cxx_std_20)
target_compile_features(broadband-mode-switch-mlp-tests PRIVATE cxx_std_20)
target_compile_features(broadband-mode-switch-fit-worker-tests PRIVATE cxx_std_20)
target_include_directories(broadband-mode-switch-control-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(broadband-mode-switch-protocol-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(broadband-mode-switch-control-state-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(broadband-mode-switch-mlp-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(broadband-mode-switch-fit-worker-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_sources(broadband-mode-switch-control-state-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)

target_link_libraries(broadband-mode-switch-protocol-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(broadband-mode-switch-control-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(broadband-mode-switch-fit-worker-tests PRIVATE Threads::Threads)

# protobuf_generate() attaches the generated source/header custom command to
# the protocol target. Make the SDK-independent state test wait for that
# generation step as well.
add_dependencies(broadband-mode-switch-control-state-tests
    broadband-mode-switch-protocol-tests
)

if(MSVC)
    target_compile_options(broadband-mode-switch-control-tests PRIVATE /W4 /WX)
    target_compile_options(broadband-mode-switch-protocol-tests PRIVATE /W4 /WX)
    target_compile_options(broadband-mode-switch-control-state-tests PRIVATE /W4 /WX)
    target_compile_options(broadband-mode-switch-mlp-tests PRIVATE /W4 /WX)
    target_compile_options(broadband-mode-switch-fit-worker-tests PRIVATE /W4 /WX)
else()
    target_compile_options(broadband-mode-switch-control-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(broadband-mode-switch-protocol-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(broadband-mode-switch-control-state-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(broadband-mode-switch-mlp-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(broadband-mode-switch-fit-worker-tests PRIVATE
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
add_test(
    NAME broadband-mode-switch-control-state
    COMMAND broadband-mode-switch-control-state-tests
)
add_test(
    NAME broadband-mode-switch-mlp
    COMMAND broadband-mode-switch-mlp-tests
)
add_test(
    NAME broadband-mode-switch-fit-worker
    COMMAND broadband-mode-switch-fit-worker-tests
)
