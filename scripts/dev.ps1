# One-shot dev bootstrap for Windows (PowerShell).
# Creates the venv OUTSIDE OneDrive, installs deps, generates sample data,
# and prints the commands to start each process.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $env:USERPROFILE ".venvs\dq-sentinel"

if (-not (Test-Path $venv)) {
    Write-Host "Creating venv at $venv (outside OneDrive on purpose)..."
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"

& $py -m pip install --upgrade pip --quiet
& $py -m pip install -e "$repo\backend[dev]" --quiet
Write-Host "Backend deps installed."

if (-not (Test-Path "$repo\samples\shopdb.sqlite")) {
    & $py "$repo\data\generate_sample_data.py"
}

if (-not (Test-Path "$repo\.env")) {
    Copy-Item "$repo\.env.example" "$repo\.env"
    Write-Host "Created .env from template (add ANTHROPIC_API_KEY to enable AI features)."
}

Push-Location "$repo\frontend"
if (-not (Test-Path "node_modules")) { npm install }
Pop-Location

Write-Host ""
Write-Host "Ready. Start each in its own terminal:" -ForegroundColor Green
Write-Host "  1) API:      & `"$py`" -m uvicorn app.main:app --reload --port 8000 --app-dir `"$repo\backend`""
Write-Host "  2) Worker:   Push-Location `"$repo\backend`"; & `"$py`" -m app.worker"
Write-Host "  3) Frontend: Push-Location `"$repo\frontend`"; npm run dev"
Write-Host ""
Write-Host "UI: http://localhost:5173  Login: admin@example.com / admin123"
Write-Host "Sample source DSN: sqlite:///$(($repo -replace '\\','/'))/samples/shopdb.sqlite"
