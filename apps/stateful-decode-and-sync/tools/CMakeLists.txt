# Host-side task-timeline recorder consumer.
#
# This target is gated OFF by default and is NOT part of the SDK-free host-tests
# build. It links three things that build unit testing deliberately excludes:
#
#   * vendor/synapse-cpp     the Synapse Tap client (science::synapse::Tap)
#   * libhdf5 (C API)        via src/hdf5_record_sink.cpp (implementation pending
#                            a project HDF5 dependency decision)
#   * the gui_control + synapse-api protobufs
#
# Enable it once an HDF5 dependency mechanism is selected and the Hdf5RecordSink
# backend is implemented:
#
#   cmake -DBUILD_TASK_RECORDER=ON \
#         -Dsynapse_DIR=<path/to/vendor/synapse-cpp/install>/lib/cmake/synapse ...
#
# vendor/synapse-cpp installs a CMake package named "synapse" exporting the
# imported target science::synapse (see vendor/synapse-cpp/CMakeLists.txt).
#
# The recorder's value logic (TaskRecorderHdf5Writer) is already built and
# unit-tested in the host-tests build against an in-memory FakeRecordSink, so
# turning this target on wires up only the tap I/O and HDF5 serialization.

option(BUILD_TASK_RECORDER
    "Build the host-side task-timeline HDF5 recorder consumer" OFF)

if(NOT BUILD_TASK_RECORDER)
    return()
endif()

find_package(HDF5 REQUIRED C)
find_package(Protobuf CONFIG REQUIRED)

# synapse::Tap. vendor/synapse-cpp installs the "synapse" package exporting the
# science::synapse imported target; synapse_DIR overrides discovery.
find_package(synapse CONFIG REQUIRED)

add_executable(task-recorder
    task_recorder_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/task_timeline.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/task_timeline_adapter.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/task_recorder_writer.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/clock_estimator.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/hdf5_record_sink.cpp
)

# Reuse the app's protobuf codegen for gui_control.proto and the synapse-api
# datatype.proto (BroadbandFrame), matching the app target's proto_path roots.
generate_protobufs(
    TARGET task-recorder
    OUT_PROTO_DIR TASK_RECORDER_PROTO_OUT_DIR
    PROTO_DIRS
        "${CMAKE_CURRENT_SOURCE_DIR}/../external/sciencecorp/synapse-api"
        "${CMAKE_CURRENT_SOURCE_DIR}/../proto"
)

set_target_properties(task-recorder PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
)

target_include_directories(task-recorder PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${TASK_RECORDER_PROTO_OUT_DIR}
    ${HDF5_INCLUDE_DIRS}
)

target_link_libraries(task-recorder PRIVATE
    science::synapse
    protobuf::libprotobuf
    ${HDF5_C_LIBRARIES}
)
