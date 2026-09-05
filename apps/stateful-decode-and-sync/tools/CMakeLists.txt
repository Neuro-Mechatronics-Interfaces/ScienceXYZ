# Prefer the standalone host/recording build so the host dependency graph stays
# independent of the deployed App and its SDK. Retain the opt-in entry point.
option(BUILD_TASK_RECORDER "Build the host-side raw/task HDF5 recorder" OFF)
if(BUILD_TASK_RECORDER)
  add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../../../host/recording"
                   "${CMAKE_CURRENT_BINARY_DIR}/host-recorder")
endif()
