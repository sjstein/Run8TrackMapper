<#
.SYNOPSIS
    Build the Run8 Map self-host bundle (dist/Run8MapHost) with PyInstaller and
    stage the operator-editable files beside the .exe.

.DESCRIPTION
    Run from the repo root:  .\build_bundle.ps1
    Produces dist/Run8MapHost/ ready to zip and hand to a server operator.

    (Bash users: build_bundle.sh does the same thing.)

.NOTES
    Must be run on Windows (PyInstaller builds for the OS it runs on).
    Native-command failures are detected via $LASTEXITCODE rather than
    $ErrorActionPreference='Stop' - the latter turns a program's normal stderr
    (e.g. the "not installed" probe) into a fatal error.
#>
[CmdletBinding()]
param(
    # Skip the PyInstaller build and only re-stage the operator files into an
    # existing dist/Run8MapHost (fast when iterating on config/README/bat).
    [switch]$StageOnly
)

Set-Location -Path $PSScriptRoot

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# --- pick a Python interpreter (prefer the project venv) --------------------
$py = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host "venv python not found at $py; falling back to 'python' on PATH." -ForegroundColor Yellow
    $py = 'python'
}
Write-Host "Using Python: $py"

$distApp = Join-Path $PSScriptRoot 'dist\Run8MapHost'

if (-not $StageOnly) {
    # --- ensure PyInstaller (probe exit code; its stderr is not an error) ---
    & $py -m pip show pyinstaller *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PyInstaller not installed; installing into the venv..." -ForegroundColor Cyan
        & $py -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller failed." }
    }

    # --- build --------------------------------------------------------------
    Write-Host "`nBuilding bundle with PyInstaller..." -ForegroundColor Cyan
    & $py -m PyInstaller run8maphost.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }
}

if (-not (Test-Path $distApp)) {
    Fail "Expected build output not found at $distApp. Run without -StageOnly first."
}

# --- stage the operator-editable files beside the exe -----------------------
Write-Host "`nStaging operator files into $distApp ..." -ForegroundColor Cyan
$staged = @(
    @{ Src = 'config-socal.ini';              Dst = 'config-socal.ini' },
    @{ Src = 'areas_socal.ini';               Dst = 'areas_socal.ini' },
    @{ Src = 'packaging\README-operator.txt'; Dst = 'README.txt' },
    @{ Src = 'packaging\Start Map.bat';       Dst = 'Start Map.bat' }
)
foreach ($f in $staged) {
    $src = Join-Path $PSScriptRoot $f.Src
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $distApp $f.Dst) -Force
        Write-Host "  + $($f.Dst)"
    } else {
        Write-Host "  ! missing source, skipped: $($f.Src)" -ForegroundColor Yellow
    }
}

# --- zip the folder into the release artifact + a sha256 --------------------
$ver = & $py -c "import version; print(version.__version__)"
$zip = Join-Path $PSScriptRoot ("dist\Run8MapHost-{0}.zip" -f $ver)
Write-Host "`nZipping release artifact..." -ForegroundColor Cyan
& $py -c "import shutil; shutil.make_archive(r'dist\Run8MapHost-$ver', 'zip', root_dir='dist', base_dir='Run8MapHost')"
if ($LASTEXITCODE -ne 0) { Fail "zip step failed." }
$sha = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
"$sha  Run8MapHost-$ver.zip" | Set-Content -Path "$zip.sha256" -NoNewline
Write-Host "  sha256: $sha"

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  Bundle folder : $distApp"
Write-Host "  Release zip   : $zip  (+ .sha256)  <- attach to the GitHub Release (see packaging/RELEASING.md)"
Write-Host "Smoke-test the exe:"
Write-Host "  cd `"$distApp`"; .\Run8MapHost.exe config-socal.ini --host 127.0.0.1 --port 8001 --world `"<autosave path>`""
