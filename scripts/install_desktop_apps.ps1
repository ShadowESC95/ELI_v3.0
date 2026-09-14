# Redistribution-safe Start Menu shortcuts (stable eli-run + install_root pointer).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$env:PYTHONPATH = $Root
$env:ELI_PROJECT_ROOT = $Root
$env:ELI_PRODUCT_LINE = "v3"
& $Py -m eli.runtime.desktop_launchers install --root $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
