# Build the standalone OneCompute worker exe for managed-machine pilots.
# Produces dist\onecompute-worker.exe and prints its SHA-256 (for Defender allow-listing /
# the IT sanction doc). SIGN it with the corporate code-signing cert before distribution:
#     signtool sign /fd SHA256 /tr <timestamp-url> /td SHA256 /a dist\onecompute-worker.exe
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Output "Installing pyinstaller (build-only; not a runtime dependency)..."
uv pip install pyinstaller 2>&1 | Select-Object -Last 1

Write-Output "Building onecompute-worker.exe ..."
uv run pyinstaller --noconfirm --onefile --name onecompute-worker `
  --paths src `
  --hidden-import pynvml `
  --hidden-import psutil `
  --collect-submodules worker `
  --collect-submodules isolation `
  --collect-submodules jobkit `
  --collect-submodules contracts `
  --collect-submodules trust `
  scripts/worker_entry.py 2>&1 | Select-Object -Last 12

$exe = "dist\onecompute-worker.exe"
if (Test-Path $exe) {
    $hash = (Get-FileHash $exe -Algorithm SHA256).Hash
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Output ""
    Write-Output "BUILT: $exe ($size MB)"
    Write-Output "SHA256: $hash"
    "$hash *onecompute-worker.exe" | Out-File "dist\onecompute-worker.exe.sha256" -Encoding ascii
    Write-Output "(unsigned build - sign with the corporate cert, then re-hash for the allow-list)"
} else {
    Write-Output "BUILD FAILED: $exe not found"
}
