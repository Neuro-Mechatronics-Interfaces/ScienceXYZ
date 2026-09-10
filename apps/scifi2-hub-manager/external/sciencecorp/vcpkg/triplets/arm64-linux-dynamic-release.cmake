set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE dynamic)

set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_BUILD_TYPE release)

# szip 2.1.1 (pulled in transitively by hdf5) uses try_run() in its CMake
# ConfigureChecks to probe _DEFAULT_SOURCE and large-file support. Cross-compiling
# (x86 host -> arm64) cannot execute the target test binary, so CMake hard-errors
# unless these result cache variables are pre-seeded. On arm64/glibc both probes
# succeed, so seed them to 0 (success) with empty run output. This is the actual
# blocker; see ScienceXYZ MISTAKES.md 2026-09-04.
set(VCPKG_CMAKE_CONFIGURE_OPTIONS
  "-DHAVE_DEFAULT_SOURCE_RUN=0"
  "-DHAVE_DEFAULT_SOURCE_RUN__TRYRUN_OUTPUT="
  "-DTEST_LFS_WORKS_RUN=0"
  "-DTEST_LFS_WORKS_RUN__TRYRUN_OUTPUT=")
