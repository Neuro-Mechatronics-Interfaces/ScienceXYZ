add_executable(stateful-decode-and-sync-control-tests
    ring_buffer_test.cpp
)

add_executable(stateful-decode-and-sync-multipart-drain-tests
    multipart_drain_test.cpp
)

add_executable(stateful-decode-and-sync-protocol-tests
    protocol_test.cpp
)

add_executable(stateful-decode-and-sync-control-state-tests
    control_state_test.cpp
)

add_executable(stateful-decode-and-sync-mlp-tests
    mlp_test.cpp
    ../src/mlp.cpp
)

add_executable(stateful-decode-and-sync-fit-worker-tests
    fit_worker_test.cpp
    ../src/mlp.cpp
)

add_executable(stateful-decode-and-sync-feature-worker-tests
    feature_worker_test.cpp
    ../src/mpf_features.cpp
)

generate_protobufs(
    TARGET stateful-decode-and-sync-protocol-tests
    OUT_PROTO_DIR PROTO_OUT_DIR
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/gui_control.proto"
)

target_compile_features(stateful-decode-and-sync-control-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-multipart-drain-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-protocol-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-control-state-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-mlp-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-fit-worker-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-feature-worker-tests PRIVATE cxx_std_20)
target_include_directories(stateful-decode-and-sync-control-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-multipart-drain-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-protocol-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(stateful-decode-and-sync-control-state-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(stateful-decode-and-sync-mlp-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-fit-worker-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-feature-worker-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_sources(stateful-decode-and-sync-control-state-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)

target_link_libraries(stateful-decode-and-sync-protocol-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-control-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-fit-worker-tests PRIVATE Threads::Threads)
target_link_libraries(stateful-decode-and-sync-feature-worker-tests PRIVATE Threads::Threads)

# protobuf_generate() attaches the generated source/header custom command to
# the protocol target. Make the SDK-independent state test wait for that
# generation step as well.
add_dependencies(stateful-decode-and-sync-control-state-tests
    stateful-decode-and-sync-protocol-tests
)

if(MSVC)
    target_compile_options(stateful-decode-and-sync-control-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-protocol-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-control-state-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-mlp-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-fit-worker-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-feature-worker-tests PRIVATE /W4 /WX)
else()
    target_compile_options(stateful-decode-and-sync-control-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-multipart-drain-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-protocol-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-control-state-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-mlp-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-fit-worker-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-feature-worker-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
endif()

add_test(
    NAME stateful-decode-and-sync-control
    COMMAND stateful-decode-and-sync-control-tests
)
add_test(
    NAME stateful-decode-and-sync-multipart-drain
    COMMAND stateful-decode-and-sync-multipart-drain-tests
)
add_test(
    NAME stateful-decode-and-sync-protocol
    COMMAND stateful-decode-and-sync-protocol-tests
)
add_test(
    NAME stateful-decode-and-sync-control-state
    COMMAND stateful-decode-and-sync-control-state-tests
)
add_test(
    NAME stateful-decode-and-sync-mlp
    COMMAND stateful-decode-and-sync-mlp-tests
)
add_test(
    NAME stateful-decode-and-sync-fit-worker
    COMMAND stateful-decode-and-sync-fit-worker-tests
)
add_test(
    NAME stateful-decode-and-sync-feature-worker
    COMMAND stateful-decode-and-sync-feature-worker-tests
)
