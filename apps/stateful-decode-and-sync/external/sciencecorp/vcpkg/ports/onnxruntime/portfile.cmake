vcpkg_check_features(OUT_FEATURE_OPTIONS FEATURE_OPTIONS
  FEATURES
    coreml          WITH_COREML
    cuda50          WITH_CUDA_50
    cuda52          WITH_CUDA_52
    cuda60          WITH_CUDA_60
    cuda61          WITH_CUDA_61
    cuda70          WITH_CUDA_70
    cuda75          WITH_CUDA_75
    cuda80          WITH_CUDA_80
    cuda86          WITH_CUDA_86
    tests           WITH_TESTS
)

vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO microsoft/onnxruntime
    REF "v${VERSION}"
    SHA512 f2fec4ded88da6bf67ae7d0aa3082736cb3b8ba29e723b5a516d7632b68ce02aed461f24d3e82cbab20757729e0ab45d736bd986c9b7395f2879b16a091c12a1
    HEAD_REF main
    PATCHES
        "patches/fix-ep-headers.patch"
        "patches/use-env-build-dir.patch"
        "patches/use-build-log-file.patch"
        "patches/fix-eigen-sha1.patch"
)

# Configuration Options
#   Onnxruntime provides a build.sh / build.py script that configures and builds the project via CMake & Make.
#   We'll run this instead of calling CMake directly.


# We need to tell vcpkg's `vcpkg_cmake_install` that we used CMake / Make to configure the project.
set(generator "Unix Makefiles")
set(Z_VCPKG_CMAKE_GENERATOR ${generator} CACHE INTERNAL "The generator which was used to configure CMake.")

set(build_command "${SOURCE_PATH}/build.sh")
set(build_options --parallel --skip_submodule_sync --cmake_generator ${generator} --allow_running_as_root)
set(cmake_extra_defines "ONNXRUNTIME_VERSION=${VERSION}")

if (NOT WITH_TESTS)
    set(build_options ${build_options} --skip_tests)
endif()

# if (VCPKG_LIBRARY_LINKAGE STREQUAL "dynamic")
#     set(build_options ${build_options} --build_shared_lib)
# endif()

# TODO(antoniae): bad, but only the shared build produces cmake config files
set(build_options ${build_options} --build_shared_lib)

if (VCPKG_TARGET_IS_OSX)
    set(osx_arch ${VCPKG_TARGET_ARCHITECTURE})
    if (osx_arch STREQUAL "x64")
        set(osx_arch "x86_64")
    endif()

    set(build_options ${build_options} --osx_arch ${osx_arch})
    set(cmake_extra_defines ${cmake_extra_defines} CMAKE_OSX_ARCHITECTURES=${osx_arch})

    if (WITH_COREML)
        set(build_options ${build_options} --use_coreml)
    endif()

elseif (VCPKG_TARGET_IS_LINUX)
    # linux_arch provides mapping from architecture name to name used in triplet for cuDNN installation location
    if (VCPKG_TARGET_ARCHITECTURE STREQUAL "x64")
        set(target_arch "x64")
        set(linux_arch "x84_64")
    elseif (VCPKG_TARGET_ARCHITECTURE STREQUAL "arm64")
        set(target_arch "aarch64")
        set(linux_arch "aarch64")
        # Need to pass --arm64 to onnxruntime's build.sh -> ci_build.py to target arm64 and enable cross-compiling
        set(build_options ${build_options} --arm64)
    else()
        message(FATAL_ERROR "Unsupported architecture: ${VCPKG_TARGET_ARCHITECTURE}")
    endif()

    # used downstream on onnxruntime's CMake
    list(APPEND cmake_extra_defines "CMAKE_SYSTEM_PROCESSOR=${linux_arch}")
    list(APPEND cmake_extra_defines "CMAKE_SYSTEM_NAME=Linux")

    if (VCPKG_TARGET_ARCHITECTURE STREQUAL "arm64")
        # Detect host architecture and check if we're cross-compiling
        execute_process(COMMAND uname -m OUTPUT_VARIABLE HOST_ARCH OUTPUT_STRIP_TRAILING_WHITESPACE)
        message(STATUS "Host architecture: ${HOST_ARCH}")

        if (NOT HOST_ARCH STREQUAL "aarch64")
            message(STATUS "Cross-compiling from ${HOST_ARCH} to arm64")

            if (EXISTS "/usr/bin/${target_arch}-linux-gnu-gcc")
                message(STATUS "Using ${target_arch}-linux-gnu cross-compiler")
                # onnxruntime's CMake needs to know the location of our cross-compilers
                list(APPEND cmake_extra_defines "CMAKE_C_COMPILER=/usr/bin/${target_arch}-linux-gnu-gcc")
                list(APPEND cmake_extra_defines "CMAKE_CXX_COMPILER=/usr/bin/${target_arch}-linux-gnu-g++")
            else()
                message(WARNING "No ARM64 cross-compiler found. Build may fail.")
            endif()
        endif()
    endif()

    set(cuda_architectures "")
    foreach(cuda_option IN ITEMS
        WITH_CUDA_50
        WITH_CUDA_52
        WITH_CUDA_60
        WITH_CUDA_61
        WITH_CUDA_70
        WITH_CUDA_75
        WITH_CUDA_80
        WITH_CUDA_86)
        message(STATUS "- CUDA OPTION: ${cuda_option}: ${${cuda_option}}")
        if (${${cuda_option}})
            set(cuda_enabled ON)
            string(REPLACE "WITH_CUDA_" "" cuda_arch "${cuda_option}")
            message(STATUS "- enabling cuda ${cuda_option}: ${${cuda_option}} ${cuda_arch}")
            list(APPEND cuda_architectures "${cuda_arch}")
        endif()
    endforeach()
    
    if (cuda_enabled)
        list(JOIN cuda_architectures "\\;" cuda_architectures)
        list(APPEND cmake_extra_defines "CUDA_ARCHITECTURES=${cuda_architectures}")

        # onnxruntime needs to know where our cuda and cuDNN installations are
        set(CUDA_HOME "/usr/local/cuda")
        if (target_arch STREQUAL "aarch64")
            set(CUDNN_HOME "/usr/lib/aarch64-linux-gnu/")
        elseif (target_arch STREQUAL "x64")
            set(CUDNN_HOME "/usr/lib/x86_64-linux-gnu/")
        else()
            set(CUDNN_HOME "/usr/lib/${linux_arch}-linux-gnu/")
        endif()
        set(build_options ${build_options} --cuda_home ${CUDA_HOME} --cudnn_home ${CUDNN_HOME} --use_cuda)
    endif()
endif()


# Configure & Build

set(building_message "Configuring and building ${TARGET_TRIPLET}")

if(NOT DEFINED arg_LOGFILE_BASE)
    set(arg_LOGFILE_BASE "config-${TARGET_TRIPLET}")
endif()

foreach(build_type IN ITEMS debug release)
    if(NOT DEFINED VCPKG_BUILD_TYPE OR "${VCPKG_BUILD_TYPE}" STREQUAL "${build_type}")
        if("${build_type}" STREQUAL "debug")
            set(short_build_type "dbg")
            set(config "Debug")
            set(prefix_dir "/debug")
        else()
            set(short_build_type "rel")
            set(config "Release")
            set(prefix_dir "")
        endif()

        message(STATUS "${building_message}-${short_build_type}")

        set(build_dir "${CURRENT_BUILDTREES_DIR}/${TARGET_TRIPLET}-${short_build_type}")
        file(REMOVE_RECURSE "${build_dir}")
        file(MAKE_DIRECTORY "${build_dir}")

        set(ENV{ONNXRUNTIME_BUILD_DIR} "${build_dir}")
        vcpkg_list(SET build_command
            ${build_command}
            --build_dir ${build_dir}
            --config ${config}
            ${build_options}
            --cmake_extra_defines
            ${cmake_extra_defines}
            "CMAKE_INSTALL_PREFIX=${CURRENT_PACKAGES_DIR}${prefix_dir}"
        )

        message(STATUS "Running ${build_command}")

        vcpkg_execute_required_process(
            COMMAND ${build_command}
            WORKING_DIRECTORY "${build_dir}"
            LOGNAME "${arg_LOGFILE_BASE}-${short_build_type}"
            SAVE_LOG_FILES
                CMakeCache.txt
                build.log
        )

        vcpkg_list(APPEND config_logs
            "${CURRENT_BUILDTREES_DIR}/${arg_LOGFILE_BASE}-${short_build_type}-out.log"
            "${CURRENT_BUILDTREES_DIR}/${arg_LOGFILE_BASE}-${short_build_type}-err.log"
        )
    endif()
endforeach()

# Install
vcpkg_cmake_install()

vcpkg_cmake_config_fixup(CONFIG_PATH lib/cmake/${PORT})

# License
vcpkg_install_copyright(FILE_LIST "${SOURCE_PATH}/LICENSE")

# Usage
configure_file("${CMAKE_CURRENT_LIST_DIR}/usage" "${CURRENT_PACKAGES_DIR}/share/${PORT}/usage" COPYONLY)
