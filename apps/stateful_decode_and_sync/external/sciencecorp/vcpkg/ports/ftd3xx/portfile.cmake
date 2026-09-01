set(no_remove_one_level NO_REMOVE_ONE_LEVEL)
if (VCPKG_TARGET_IS_LINUX)
    set(no_remove_one_level "")
    if (VCPKG_TARGET_ARCHITECTURE STREQUAL "arm64")
        # https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-arm-v6-hf-1.0.14.tgz
        # 92687cb0ccb297d64e6982a7052396fdc54f37edc26dcca1c16ee3534eaf555a8129912e220c543e380294827fc14aead7020e0c3c71050297ab3b50af82391b

        # https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-arm-v7_32-1.0.14.tgz
        # 7e5898c2a6ef4a3c3c3f8d2d5f140e71254fe8ffeb8533f962c32046ae875edec414c15856a76386d26620a0b8e0894351055c6282cb388252d46cb94d526496

        # https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-arm-v8-1.0.14.tgz
        # 9fab2f40b8e2933a8c8b1af7244b4b1f715e1817ea46765e66f680b946e7c22ab1ca8ba21e9e99b44875611acd603d40df963f67f22250dc4f14a4c4885cc6d3
        set(ARCHIVE_URL "https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-arm-v8-1.0.14.tgz")
        set(ARCHIVE_SHA512 "9fab2f40b8e2933a8c8b1af7244b4b1f715e1817ea46765e66f680b946e7c22ab1ca8ba21e9e99b44875611acd603d40df963f67f22250dc4f14a4c4885cc6d3")

    elseif (VCPKG_TARGET_ARCHITECTURE STREQUAL "x64")
        set(ARCHIVE_URL "https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-x86_64-1.0.14.tgz")
        set(ARCHIVE_SHA512 "8f8890fd34ccb465bc8bc4d5c03e4817193565fe8d21504faad33bbfb8b4c8054589438cf39b9c49845afede508ddf22298a4d7b4cf715b7eb00cad0ee37a713")

    elseif (VCPKG_TARGET_ARCHITECTURE STREQUAL "x86")
        set(ARCHIVE_URL "https://ftdichip.com/wp-content/uploads/2023/06/libftd3xx-linux-x86_32-1.0.14.tgz")
        set(ARCHIVE_SHA512 "abc8622560feadaa3b0fcaaa51e6f6c5c62d8920527bc0b5eb398ccca50e9d76cb8da16f8c7d1d8933de69455011da64258a68e0f9993d4eeff8832e13170176")
    endif()

elseif (VCPKG_TARGET_IS_OSX)
    set(ARCHIVE_URL "https://ftdichip.com/wp-content/uploads/2023/06/d3xx-osx.1.0.14.tgz")
    set(ARCHIVE_SHA512 "407c60438db4e68d308bbec9e900faad4b3d70c59b6ced3a0c707081b8a0d0533e91fb0565b6696fb95a7450f00ed341cdbeaf71e606b87b936d84e860aa3923")

endif()


vcpkg_download_distfile(
    ARCHIVE
    URLS ${ARCHIVE_URL}
    SHA512 ${ARCHIVE_SHA512}
    FILENAME "ftd3xx.tgz"
)

vcpkg_extract_source_archive(
    SOURCE_PATH
    ARCHIVE "${ARCHIVE}"
    ${no_remove_one_level}
)

vcpkg_check_features(OUT_FEATURE_OPTIONS FEATURE_OPTIONS
  FEATURES
    examples   WITH_EXAMPLES
)

file(INSTALL
    "${CMAKE_CURRENT_LIST_DIR}/CMakeLists.txt"
    DESTINATION "${SOURCE_PATH}")
file(INSTALL
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Config.cmake.in"
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Targets-shared.cmake.in"
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Targets-static.cmake.in"
    DESTINATION "${SOURCE_PATH}/cmake")

vcpkg_cmake_configure(SOURCE_PATH "${SOURCE_PATH}")

vcpkg_cmake_install()

vcpkg_cmake_config_fixup()

# TODO(antoniae):
# the dylib is shipped with ID / install name that does not include @rpath
# AFAIK there's no way for a consuming project to find the dylib without setting DYLD_LIBRARY_PATH
# or using install_name_tool to change the ID to @rpath
# however... changing the ID to @rpath invalidates the signature
# this solution is really not ideal, there's probably a better way
if (VCPKG_LIBRARY_LINKAGE STREQUAL "dynamic")
    if(VCPKG_TARGET_IS_OSX OR VCPKG_TARGET_IS_IOS)
        find_program(INSTALL_NAME_TOOL install_name_tool
            HINTS /usr/bin /Library/Developer/CommandLineTools/usr/bin/
            REQUIRED
        )
        message(STATUS "Using install_name_tool: ${INSTALL_NAME_TOOL}")
        vcpkg_execute_required_process(
            COMMAND "${INSTALL_NAME_TOOL}" -id "@rpath/libftd3xx.dylib" "libftd3xx.dylib"
            WORKING_DIRECTORY "${CURRENT_PACKAGES_DIR}/debug/lib"
            LOGNAME "fix-rpath-dbg"
        )
        vcpkg_execute_required_process(
            COMMAND "${INSTALL_NAME_TOOL}" -id "@rpath/libftd3xx.dylib" "libftd3xx.dylib"
            WORKING_DIRECTORY "${CURRENT_PACKAGES_DIR}/lib"
            LOGNAME "fix-rpath-rel"
        )

        vcpkg_execute_required_process(
            COMMAND  codesign --force -s - ${CURRENT_PACKAGES_DIR}/debug/lib/libftd3xx.dylib
            WORKING_DIRECTORY "${CURRENT_PACKAGES_DIR}"
            LOGNAME "codesign-dbg"
        )
        vcpkg_execute_required_process(
            COMMAND  codesign --force -s - ${CURRENT_PACKAGES_DIR}/lib/libftd3xx.dylib
            WORKING_DIRECTORY "${CURRENT_PACKAGES_DIR}"
            LOGNAME "codesign-rel"
        )
    endif()
endif()

# Clean
file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include")

# License
vcpkg_install_copyright(FILE_LIST "${CMAKE_CURRENT_LIST_DIR}/LICENSE")

# Usage
file(INSTALL "${CMAKE_CURRENT_LIST_DIR}/usage" DESTINATION "${CURRENT_PACKAGES_DIR}/share/${PORT}")
