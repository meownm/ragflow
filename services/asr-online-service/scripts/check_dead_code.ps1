$ErrorActionPreference = "Stop"

Push-Location "$PSScriptRoot\.."
try {
    poetry install
    poetry run ruff check src tests
    poetry run vulture src tests vulture_allowlist.py --min-confidence 80
}
catch {
    Write-Host "Dead-code check failed: $($_.Exception.Message)"
    exit 1
}
finally {
    Pop-Location
}
