cmake_minimum_required(VERSION 3.20)
project(axon-exo VERSION 0.2.0 LANGUAGES CXX)
find_package(cppzmq REQUIRED)
find_package(Protobuf CONFIG REQUIRED)
find_package(spdlog CONFIG REQUIRED)
find_package(Threads REQUIRED)
find_library(SCIFI_PERIPHERAL_SDK NAMES scifi-peripheral-sdk REQUIRED)
add_library(axon_exo SHARED src/axon_exo.cpp)
set_target_properties(axon_exo PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON
  CXX_EXTENSIONS OFF PREFIX "" CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON
  INSTALL_RPATH "$ORIGIN/../axon-exo;/opt/scifi/lib")
target_compile_options(axon_exo PRIVATE -Wall -Wextra -Wno-psabi)
target_link_libraries(axon_exo PRIVATE cppzmq protobuf::libprotobuf spdlog::spdlog ${SCIFI_PERIPHERAL_SDK} Threads::Threads rt)
target_link_options(axon_exo PRIVATE -Wl,-rpath-link,/opt/scifi/lib -Wl,--no-undefined)
install(TARGETS axon_exo LIBRARY DESTINATION lib/scifi/plugins)
# Like the reference CLI package, carry the SDK used at build time. A private
# directory avoids conflicting with SDK files owned by axon_test_source's deb.
get_filename_component(SCIFI_SDK_REAL "${SCIFI_PERIPHERAL_SDK}" REALPATH)
install(FILES "${SCIFI_SDK_REAL}" DESTINATION lib/scifi/axon-exo
  RENAME libscifi-peripheral-sdk.so.0)
include(CTest)
if(BUILD_TESTING)
  add_executable(axon_exo_plugin_test tests/plugin_test.cpp)
  set_target_properties(axon_exo_plugin_test PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
  target_link_libraries(axon_exo_plugin_test PRIVATE cppzmq protobuf::libprotobuf
    spdlog::spdlog ${SCIFI_PERIPHERAL_SDK} ${CMAKE_DL_LIBS} Threads::Threads rt)
  target_link_options(axon_exo_plugin_test PRIVATE -Wl,-rpath-link,/opt/scifi/lib)
  add_test(NAME plugin COMMAND axon_exo_plugin_test $<TARGET_FILE:axon_exo>)
endif()
set(CPACK_GENERATOR DEB)
set(CPACK_PACKAGE_NAME scifi-axon-exo)
set(CPACK_PACKAGE_CONTACT "ScienceXYZ maintainers")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "NML Hand Exo Axon registration (no gateware)")
set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE arm64)
# synapsectl derives the RPC version from underscore-separated Debian names.
# CPack's generic name (...-0.1.0-Linux.deb) yields an empty RPC version.
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_DEBIAN_PACKAGE_SECTION synapse-peripherals)
set(CPACK_PACKAGING_INSTALL_PREFIX /usr)
include(CPack)
