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

# The app-level generate_protobufs() compiles this same proto successfully, so
# the file is present in the build sandbox. Its protoc invocations run with the
# app source directory as the working directory (that is what protobuf_generate
# uses); an absolute "/home/workspace/proto" is only stat-able relative to that
# location in synapsectl's build mount, not from this tests binary directory.
# Match that: run protoc from the app source dir and give it the include root
# and proto file as paths relative to that dir, so protoc's lexical proto_path
# prefix check succeeds and the include directory resolves.
get_filename_component(WIRELESS_APP_SOURCE_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/.." REALPATH)
set(WIRELESS_PROTO_REL_INCLUDE_DIR "proto")
set(WIRELESS_PROTO_REL_FILE "proto/wireless/v1/wireless_batch.proto")
set(WIRELESS_PROTO_FILE
    "${WIRELESS_APP_SOURCE_DIR}/${WIRELESS_PROTO_REL_FILE}")
set(WIRELESS_PROTO_OUT_DIR "${CMAKE_CURRENT_BINARY_DIR}/wireless-generated")
set(WIRELESS_PROTO_HEADER
    "${WIRELESS_PROTO_OUT_DIR}/wireless/v1/wireless_batch.pb.h")
set(WIRELESS_PROTO_SOURCE
    "${WIRELESS_PROTO_OUT_DIR}/wireless/v1/wireless_batch.pb.cc")
file(MAKE_DIRECTORY "${WIRELESS_PROTO_OUT_DIR}")

# Keep the generated header as an explicit output. The protobuf CMake helper
# attaches generated sources to a target, but parallel consumers can otherwise
# start compiling before the included header has been created.
add_custom_command(
    OUTPUT "${WIRELESS_PROTO_HEADER}" "${WIRELESS_PROTO_SOURCE}"
    COMMAND "${Protobuf_PROTOC_EXECUTABLE}"
        --cpp_out="${WIRELESS_PROTO_OUT_DIR}"
        -I"${WIRELESS_PROTO_REL_INCLUDE_DIR}"
        "${WIRELESS_PROTO_REL_FILE}"
    WORKING_DIRECTORY "${WIRELESS_APP_SOURCE_DIR}"
    DEPENDS "${WIRELESS_PROTO_FILE}"
    COMMENT "Generating wireless WirelessBatch protobuf sources"
    VERBATIM
)
add_custom_target(stateful-decode-and-sync-wireless-protobuf-generated
    DEPENDS "${WIRELESS_PROTO_HEADER}" "${WIRELESS_PROTO_SOURCE}"
)

add_executable(stateful-decode-and-sync-wireless-ingress-tests
    wireless_ingress_test.cpp
    ../src/wireless_ingress.cpp
    "${WIRELESS_PROTO_SOURCE}"
)

add_executable(stateful-decode-and-sync-wireless-multisource-tests
    wireless_multisource_test.cpp
    ../src/clock_estimator.cpp
    ../src/wireless_source_adapter.cpp
    ../src/wireless_simulator.cpp
    "${WIRELESS_PROTO_SOURCE}"
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
target_compile_features(stateful-decode-and-sync-wireless-ingress-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-wireless-multisource-tests PRIVATE cxx_std_20)
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
target_include_directories(stateful-decode-and-sync-wireless-ingress-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${WIRELESS_PROTO_OUT_DIR}
)
target_include_directories(stateful-decode-and-sync-wireless-multisource-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${WIRELESS_PROTO_OUT_DIR}
)
target_sources(stateful-decode-and-sync-control-state-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)

target_link_libraries(stateful-decode-and-sync-protocol-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-control-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-fit-worker-tests PRIVATE Threads::Threads)
target_link_libraries(stateful-decode-and-sync-feature-worker-tests PRIVATE Threads::Threads)
target_link_libraries(stateful-decode-and-sync-wireless-ingress-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-wireless-multisource-tests PRIVATE protobuf::libprotobuf)

add_dependencies(stateful-decode-and-sync-wireless-ingress-tests
    stateful-decode-and-sync-wireless-protobuf-generated
)
add_dependencies(stateful-decode-and-sync-wireless-multisource-tests
    stateful-decode-and-sync-wireless-protobuf-generated
)

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
    target_compile_options(stateful-decode-and-sync-wireless-ingress-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-wireless-multisource-tests PRIVATE /W4 /WX)
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
    target_compile_options(stateful-decode-and-sync-wireless-ingress-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-wireless-multisource-tests PRIVATE
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
add_test(
    NAME stateful-decode-and-sync-wireless-ingress
    COMMAND stateful-decode-and-sync-wireless-ingress-tests
)
add_test(
    NAME stateful-decode-and-sync-wireless-multisource
    COMMAND stateful-decode-and-sync-wireless-multisource-tests
)
