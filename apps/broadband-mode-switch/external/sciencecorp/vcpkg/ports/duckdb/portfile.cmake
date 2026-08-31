if(VCPKG_TARGET_IS_OSX)
    if (VERSION STREQUAL "1.1.0")
        set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-osx-universal.zip")
        set(DUCKDB_SHA512 "7badf2548188e108821a5f4da73ae649c94c994b2808051024ec103f7fbaf7cc16ffc64dff2b3fdb4dcaf9b2dc4734e6bed6058dfa5dd60d1f138ee9c0d02c50")
    elseif (VERSION STREQUAL "1.1.3")
        set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-osx-universal.zip")
        set(DUCKDB_SHA512 "b3b8cdc9a61e088abb131155f33a2463ed7b686581c2565879571f8cc0f2776f2239477277c17744c79c88fdf513c5892b31700027aae9e0fbe7d2eceace1ebf")
    endif()
elseif(VCPKG_TARGET_IS_LINUX)
    if(VCPKG_TARGET_ARCHITECTURE STREQUAL "arm64")
        if (VERSION STREQUAL "1.1.0")
            set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-linux-aarch64.zip")
            set(DUCKDB_SHA512 "fc163d12f7b2f0bd635f239eb400545f87ae62cead38d178755d4947bfebe268a143485457197561edc3873ded67e0f78c660b79cf9bdf19227856989dc69dff")
        elseif (VERSION STREQUAL "1.1.3")
            set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-linux-aarch64.zip")
            set(DUCKDB_SHA512 "c0fe1a95c2c74980645319cabcc7cbb4e80a9bc5517cfa1ef05e32ab9f09a6576d1624db01f7fdd9424862de686cb3269e30f0408b09273faffa8360476b0d73")
        endif()
    else()
        if (VERSION STREQUAL "1.1.0")
            set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-linux-amd64.zip")
            set(DUCKDB_SHA512 "bfce08fa9d5e6b0c0f81968ed3fa415b6e262fea8bf8fa3dd2981ba9f854295afe6ba0167f1941476aef8354935b2254224bf3b56fce330c2aed3e606e3a1c0b")
        elseif (VERSION STREQUAL "1.1.3")
            set(DUCKDB_URL "https://github.com/duckdb/duckdb/releases/download/v${VERSION}/libduckdb-linux-amd64.zip")
            set(DUCKDB_SHA512 "214add5d768d0963a1882d036802a6a3272f3baff4b3832b324e2dc660b76e1b29495b541d8ba71d39c33a0f38e068cac8487e1f06a81a310a96be9e3b39fc00")
        endif()
    endif()
else()
    message(FATAL_ERROR "Unsupported platform")
endif()

vcpkg_download_distfile(
    ARCHIVE
    URLS ${DUCKDB_URL}
    FILENAME "duckdb-${VERSION}-${VCPKG_TARGET_ARCHITECTURE}.zip"
    SHA512 ${DUCKDB_SHA512}
)

vcpkg_extract_source_archive(
    SOURCE_PATH
    ARCHIVE "${ARCHIVE}"
    NO_REMOVE_ONE_LEVEL
)

file(INSTALL "${SOURCE_PATH}/duckdb.hpp" DESTINATION "${CURRENT_PACKAGES_DIR}/include")

message(STATUS "Installing DuckDB library to ${CURRENT_PACKAGES_DIR}/lib from ${SOURCE_PATH}")
if(VCPKG_TARGET_IS_OSX)
    file(INSTALL "${SOURCE_PATH}/libduckdb.dylib" DESTINATION "${CURRENT_PACKAGES_DIR}/lib")
    file(INSTALL "${SOURCE_PATH}/libduckdb.dylib" DESTINATION "${CURRENT_PACKAGES_DIR}/debug/lib")
else()
    file(INSTALL "${SOURCE_PATH}/libduckdb.so" DESTINATION "${CURRENT_PACKAGES_DIR}/lib")
    file(INSTALL "${SOURCE_PATH}/libduckdb.so" DESTINATION "${CURRENT_PACKAGES_DIR}/debug/lib")
endif()

# Install CMake config files
file(MAKE_DIRECTORY "${CURRENT_PACKAGES_DIR}/share/duckdb/cmake")

configure_file(
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Config.cmake.in"
    "${CURRENT_PACKAGES_DIR}/share/duckdb/cmake/DuckDBConfig.cmake"
    @ONLY
)

configure_file(
    "${CMAKE_CURRENT_LIST_DIR}/cmake/Targets.cmake.in"
    "${CURRENT_PACKAGES_DIR}/share/duckdb/cmake/DuckDBTargets.cmake"
    @ONLY
)

vcpkg_download_distfile(
    LICENSE_FILE
    URLS "https://github.com/duckdb/duckdb/blob/v${VERSION}/LICENSE"
    FILENAME "duckdb-${VERSION}-LICENSE"
    SKIP_SHA512
)

vcpkg_install_copyright(FILE_LIST "${LICENSE_FILE}")

configure_file("${CMAKE_CURRENT_LIST_DIR}/usage" "${CURRENT_PACKAGES_DIR}/share/${PORT}/usage" COPYONLY)
