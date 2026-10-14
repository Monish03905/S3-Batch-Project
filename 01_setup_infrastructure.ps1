#requires -Version 5.1
$ErrorActionPreference = "Stop"

$SetupScript = Join-Path $PSScriptRoot "01_setup_infrastructure_fixed.ps1"
if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) {
    throw "Infrastructure setup script not found: $SetupScript"
}

& $SetupScript
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
