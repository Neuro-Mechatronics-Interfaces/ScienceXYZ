cmake_minimum_required(VERSION 3.23)
project(sciencexyz_raw_recorder LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# This project uses no C++20 modules. Some CMake+Ninja+MSVC combinations emit a
# malformed C++20 module dependency-scan rule ("CXX_SCAN__..._$Config: expected
# newline"); the recorder has no modules to scan, so disable scanning outright.
# Set here (before any target) so generation honours it regardless of a buggy
# cache default. Harmless on toolchains that never emitted the scan rule.
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)
include(CTest)
# Protobuf: the Linux distribution ships only module-mode FindProtobuf; a vcpkg
# protobuf on Windows (especially the *-static triplet) ships CONFIG files and
# module mode cannot locate its libs (Could NOT find Protobuf: missing
# Protobuf_LIBRARIES). Prefer CONFIG on Windows, keep module mode on Linux. Both
# provide the protobuf::libprotobuf / protobuf::protoc targets used below.
if(WIN32)
  find_package(Protobuf CONFIG REQUIRED)
else()
  find_package(Protobuf REQUIRED)
endif()
find_package(gRPC CONFIG REQUIRED)
find_package(cppzmq CONFIG REQUIRED)
# The Linux distribution build discovers HDF5 in module mode (its -dev package
# ships only FindHDF5.cmake). A vcpkg HDF5 on Windows ships CONFIG files and
# pulls in zlib/szip that module mode misses, so prefer CONFIG there. Keep the
# Linux path exactly as before.
if(WIN32)
  find_package(HDF5 CONFIG REQUIRED COMPONENTS C)
  # vcpkg's config exposes imported targets (hdf5::hdf5-shared/-static) but does
  # not always populate HDF5_C_LIBRARIES/HDF5_INCLUDE_DIRS the way module mode
  # does. Normalize to the variables the targets below already consume.
  if(NOT HDF5_C_LIBRARIES)
    if(TARGET hdf5::hdf5-shared)
      set(HDF5_C_LIBRARIES hdf5::hdf5-shared)
    elseif(TARGET hdf5::hdf5-static)
      set(HDF5_C_LIBRARIES hdf5::hdf5-static)
    elseif(TARGET hdf5::hdf5)
      set(HDF5_C_LIBRARIES hdf5::hdf5)
    endif()
  endif()
else()
  set(HDF5_NO_FIND_PACKAGE_CONFIG_FILE TRUE)
  find_package(HDF5 REQUIRED COMPONENTS C)
endif()
find_package(Threads REQUIRED)
get_filename_component(ROOT "../.." ABSOLUTE)
set(APP "${ROOT}/apps/scifi2-hub-manager")
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
  add_executable(task-profile-check task_profile_check.cpp "${APP}/src/task_state.cpp")
  target_link_libraries(task-profile-check PRIVATE recorder_core)
  add_test(NAME calibration-task-profile COMMAND task-profile-check "${ROOT}/config/calibration-task-profile.json")
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
