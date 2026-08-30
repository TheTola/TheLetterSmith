param(
    [switch]$Build,
    [switch]$ConfirmPackage,
    [switch]$Sign,
    [string]$FFmpegSourceDir
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "build_release.py"
$arguments = @("-3.13", $scriptPath, "--validate-only")

if ($Build) {
    $arguments = @("-3.13", $scriptPath, "--build")
    if ($ConfirmPackage) {
        $arguments += "--confirm-package"
    }
}
if ($Sign) {
    $arguments += "--sign"
}
if ($FFmpegSourceDir) {
    $arguments += @("--ffmpeg-source-dir", $FFmpegSourceDir)
}

Push-Location $projectRoot
try {
    & py @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
