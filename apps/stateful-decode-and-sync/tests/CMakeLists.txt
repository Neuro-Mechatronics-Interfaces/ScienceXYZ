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

add_executable(stateful-decode-and-sync-task-state-tests
    task_state_test.cpp
    ../src/task_state.cpp
)

add_executable(stateful-decode-and-sync-task-timeline-tests
    task_timeline_test.cpp
    ../src/task_timeline.cpp
)

add_executable(stateful-decode-and-sync-task-timeline-adapter-tests
    task_timeline_adapter_test.cpp
    ../src/task_timeline.cpp
    ../src/task_timeline_adapter.cpp
)

# Host task-recorder writer. SDK- and HDF5-free: it drives the persistence seam
# (RecordSink) from the value types, and the test exercises it with an in-memory
# fake sink. clock_estimator.cpp supplies AffineClockEstimator::epochs().
add_executable(stateful-decode-and-sync-task-recorder-writer-tests
    task_recorder_writer_test.cpp
    ../src/task_timeline.cpp
    ../src/task_recorder_writer.cpp
    ../src/clock_estimator.cpp
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

# Generate the wireless WirelessBatch protobuf via the same generate_protobufs()
# helper the app target and the gui_control protocol-test target use. That path
# is known to work in this build sandbox; a hand-rolled protoc add_custom_command
# failed here regardless of absolute/relative path spelling or WORKING_DIRECTORY,
# because protobuf_generate() and a raw protoc invocation resolve the proto_path
# differently under synapsectl's build mount.
#
# Passing PROTO_DIRS *and* PROTO_FILES compiles only wireless_batch.proto (the
# PROTO_DIRS glob is skipped when PROTO_FILES is set) while keeping ../proto as
# the first include root, so the header is emitted as "wireless/v1/
# wireless_batch.pb.h" and the "#include \"wireless/v1/wireless_batch.pb.h\""
# in the sources resolves. Compiling only the one file also avoids re-emitting
# gui_control.pb.* into the shared ${CMAKE_CURRENT_BINARY_DIR}/include output
# dir, which would collide with the protocol-test generate_protobufs() call.
add_custom_target(stateful-decode-and-sync-wireless-protobuf-generated)
generate_protobufs(
    TARGET stateful-decode-and-sync-wireless-protobuf-generated
    OUT_PROTO_DIR WIRELESS_PROTO_OUT_DIR
    PROTO_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/../proto"
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/wireless/v1/wireless_batch.proto"
)
set(WIRELESS_PROTO_SOURCE
    "${WIRELESS_PROTO_OUT_DIR}/wireless/v1/wireless_batch.pb.cc")

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
target_compile_features(stateful-decode-and-sync-task-state-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-task-timeline-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE cxx_std_20)
target_compile_features(stateful-decode-and-sync-task-recorder-writer-tests PRIVATE cxx_std_20)
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
target_include_directories(stateful-decode-and-sync-task-state-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-task-timeline-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(stateful-decode-and-sync-task-recorder-writer-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
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
target_sources(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)

target_link_libraries(stateful-decode-and-sync-protocol-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-control-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(stateful-decode-and-sync-task-state-tests PRIVATE protobuf::libprotobuf)
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
add_dependencies(stateful-decode-and-sync-task-timeline-adapter-tests
    stateful-decode-and-sync-protocol-tests
)

if(MSVC)
    target_compile_options(stateful-decode-and-sync-control-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-protocol-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-control-state-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-task-state-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-task-timeline-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE /W4 /WX)
    target_compile_options(stateful-decode-and-sync-task-recorder-writer-tests PRIVATE /W4 /WX)
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
    target_compile_options(stateful-decode-and-sync-task-state-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-task-timeline-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-task-timeline-adapter-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(stateful-decode-and-sync-task-recorder-writer-tests PRIVATE
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
    NAME stateful-decode-and-sync-task-state
    COMMAND stateful-decode-and-sync-task-state-tests
)
add_test(
    NAME stateful-decode-and-sync-task-timeline
    COMMAND stateful-decode-and-sync-task-timeline-tests
)
add_test(
    NAME stateful-decode-and-sync-task-timeline-adapter
    COMMAND stateful-decode-and-sync-task-timeline-adapter-tests
)
add_test(
    NAME stateful-decode-and-sync-task-recorder-writer
    COMMAND stateful-decode-and-sync-task-recorder-writer-tests
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
