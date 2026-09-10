cmake_minimum_required(VERSION 3.15) # Choose the version depending on your requirements

project(mcap)

option(BUILD_SHARED_LIBS "Build shared libraries" ON)

find_package(lz4 CONFIG REQUIRED)
find_package(zstd CONFIG REQUIRED)

add_library(${PROJECT_NAME} INTERFACE)

set(LIBS_SHARED_OR_STATIC zstd::libzstd_shared)
if (NOT BUILD_SHARED_LIBS)
  set(LIBS_SHARED_OR_STATIC zstd::libzstd_static)
endif()

target_include_directories(${PROJECT_NAME} INTERFACE 
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/cpp/mcap/include>
  $<INSTALL_INTERFACE:include>
)

target_link_libraries(${PROJECT_NAME}
  INTERFACE
  ${LIBS_SHARED_OR_STATIC}
  lz4::lz4
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/cpp/mcap/include/ DESTINATION include)

install(TARGETS ${PROJECT_NAME} EXPORT ${PROJECT_NAME}Targets)
install(EXPORT ${PROJECT_NAME}Targets
  FILE ${PROJECT_NAME}Targets.cmake
  NAMESPACE mcap::
  DESTINATION share/${PROJECT_NAME}
)

include(CMakePackageConfigHelpers)
write_basic_package_version_file(
  "mcapConfigVersion.cmake"
  VERSION 1.3.0
  COMPATIBILITY AnyNewerVersion
)

configure_package_config_file(${CMAKE_CURRENT_SOURCE_DIR}/cmake/Config.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}Config.cmake"
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/${PROJECT_NAME}
)

install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}Config.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/${PROJECT_NAME}ConfigVersion.cmake"
  DESTINATION share/${PROJECT_NAME}
)
