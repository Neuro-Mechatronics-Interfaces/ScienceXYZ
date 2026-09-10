<#
.SYNOPSIS
  Build the native Windows task-recorder.exe so calibrate-gui / the Reactions
  bridge (Windows processes) can spawn it. The Linux ELF under
  build/raw-recorder cannot be launched by a Windows process (WinError 193);
  this produces a PE executable instead.

.DESCRIPTION
  Configures and builds host/recording with the MSVC toolchain and vcpkg
  dependencies (grpc/protobuf/abseil, cppzmq/zeromq, hdf5). The Linux-only test
  wrappers in the recorder CMake are already guarded by CMAKE_SYSTEM_NAME, so
  they are skipped on Windows; only the task-recorder target is built here.

  Prerequisites (verified present on this bench 2026-09-09):
    - Visual Studio 2022 (Community) with the C++ workload
    - vcpkg at $env:VCPKG_ROOT (C:\MyRepos\Libraries\vcpkg)
    - vcpkg install grpc cppzmq hdf5 --triplet x64-windows  (run once)

.EXAMPLE
  pwsh -File scripts/build-recorder-windows.ps1
  # then point calibrate-gui's "Recorder path" at build-win/raw-recorder/task-recorder.exe
#>
[CmdletBinding()]
param(
  [string]$BuildDir = "build-win/raw-recorder",
  [string]$Config   = "Release",
  [string]$VcpkgRoot = $env:VCPKG_ROOT,
  # x64-windows-static avoids the PROTOBUF_USE_DLLS map_field.h C2370 that the
  # dynamic x64-windows protobuf 25.1 triggers with MSVC. Install the deps for
  # the matching triplet first: `vcpkg install grpc cppzmq hdf5 --triplet <t>`.
  [string]$Triplet  = "x64-windows-static"
)
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source   = Join-Path $repoRoot "host/recording"

if (-not $VcpkgRoot) { $VcpkgRoot = "C:\MyRepos\Libraries\vcpkg" }
$toolchain = Join-Path $VcpkgRoot "scripts/buildsystems/vcpkg.cmake"
if (-not (Test-Path $toolchain)) {
  throw "vcpkg toolchain not found at $toolchain. Set -VcpkgRoot or `$env:VCPKG_ROOT."
}

# Enter the VS2022 x64 developer environment so cl/cmake/ninja are in scope.
$vsRoot = "C:\Program Files\Microsoft Visual Studio\2022\Community"
$devShell = Join-Path $vsRoot "Common7/Tools/Launch-VsDevShell.ps1"
if (-not (Test-Path $devShell)) { throw "Launch-VsDevShell.ps1 not found at $devShell" }
Write-Host "Entering VS2022 x64 developer environment..." -ForegroundColor Cyan
& $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null

# Prefer a standalone CMake over the one bundled with VS: CMake 3.29.5-msvc4
# (VS-bundled) emits a malformed C++20 module scan rule for Ninja
# ("CXX_SCAN__..._$Config: expected newline"). A standalone CMake generates
# correct dyndep rules. Fall back to whatever `cmake` the dev shell provides.
$cmake = "cmake"
$standalone = "C:\Program Files\CMake\bin\cmake.exe"
if (Test-Path $standalone) {
  $cmake = $standalone
  Write-Host "Using standalone CMake: $((& $cmake --version | Select-Object -First 1))" -ForegroundColor Cyan
}

$absBuild = Join-Path $repoRoot $BuildDir
Write-Host "Configuring $source -> $absBuild (vcpkg $Triplet)" -ForegroundColor Cyan
# The MSVC runtime linkage (static /MT for a *-static triplet, dynamic /MD
# otherwise) is set by vcpkg's toolchain from the triplet's VCPKG_CRT_LINKAGE, so
# it is NOT overridden here -- an explicit CMAKE_MSVC_RUNTIME_LIBRARY both fought
# the toolchain and (glued into -D...=$var) failed to expand under PowerShell.
# CMAKE_CXX_SCAN_FOR_MODULES=OFF: the recorder is plain C++20, no modules. With
# Ninja + MSVC, CMake otherwise emits a C++20 module-dependency scan rule that
# fails to generate ("CXX_SCAN__..._$Config: expected newline").
& $cmake -S $source -B $absBuild -G Ninja `
  -DCMAKE_BUILD_TYPE=$Config `
  -DCMAKE_TOOLCHAIN_FILE="$toolchain" `
  -DVCPKG_TARGET_TRIPLET=$Triplet `
  -DCMAKE_CXX_SCAN_FOR_MODULES=OFF `
  -DBUILD_TESTING=OFF
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed (exit $LASTEXITCODE)" }

Write-Host "Building task-recorder..." -ForegroundColor Cyan
& $cmake --build $absBuild --target task-recorder --config $Config
if ($LASTEXITCODE -ne 0) { throw "Build failed (exit $LASTEXITCODE)" }

$exe = Join-Path $absBuild "task-recorder.exe"
if (Test-Path $exe) {
  Write-Host "Built: $exe" -ForegroundColor Green
  Write-Host "Set calibrate-gui's 'Recorder path' field to this path." -ForegroundColor Green
} else {
  # Fail loudly with a nonzero exit so a background/CI run does not report success.
  Write-Error "Build reported success but $exe was not found; check the build tree."
  exit 1
}
