cmake_minimum_required(VERSION 3.23)
project(sciencexyz_raw_recorder LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
include(CTest)
find_package(Protobuf REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(cppzmq CONFIG REQUIRED)
set(HDF5_NO_FIND_PACKAGE_CONFIG_FILE TRUE)
find_package(HDF5 REQUIRED COMPONENTS C)
find_package(Threads REQUIRED)
get_filename_component(ROOT "../.." ABSOLUTE)
set(APP "${ROOT}/apps/stateful-decode-and-sync")
set(API "${ROOT}/vendor/synapse-api")
set(CLIENT "${ROOT}/vendor/synapse-cpp")
set(GEN "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GEN}/science/synapse")
# Compile upstream sources without running its in-source protobuf generation.
# All wire code and gRPC use the same discovered protobuf dependency graph.
file(GLOB_RECURSE API_PROTOS "${API}/api/*.proto")
set(API_SOURCES)
foreach(PROTO IN LISTS API_PROTOS)
  file(RELATIVE_PATH REL "${API}" "${PROTO}")
  string(REGEX REPLACE "\\.proto$" "" NAME "${REL}")
  set(CC "${GEN}/science/synapse/${NAME}.pb.cc")
  set(H "${GEN}/science/synapse/${NAME}.pb.h")
  add_custom_command(OUTPUT "${CC}" "${H}"
    COMMAND protobuf::protoc --proto_path=${API} --cpp_out=${GEN}/science/synapse "${PROTO}"
    DEPENDS ${API_PROTOS} protobuf::protoc VERBATIM)
  list(APPEND API_SOURCES "${CC}" "${H}")
endforeach()
set(GRPC_CC "${GEN}/science/synapse/api/synapse.grpc.pb.cc")
set(GRPC_H "${GEN}/science/synapse/api/synapse.grpc.pb.h")
add_custom_command(OUTPUT "${GRPC_CC}" "${GRPC_H}"
  COMMAND protobuf::protoc --proto_path=${API} --grpc_out=${GEN}/science/synapse
    --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin> "${API}/api/synapse.proto"
  DEPENDS ${API_PROTOS} protobuf::protoc gRPC::grpc_cpp_plugin VERBATIM)
file(GLOB_RECURSE CLIENT_SOURCES "${CLIENT}/src/science/synapse/*.cpp")
add_library(recorder_synapse STATIC ${CLIENT_SOURCES} ${API_SOURCES} "${GRPC_CC}" "${GRPC_H}")
target_include_directories(recorder_synapse PUBLIC "${GEN}" "${GEN}/science/synapse" "${CLIENT}/include" PRIVATE "${CLIENT}/src")
target_link_libraries(recorder_synapse PUBLIC protobuf::libprotobuf gRPC::grpc++ cppzmq Threads::Threads)
add_custom_command(OUTPUT "${GEN}/gui_control.pb.cc" "${GEN}/gui_control.pb.h"
  COMMAND protobuf::protoc --proto_path=${APP}/proto --cpp_out=${GEN} "${APP}/proto/gui_control.proto"
  DEPENDS "${APP}/proto/gui_control.proto" protobuf::protoc VERBATIM)
add_library(recorder_core STATIC
  "${GEN}/gui_control.pb.cc" "${GEN}/gui_control.pb.h"
  "${APP}/src/task_timeline.cpp" "${APP}/src/task_timeline_adapter.cpp"
  "${APP}/src/task_recorder_writer.cpp" "${APP}/src/clock_estimator.cpp"
  "${APP}/src/hdf5_record_sink.cpp")
target_include_directories(recorder_core PUBLIC "${APP}/src" "${GEN}" ${HDF5_INCLUDE_DIRS})
target_link_libraries(recorder_core PUBLIC protobuf::libprotobuf ${HDF5_C_LIBRARIES})
add_executable(task-recorder "${APP}/tools/task_recorder_main.cpp")
target_link_libraries(task-recorder PRIVATE recorder_core recorder_synapse)
execute_process(COMMAND git describe --always --dirty WORKING_DIRECTORY "${ROOT}"
  OUTPUT_VARIABLE REVISION OUTPUT_STRIP_TRAILING_WHITESPACE)
target_compile_definitions(task-recorder PRIVATE SCIENCEXYZ_REVISION="${REVISION}")
if(BUILD_TESTING)
  add_executable(raw-recording-test raw_recording_test.cpp)
  target_link_libraries(raw-recording-test PRIVATE recorder_core recorder_synapse)
  add_test(NAME raw-recording-roundtrip COMMAND raw-recording-test "${CMAKE_CURRENT_BINARY_DIR}/roundtrip.h5")
  if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_definitions(raw-recording-test PRIVATE TEST_HDF5_FAILURES)
    target_link_options(raw-recording-test PRIVATE -Wl,--wrap=H5Dwrite -Wl,--wrap=H5Fflush)
    add_executable(recorder-loopback-test recorder_loopback_test.cpp)
    target_link_libraries(recorder-loopback-test PRIVATE recorder_core recorder_synapse)
    foreach(MODE stop idle loss)
      add_test(NAME recorder-loopback-${MODE} COMMAND recorder-loopback-test
        $<TARGET_FILE:task-recorder> "${CMAKE_CURRENT_BINARY_DIR}/loopback-${MODE}.h5" ${MODE})
      set_tests_properties(recorder-loopback-${MODE} PROPERTIES TIMEOUT 20)
    endforeach()
  endif()
endif()
