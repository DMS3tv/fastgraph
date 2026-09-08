$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Missing .venv. Create it first with:"
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r requirements.lock"
    exit 1
}

# requirements.lock is the fully pinned build environment (app deps, test deps
# and PyInstaller) so release builds are reproducible. Regenerate it after an
# intentional upgrade with:
#   .venv\Scripts\python.exe -m pip freeze > requirements.lock
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.lock

$PngIcon = Join-Path $RootDir "fastgraph icon.png"
$IcoIcon = Join-Path $RootDir "fastgraph.ico"
if (-not (Test-Path $PngIcon)) {
    Write-Error "Missing icon source: $PngIcon"
}

$env:FASTGRAPH_PNG_ICON = $PngIcon
$env:FASTGRAPH_ICO_ICON = $IcoIcon
@'
import os
from pathlib import Path
from PyQt6.QtGui import QImage

png_path = Path(os.environ["FASTGRAPH_PNG_ICON"])
ico_path = Path(os.environ["FASTGRAPH_ICO_ICON"])
image = QImage(str(png_path))
if image.isNull():
    raise SystemExit(f"Unable to load icon source: {png_path}")
if not image.save(str(ico_path), "ICO"):
    raise SystemExit(f"Unable to write Windows icon: {ico_path}")
'@ | & $Python -
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$BuildDir = Join-Path $RootDir "build"
$DistDir = Join-Path $RootDir "dist"
foreach ($Path in @($BuildDir, $DistDir)) {
    if ((Test-Path $Path) -and ((Resolve-Path $Path).Path.StartsWith($RootDir))) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

& $Python -m PyInstaller --noconfirm dms_fastgraph.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$AppDir = Join-Path $DistDir "FastGraph Beta"
# The app folder keeps its display name; the zip does not, so release upload
# and checksum tooling never has to quote a filename with a space in it.
$ZipPath = Join-Path $DistDir "FastGraph-Beta-windows-x64.zip"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

# Compress-Archive is a cmdlet, so it never sets $LASTEXITCODE. Catch its
# terminating error instead, then confirm the zip is actually on disk.
try {
    Compress-Archive -Path $AppDir -DestinationPath $ZipPath -ErrorAction Stop
} catch {
    Write-Host "Failed to package the app folder into a zip:"
    Write-Host "  $($_.Exception.Message)"
    exit 1
}
if (-not (Test-Path $ZipPath)) {
    Write-Host "Compress-Archive reported success but $ZipPath does not exist."
    exit 1
}

Write-Host ""
Write-Host "Built Windows app:"
Write-Host "  $AppDir"
Write-Host "Packaged zip:"
Write-Host "  $ZipPath"
