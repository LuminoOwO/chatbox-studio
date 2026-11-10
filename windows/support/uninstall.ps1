# Chatbox Studio - uninstaller (started by Uninstall.bat from a temp copy).
$ErrorActionPreference = 'Continue'
$AppName = 'Chatbox Studio'
$Root    = Join-Path $env:LOCALAPPDATA $AppName
$Config  = Join-Path $env:APPDATA $AppName

Write-Host ''
Write-Host "  Uninstalling $AppName" -ForegroundColor White

Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force
Start-Sleep -Seconds 1

foreach ($l in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName Setup.lnk"))) {
    Remove-Item $l -Force -ErrorAction SilentlyContinue
}
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ChatboxStudio' -Recurse -Force -ErrorAction SilentlyContinue

$keep = Read-Host '  Keep your settings and the downloaded translation model for a later reinstall? (Y/n)'
if ($keep -match '^[nN]') {
    Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Config -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host '  Removed everything.'
} else {
    foreach ($d in 'app', 'runtime', '.venv', 'tools', 'python', 'Uninstall.bat', 'uninstall.ps1',
                   'Start with console.bat', 'installed-version.txt', 'README.txt', 'CHANGELOG.md',
                   'components.json') {
        Remove-Item (Join-Path $Root $d) -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  Removed the app. Settings and models were kept in $Root and $Config."
}
Write-Host '  The Whisper speech model cache can be deleted from %USERPROFILE%\.cache\huggingface if you like.'
Write-Host ''
Write-Host '  Uninstall finished.' -ForegroundColor Green
