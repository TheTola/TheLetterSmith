param(
    [switch]$Build,
    [switch]$ConfirmPackage
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "build_installer.py"
$arguments = @("-3.13", $scriptPath, "--validate-only")

if ($Build) {
    $arguments = @("-3.13", $scriptPath, "--build")
    if ($ConfirmPackage) {
        $arguments += "--confirm-package"
    }
}

Push-Location $projectRoot
try {
    & py @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
