vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO foxglove/mcap
    REF "releases/cpp/v1.3.0"
    SHA512 5083878127bc4010b24aae79fbb13014794bcc2632dc77c74ba61556c3f6ec1c46bb095b2f8d96ef0b9803eebdd701cdf7cba610f21da635fe4e81a21b867bde
    HEAD_REF main
)

file(INSTALL
    "${CMAKE_CURRENT_LIST_DIR}/CMakeLists.txt"
    DESTINATION "${SOURCE_PATH}")
file(INSTALL
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Config.cmake.in"
    DESTINATION "${SOURCE_PATH}/cmake")

vcpkg_cmake_configure(SOURCE_PATH "${SOURCE_PATH}")

vcpkg_cmake_install()

vcpkg_install_copyright(FILE_LIST "${SOURCE_PATH}/LICENSE")

configure_file("${CMAKE_CURRENT_LIST_DIR}/usage" "${CURRENT_PACKAGES_DIR}/share/${PORT}/usage" COPYONLY)
