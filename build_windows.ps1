$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Missing .venv. Create it first with:"
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller"
    exit 1
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt pyinstaller

$BuildDir = Join-Path $RootDir "build"
$DistDir = Join-Path $RootDir "dist"
foreach ($Path in @($BuildDir, $DistDir)) {
    if ((Test-Path $Path) -and ((Resolve-Path $Path).Path.StartsWith($RootDir))) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

& $Python -m PyInstaller --noconfirm dms_fastgraph.spec

$AppDir = Join-Path $DistDir "DMS Fastgraph Beta"
$ZipPath = Join-Path $DistDir "DMS Fastgraph Beta-windows-x64.zip"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path $AppDir -DestinationPath $ZipPath

Write-Host ""
Write-Host "Built Windows app:"
Write-Host "  $AppDir"
Write-Host "Packaged zip:"
Write-Host "  $ZipPath"
