#!/usr/bin/env bash
# Run inside the C++ builder, with this directory mounted at /work.
set -euo pipefail
cd "$(dirname "$0")"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_CXX_COMPILER=/usr/bin/aarch64-linux-gnu-g++ \
  -DCMAKE_TOOLCHAIN_FILE=/vcpkg/scripts/buildsystems/vcpkg.cmake \
  -DVCPKG_TARGET_TRIPLET=arm64-linux-dynamic-release \
  -DVCPKG_INSTALLED_DIR=/vcpkg/build/host/vcpkg_installed \
  -DVCPKG_MANIFEST_MODE=OFF
cmake --build build --parallel 4
(cd build && cpack -G DEB)
# The deploy client reads version from the filename, not Debian metadata.
# Fail the local build if either representation drifts.
package=build/scifi-axon-exo_0.2.0_arm64.deb
test "$(dpkg-deb -f "$package" Package)" = scifi-axon-exo
test "$(dpkg-deb -f "$package" Version)" = 0.2.0
test "$(dpkg-deb -f "$package" Architecture)" = arm64
test "$(dpkg-deb -f "$package" Section)" = synapse-peripherals
