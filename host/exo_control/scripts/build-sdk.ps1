<# Build only: never runs synapsectl, adb, or device commands.
Run Windows builds from VS Developer PowerShell. Android builds need NDK + Ninja.
Dependencies must already be installed in the supplied vcpkg checkout. #>
[CmdletBinding()]
param(
  [ValidateSet('Windows','Android')][string]$Platform = 'Windows',
  [Parameter(Mandatory=$true)][string]$VcpkgRoot,
  [string]$Ndk = $env:ANDROID_NDK_HOME,
  [string]$BuildDir,
  [string]$CMake = 'cmake',
  [int]$Jobs = 6
)
$ErrorActionPreference = 'Stop'
$sdk = Split-Path -Parent $PSScriptRoot
if (!$BuildDir) { $BuildDir = Join-Path $sdk "build/$Platform" }
$triplet = if ($Platform -eq 'Android') { 'arm64-android' } else { 'x64-windows-static' }
$hostTools = Join-Path $VcpkgRoot 'installed/x64-windows/tools'
$argsList = @('-S', $sdk, '-B', $BuildDir, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
  "-DCMAKE_TOOLCHAIN_FILE=$VcpkgRoot/scripts/buildsystems/vcpkg.cmake",
  '-DVCPKG_MANIFEST_MODE=OFF', "-DVCPKG_TARGET_TRIPLET=$triplet",
  "-DEXO_PROTOC=$hostTools/protobuf/protoc.exe",
  "-DEXO_GRPC_PLUGIN=$hostTools/grpc/grpc_cpp_plugin.exe", '-DEXO_BUILD_JNI=ON')
if ($Platform -eq 'Android') {
  if (!(Test-Path -LiteralPath "$Ndk/build/cmake/android.toolchain.cmake")) { throw 'Supply -Ndk or ANDROID_NDK_HOME' }
  $env:ANDROID_NDK_HOME = $Ndk
  $argsList += @("-DVCPKG_CHAINLOAD_TOOLCHAIN_FILE=$Ndk/build/cmake/android.toolchain.cmake",
    '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-26', '-DANDROID_STL=c++_static', '-DBUILD_TESTING=OFF')
}
& $CMake @argsList
if ($LASTEXITCODE -ne 0) { throw 'Configure failed' }
& $CMake --build $BuildDir --parallel $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
if ($Platform -eq 'Windows') {
  & (Join-Path (Split-Path (Get-Command $CMake).Source) 'ctest.exe') --test-dir $BuildDir --output-on-failure
  if ($LASTEXITCODE -ne 0) { throw 'Local tests failed' }
}
& $CMake --install $BuildDir --prefix (Join-Path $BuildDir 'install')
if ($LASTEXITCODE -ne 0) { throw 'Install failed' }
Write-Host "Installed SDK in $BuildDir/install"
