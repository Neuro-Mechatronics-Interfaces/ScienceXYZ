add_executable(scifi2-hub-manager-control-tests
    ring_buffer_test.cpp
)

add_executable(scifi2-hub-manager-multipart-drain-tests
    multipart_drain_test.cpp
)

add_executable(scifi2-hub-manager-protocol-tests
    protocol_test.cpp
)

add_executable(scifi2-hub-manager-control-state-tests
    control_state_test.cpp
)

add_executable(scifi2-hub-manager-task-state-tests
    task_state_test.cpp
    ../src/task_state.cpp
)

add_executable(scifi2-hub-manager-task-timeline-tests
    task_timeline_test.cpp
    ../src/task_timeline.cpp
)

add_executable(scifi2-hub-manager-task-timeline-adapter-tests
    task_timeline_adapter_test.cpp
    ../src/task_timeline.cpp
    ../src/task_timeline_adapter.cpp
)

# Host task-recorder writer. SDK- and HDF5-free: it drives the persistence seam
# (RecordSink) from the value types, and the test exercises it with an in-memory
# fake sink. clock_estimator.cpp supplies AffineClockEstimator::epochs().
add_executable(scifi2-hub-manager-task-recorder-writer-tests
    task_recorder_writer_test.cpp
    ../src/task_timeline.cpp
    ../src/task_recorder_writer.cpp
    ../src/clock_estimator.cpp
)

add_executable(scifi2-hub-manager-mlp-tests
    mlp_test.cpp
    ../src/mlp.cpp
)

add_executable(scifi2-hub-manager-fit-worker-tests
    fit_worker_test.cpp
    ../src/mlp.cpp
)

add_executable(scifi2-hub-manager-feature-worker-tests
    feature_worker_test.cpp
    ../src/mpf_features.cpp
)

# Exo link worker + serial-port seam. SDK-independent: the worker is driven
# against an in-process fake SerialPort with firmware-shaped replies, so no
# hardware and no nml_hand_exo SDK are required.
add_executable(scifi2-hub-manager-exo-link-tests
    exo_link_test.cpp
    ../src/exo_link.cpp
    ../src/serial_port.cpp
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
add_custom_target(scifi2-hub-manager-wireless-protobuf-generated)
generate_protobufs(
    TARGET scifi2-hub-manager-wireless-protobuf-generated
    OUT_PROTO_DIR WIRELESS_PROTO_OUT_DIR
    PROTO_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/../proto"
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/wireless/v1/wireless_batch.proto"
)
set(WIRELESS_PROTO_SOURCE
    "${WIRELESS_PROTO_OUT_DIR}/wireless/v1/wireless_batch.pb.cc")

add_executable(scifi2-hub-manager-wireless-ingress-tests
    wireless_ingress_test.cpp
    ../src/wireless_ingress.cpp
    "${WIRELESS_PROTO_SOURCE}"
)

add_executable(scifi2-hub-manager-wireless-multisource-tests
    wireless_multisource_test.cpp
    ../src/clock_estimator.cpp
    ../src/wireless_source_adapter.cpp
    ../src/wireless_simulator.cpp
    "${WIRELESS_PROTO_SOURCE}"
)

generate_protobufs(
    TARGET scifi2-hub-manager-protocol-tests
    OUT_PROTO_DIR PROTO_OUT_DIR
    PROTO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/../proto/gui_control.proto"
)

target_compile_features(scifi2-hub-manager-control-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-multipart-drain-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-protocol-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-control-state-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-task-state-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-task-timeline-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-task-recorder-writer-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-mlp-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-fit-worker-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-feature-worker-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-wireless-ingress-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-wireless-multisource-tests PRIVATE cxx_std_20)
target_compile_features(scifi2-hub-manager-exo-link-tests PRIVATE cxx_std_20)
target_include_directories(scifi2-hub-manager-exo-link-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_link_libraries(scifi2-hub-manager-exo-link-tests PRIVATE Threads::Threads)
target_include_directories(scifi2-hub-manager-control-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-multipart-drain-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-protocol-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(scifi2-hub-manager-control-state-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(scifi2-hub-manager-task-state-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
# Cross-language canonical-hash anchor shared with the Python profile generator
# (client/scifi2_hub_manager/motion_profile.py). Passed as a compile
# definition so the test needs no working-directory assumption under ctest.
target_compile_definitions(scifi2-hub-manager-task-state-tests PRIVATE
    HUB_SPOKE_FIXTURE_PATH="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/hub_spoke_definition.json"
)
target_include_directories(scifi2-hub-manager-task-timeline-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${PROTO_OUT_DIR}
)
target_include_directories(scifi2-hub-manager-task-recorder-writer-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-mlp-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-fit-worker-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-feature-worker-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
)
target_include_directories(scifi2-hub-manager-wireless-ingress-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${WIRELESS_PROTO_OUT_DIR}
)
target_include_directories(scifi2-hub-manager-wireless-multisource-tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${WIRELESS_PROTO_OUT_DIR}
)
target_sources(scifi2-hub-manager-control-state-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)
target_sources(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE
    ${PROTO_OUT_DIR}/gui_control.pb.cc
)

target_link_libraries(scifi2-hub-manager-protocol-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(scifi2-hub-manager-control-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(scifi2-hub-manager-task-state-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(scifi2-hub-manager-fit-worker-tests PRIVATE Threads::Threads)
target_link_libraries(scifi2-hub-manager-feature-worker-tests PRIVATE Threads::Threads)
target_link_libraries(scifi2-hub-manager-wireless-ingress-tests PRIVATE protobuf::libprotobuf)
target_link_libraries(scifi2-hub-manager-wireless-multisource-tests PRIVATE protobuf::libprotobuf)

add_dependencies(scifi2-hub-manager-wireless-ingress-tests
    scifi2-hub-manager-wireless-protobuf-generated
)
add_dependencies(scifi2-hub-manager-wireless-multisource-tests
    scifi2-hub-manager-wireless-protobuf-generated
)

# protobuf_generate() attaches the generated source/header custom command to
# the protocol target. Make the SDK-independent state test wait for that
# generation step as well.
add_dependencies(scifi2-hub-manager-control-state-tests
    scifi2-hub-manager-protocol-tests
)
add_dependencies(scifi2-hub-manager-task-timeline-adapter-tests
    scifi2-hub-manager-protocol-tests
)

if(MSVC)
    target_compile_options(scifi2-hub-manager-control-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-protocol-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-control-state-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-task-state-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-task-timeline-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-task-recorder-writer-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-mlp-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-fit-worker-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-feature-worker-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-exo-link-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-wireless-ingress-tests PRIVATE /W4 /WX)
    target_compile_options(scifi2-hub-manager-wireless-multisource-tests PRIVATE /W4 /WX)
else()
    target_compile_options(scifi2-hub-manager-control-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-multipart-drain-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-protocol-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-control-state-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-task-state-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-task-timeline-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-task-timeline-adapter-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-task-recorder-writer-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-mlp-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-fit-worker-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-feature-worker-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-exo-link-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-wireless-ingress-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
    target_compile_options(scifi2-hub-manager-wireless-multisource-tests PRIVATE
        -Wall -Wextra -Wpedantic -Werror
    )
endif()

add_test(
    NAME scifi2-hub-manager-control
    COMMAND scifi2-hub-manager-control-tests
)
add_test(
    NAME scifi2-hub-manager-multipart-drain
    COMMAND scifi2-hub-manager-multipart-drain-tests
)
add_test(
    NAME scifi2-hub-manager-protocol
    COMMAND scifi2-hub-manager-protocol-tests
)
add_test(
    NAME scifi2-hub-manager-control-state
    COMMAND scifi2-hub-manager-control-state-tests
)
add_test(
    NAME scifi2-hub-manager-task-state
    COMMAND scifi2-hub-manager-task-state-tests
)
add_test(
    NAME scifi2-hub-manager-task-timeline
    COMMAND scifi2-hub-manager-task-timeline-tests
)
add_test(
    NAME scifi2-hub-manager-task-timeline-adapter
    COMMAND scifi2-hub-manager-task-timeline-adapter-tests
)
add_test(
    NAME scifi2-hub-manager-task-recorder-writer
    COMMAND scifi2-hub-manager-task-recorder-writer-tests
)
add_test(
    NAME scifi2-hub-manager-mlp
    COMMAND scifi2-hub-manager-mlp-tests
)
add_test(
    NAME scifi2-hub-manager-fit-worker
    COMMAND scifi2-hub-manager-fit-worker-tests
)
add_test(
    NAME scifi2-hub-manager-feature-worker
    COMMAND scifi2-hub-manager-feature-worker-tests
)
add_test(
    NAME scifi2-hub-manager-exo-link
    COMMAND scifi2-hub-manager-exo-link-tests
)
add_test(
    NAME scifi2-hub-manager-wireless-ingress
    COMMAND scifi2-hub-manager-wireless-ingress-tests
)
add_test(
    NAME scifi2-hub-manager-wireless-multisource
    COMMAND scifi2-hub-manager-wireless-multisource-tests
)
