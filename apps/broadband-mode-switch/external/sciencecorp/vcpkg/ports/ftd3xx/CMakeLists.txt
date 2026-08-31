cmake_minimum_required(VERSION 3.23)
project(ftd3xx)

option(BUILD_SHARED_LIBS "Build shared libraries" OFF)

if(BUILD_SHARED_LIBS)
    if(APPLE)
        set(SHARED_LIB_NAME "libftd3xx.dylib")
    else()
        set(SHARED_LIB_NAME "libftd3xx.so")
    endif()

    set(LIB_PATH "${CMAKE_CURRENT_SOURCE_DIR}/${SHARED_LIB_NAME}")
    add_library(ftd3xx SHARED IMPORTED)
else()
    set(LIB_PATH "${CMAKE_CURRENT_SOURCE_DIR}/libftd3xx-static.a")
    add_library(ftd3xx STATIC IMPORTED)
endif()

set_target_properties(ftd3xx PROPERTIES
    IMPORTED_LOCATION ${LIB_PATH}
    INTERFACE_INCLUDE_DIRECTORIES ${CMAKE_CURRENT_SOURCE_DIR}
)

install(FILES ${LIB_PATH} DESTINATION lib)

file(GLOB_RECURSE FTD3XX_HEADERS "${CMAKE_CURRENT_SOURCE_DIR}/*.h")
install(FILES ${FTD3XX_HEADERS} DESTINATION include/ftd3xx)

configure_file(cmake/Config.cmake.in "${CMAKE_CURRENT_BINARY_DIR}/ftd3xxConfig.cmake" @ONLY)
if (BUILD_SHARED_LIBS)
  configure_file(cmake/Targets-shared.cmake.in "${CMAKE_CURRENT_BINARY_DIR}/ftd3xxTargets.cmake" @ONLY)
else()
  configure_file(cmake/Targets-static.cmake.in "${CMAKE_CURRENT_BINARY_DIR}/ftd3xxTargets.cmake" @ONLY)
endif()

install(FILES "${CMAKE_CURRENT_BINARY_DIR}/ftd3xxConfig.cmake"
              "${CMAKE_CURRENT_BINARY_DIR}/ftd3xxTargets.cmake"
        DESTINATION share/ftd3xx
)
