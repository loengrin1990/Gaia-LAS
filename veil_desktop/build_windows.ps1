$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.11 -m venv .build-venv
& .\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name Veil app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item README.md dist\Инструкция.md
Compress-Archive -Path dist\Veil.exe, dist\Инструкция.md -DestinationPath dist\Veil-Windows.zip -Force
Write-Output "Готово: $PSScriptRoot\dist\Veil-Windows.zip"
