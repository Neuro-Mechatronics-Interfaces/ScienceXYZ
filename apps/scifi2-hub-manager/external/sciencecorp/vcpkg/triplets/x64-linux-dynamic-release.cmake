set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE dynamic)

set(VCPKG_CMAKE_SYSTEM_NAME Linux)

set(VCPKG_FIXUP_ELF_RPATH ON)

set(VCPKG_BUILD_TYPE release)

# szip 2.1.1 (pulled in transitively by hdf5) uses try_run() in its CMake
# ConfigureChecks to probe _DEFAULT_SOURCE and large-file support. This x64 triplet
# builds host tools natively so try_run would work here, but the seeds are harmless
# and keep both triplets consistent should a cross scenario reuse this triplet. On
# x64/glibc both probes succeed, so seed them to 0. See ScienceXYZ MISTAKES.md 2026-09-04.
set(VCPKG_CMAKE_CONFIGURE_OPTIONS
  "-DHAVE_DEFAULT_SOURCE_RUN=0"
  "-DHAVE_DEFAULT_SOURCE_RUN__TRYRUN_OUTPUT="
  "-DTEST_LFS_WORKS_RUN=0"
  "-DTEST_LFS_WORKS_RUN__TRYRUN_OUTPUT=")
