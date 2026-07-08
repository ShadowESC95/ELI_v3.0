# Install Start Menu shortcuts (Windows) for BOTH ELI surfaces, so they launch like any app:
#   • ELI v3.0            -> the desktop GUI
#   • ELI Server (Web App) -> the self-hosted web app for phone/tablet (LAN + token)
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install_desktop_apps.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Programs = [Environment]::GetFolderPath("Programs")   # Start Menu\Programs
New-Item -ItemType Directory -Force -Path $Programs | Out-Null
$ws = New-Object -ComObject WScript.Shell

# ELI v3.0 -> eli.bat (GUI)
$eliBat = Join-Path $Root "eli.bat"
$lnk1 = $ws.CreateShortcut((Join-Path $Programs "ELI v3.0.lnk"))
$lnk1.TargetPath = $eliBat
$lnk1.WorkingDirectory = $Root
$lnk1.Description = "Local, private AI assistant (desktop GUI)"
$lnk1.Save()

# ELI Server (Web App) -> powershell running eli_serve.ps1 -Lan, window stays open (URL+token)
$serve = Join-Path $Root "scripts\eli_serve.ps1"
$lnk2 = $ws.CreateShortcut((Join-Path $Programs "ELI Server (Web App).lnk"))
$lnk2.TargetPath = "powershell.exe"
$lnk2.Arguments = "-ExecutionPolicy Bypass -NoExit -File `"$serve`" -Lan"
$lnk2.WorkingDirectory = $Root
$lnk2.Description = "Self-hosted ELI web app - open from any device on your network"
$lnk2.Save()

# ELI Uninstall -> scripts\uninstall.ps1
$unins = Join-Path $Root "scripts\uninstall.ps1"
$lnk3 = $ws.CreateShortcut((Join-Path $Programs "ELI Uninstall.lnk"))
$lnk3.TargetPath = "powershell.exe"
$lnk3.Arguments = "-ExecutionPolicy Bypass -NoExit -File `"$unins`""
$lnk3.WorkingDirectory = $Root
$lnk3.Description = "Remove ELI (Start Menu shortcuts; optionally the whole install)"
if ($icon) { $lnk3.IconLocation = $icon }
$lnk3.Save()

Write-Host "[OK] Start Menu shortcuts installed:" -ForegroundColor Green
Write-Host "       - ELI v3.0              (desktop GUI)"
Write-Host "       - ELI Server (Web App) (prints the phone URL + token)"
Write-Host "       - ELI Uninstall        (remove ELI)"
Write-Host "     Find them in the Start Menu. Inference stays 100% local."
