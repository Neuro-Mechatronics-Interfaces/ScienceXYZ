cmake_minimum_required(VERSION 3.20.1)
project(scifi2-hub-manager)

option(BUILD_FOR_ARM64 "Build for ARM64 architecture" OFF)
option(USE_LOCAL_SDK "Use locally built SDK instead of system installation" OFF)
set(LOCAL_SDK_PATH "app-sdk" CACHE PATH "Path to local SDK build directory")
option(BUILD_DEVICE_APP "Build the Synapse device application" ON)

include(CTest)
include("cmake/protos.cmake")
find_package(Threads REQUIRED)

if(BUILD_DEVICE_APP)
if (BUILD_FOR_ARM64 AND NOT CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  message(STATUS "Cross compiling for ARM64")
  set(CMAKE_SYSTEM_NAME Linux)
  set(CMAKE_SYSTEM_PROCESSOR aarch64)
  set(CMAKE_C_COMPILER /usr/bin/aarch64-linux-gnu-gcc)
  set(CMAKE_CXX_COMPILER /usr/bin/aarch64-linux-gnu-g++)
  set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
endif()

# Required packages
find_package(cppzmq REQUIRED)
find_package(Protobuf CONFIG REQUIRED)
find_package(spdlog REQUIRED CONFIG)
find_package(iir REQUIRED CONFIG)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)

if(USE_LOCAL_SDK)
    if(NOT LOCAL_SDK_PATH)
        message(FATAL_ERROR "USE_LOCAL_SDK is ON but LOCAL_SDK_PATH is not set")
    endif()
    message(STATUS "Using local SDK from: ${LOCAL_SDK_PATH}")

    set(SDK_LIB_PATH "${LOCAL_SDK_PATH}")
    set(SDK_LIB_NAME "synapse-app-sdk")
    link_directories(${SDK_LIB_PATH})

    set(SYNAPSE_APP_SDK_LIB ${SDK_LIB_NAME})

    include_directories("${LOCAL_SDK_PATH}/include")
else()
    find_library(SYNAPSE_APP_SDK_LIB NAMES synapse-app-sdk libsynapse-app-sdk.so.0.1.0)
endif()

message(STATUS "SYNAPSE_APP_SDK_LIB: ${SYNAPSE_APP_SDK_LIB}")

add_executable(scifi2-hub-manager
    ${CMAKE_CURRENT_SOURCE_DIR}/src/main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/synthetic_source.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/mpf_features.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/mlp.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/clock_estimator.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/wireless_ingress.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/wireless_source_adapter.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/wireless_simulator.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/wireless_zmq_reader.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/task_state.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/serial_port.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/usb_cdc_port.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/libusb_backend.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/src/exo_link.cpp
)

generate_protobufs(
    TARGET scifi2-hub-manager
    OUT_PROTO_DIR PROTO_OUT_DIR
    PROTO_DIRS
        "${CMAKE_CURRENT_SOURCE_DIR}/external/sciencecorp/synapse-api"
        "${CMAKE_CURRENT_SOURCE_DIR}/proto"
)

# Be strict about the standard
set_target_properties(scifi2-hub-manager PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
    POSITION_INDEPENDENT_CODE ON
)

target_include_directories(scifi2-hub-manager
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/src
   PRIVATE
        ${PROTO_OUT_DIR}
)

target_compile_options(scifi2-hub-manager PRIVATE -Wno-psabi)

# Link dependencies
target_link_libraries(scifi2-hub-manager
    PUBLIC
        cppzmq
        protobuf::libprotobuf
        iir::iir
        Threads::Threads
        ${SYNAPSE_APP_SDK_LIB}

    PRIVATE
        spdlog::spdlog
        PkgConfig::LIBUSB
)

# Resolve transitive NEEDED libs of libsynapse-app-sdk.so (e.g. libonnxruntime.so.1.16.3)
# from scifi-headstage-shared-libraries, which installs to /opt/scifi/lib in the builder image.
target_link_options(scifi2-hub-manager PRIVATE -Wl,-rpath-link,/opt/scifi/lib)

# Packaging configuration
set(CPACK_PACKAGE_DIRECTORY "${CMAKE_SOURCE_DIR}/dist")
include(CPack)
endif()

if(BUILD_TESTING)
    # The device App (find_package above) is built inside the Synapse SDK image,
    # whose protobuf (>=22) ships CMake config files. The SDK-free host-tests
    # build may run against a distro protobuf (e.g. Ubuntu's 3.21.x) that offers
    # only the module-mode FindProtobuf.cmake. Both create the same
    # protobuf::libprotobuf target the tests link, so prefer CONFIG and fall back
    # to module mode rather than hard-failing on a host without config files.
    find_package(Protobuf CONFIG QUIET)
    if(NOT Protobuf_FOUND)
        find_package(Protobuf MODULE REQUIRED)
    endif()
    add_subdirectory(tests)
endif()

# Host-side consumer tools (task-timeline recorder). Gated OFF by default; the
# subdirectory early-returns unless BUILD_TASK_RECORDER is ON, so this is inert
# for both the device App build and the SDK-free host-tests build.
add_subdirectory(tools)
