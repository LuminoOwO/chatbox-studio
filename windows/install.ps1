# Chatbox Studio - Windows installer and updater.
# Started by the setup .exe (-FromExe) or Install.bat (ZIP). Needs no admin rights and no Python: it downloads the official,
# signed Python from python.org into %LOCALAPPDATA%\Chatbox Studio\runtime.
# (Signed binaries matter: Windows 11 "Smart App Control" blocks unsigned programs.)
# Running it again with a newer package updates the app and keeps settings and models.
# It also takes over an install of "VRC Voice Translator" (the old name, versions 1.4-1.8).
# After Python is ready it opens the setup window, where you pick the parts you want.
#   -Defaults : no window, install the recommended parts (no models)
#   -FromExe  : started by Chatbox-Studio-X.Y-Windows-Setup.exe (no console; the exe shows
#               the output and closes when the setup window opens)

param([switch]$Defaults, [switch]$FromExe)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # makes downloads much faster in PowerShell 5
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppName  = 'Chatbox Studio'
$RegKey   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ChatboxStudio'
$OldName  = 'VRC Voice Translator'
$OldRoot  = Join-Path $env:LOCALAPPDATA $OldName
$OldConf  = Join-Path $env:APPDATA $OldName
$OldKey   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCVoiceTranslator'
$Conf     = Join-Path $env:APPDATA $AppName
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src      = Join-Path $Here 'app'
$Support  = Join-Path $Here 'support'
$Root     = Join-Path $env:LOCALAPPDATA $AppName
$AppDir   = Join-Path $Root 'app'
$Runtime  = Join-Path $Root 'runtime'
$PyW      = Join-Path $Runtime 'pythonw.exe'
$Py       = Join-Path $Runtime 'python.exe'
$VerFile  = Join-Path $Root 'installed-version.txt'
$HashFile = Join-Path $Runtime 'base.hash'
$PyVer    = '3.12.10'
$PyZipUrl = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip"
$GetPip   = 'https://bootstrap.pypa.io/get-pip.py'

$Again = if ($FromExe) { 'run the Chatbox Studio installer again' } else { 'run Install.bat again' }

function Step($text) { Write-Host ''; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ''; Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

function Fail-Blocked($detail) {
    Write-Host ''
    Write-Host 'ERROR: Windows "Smart App Control" blocked a file the app needs:' -ForegroundColor Red
    Write-Host "  $detail"
    Write-Host ''
    Write-Host '  Smart App Control only lets programs run that Microsoft already knows. Some of the'
    Write-Host '  AI libraries this app uses (numpy, CTranslate2, ...) are not signed, so it blocks them.'
    Write-Host ''
    Write-Host '  To use the app, turn it off:'
    Write-Host '    Windows Security > App & browser control > Smart App Control settings > Off'
    Write-Host '  Note: Windows does not let you switch Smart App Control back ON later without'
    Write-Host '  resetting Windows. Your normal antivirus (Microsoft Defender) keeps running.'
    Write-Host ''
    Write-Host "  Afterwards, $Again."
    exit 1
}

function Test-Blocked($text) { return ($text -match 'Application Control|os error 4551|0x800711C7|4551') }

# Runs a program and returns @(exitcode, output) without stopping on stderr text.
function Invoke-Native($exe, [string[]]$arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $exe @arguments 2>&1 | ForEach-Object { "$_" } | Out-String
        $code = $LASTEXITCODE
    } catch {
        $out = $_.Exception.Message
        $code = 1
    } finally {
        $ErrorActionPreference = $prev
    }
    return @($code, $out)
}

if (-not (Test-Path (Join-Path $Src 'app.py'))) {
    Fail 'The app files are missing. Extract the whole ZIP first (right-click > Extract All), then run Install.bat from the extracted folder.'
}
$Version = (Select-String -Path (Join-Path $Src 'version.py') -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$Old = $null
if (Test-Path $VerFile) { $Old = (Get-Content $VerFile -Raw).Trim() }
$Legacy = $null
if ((Test-Path $OldRoot) -or (Test-Path $OldConf)) {
    $Legacy = 'install'
    $lv = Join-Path $OldRoot 'installed-version.txt'
    if (Test-Path $lv) { $Legacy = (Get-Content $lv -Raw).Trim() + ' install' }
}

Write-Host ''
Write-Host "  $AppName $Version" -ForegroundColor White
if ($Legacy) {
    $lvText = ($Legacy -replace '\s*install$', '')
    if ($lvText) { $lvText = " $lvText" }
    Write-Host "  Updating your VRC Voice Translator$lvText - the app is now called Chatbox Studio."
    Write-Host '  Your settings, routes, filters and models are taken over.'
} elseif ($Old) {
    Write-Host "  Updating from version $Old. Your settings and models are kept."
} else {
    Write-Host "  Installing to $Root"
}

# --- 1. close a running copy (new or old name) so its files can be replaced -----------
$running = Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and ($_.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -or
                                 $_.Path.StartsWith($OldRoot, [StringComparison]::OrdinalIgnoreCase)) }
if ($running) {
    Step 'Closing the running app'
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# --- take over the old "VRC Voice Translator" install -------------------------------
if ($Legacy) {
    Step 'Moving your old install over'
    if (Test-Path $OldRoot) {
        $moved = $false
        if (-not (Test-Path $Root)) {
            try {                       # keeps Python, packages and models: no new download
                Move-Item -Path $OldRoot -Destination $Root -ErrorAction Stop
                $moved = $true
            } catch {
                Write-Host "  (Could not move the folder in one piece: $($_.Exception.Message))"
            }
        }
        if (-not $moved) {
            New-Item -ItemType Directory -Force -Path $Root | Out-Null
            $om = Join-Path $OldRoot 'models'
            $nm = Join-Path $Root 'models'
            if ((Test-Path $om) -and -not (Test-Path $nm)) {
                Copy-Item $om $nm -Recurse -Force -ErrorAction SilentlyContinue
            }
            Remove-Item $OldRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        foreach ($f in 'installed-version.txt', 'Uninstall.bat', 'uninstall.ps1') {
            Remove-Item (Join-Path $Root $f) -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path $OldConf) {
        if (-not (Test-Path (Join-Path $Conf 'settings.json'))) {
            New-Item -ItemType Directory -Force -Path $Conf | Out-Null
            Copy-Item (Join-Path $OldConf '*') $Conf -Recurse -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $OldConf -Recurse -Force -ErrorAction SilentlyContinue
    }
    foreach ($l in @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) "$OldName.lnk"),
        (Join-Path ([Environment]::GetFolderPath('Programs')) "$OldName.lnk"))) {
        Remove-Item $l -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $OldKey -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null

# Remove the Python setup of version 1.4 (it was blocked by Smart App Control)
foreach ($d in '.venv', 'tools', 'python') {
    $p = Join-Path $Root $d
    if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
}

# --- 2. Python (official signed build from python.org) -------------------------------
$sitePip = Join-Path $Runtime 'Lib\site-packages\pip'
if (-not ((Test-Path $Py) -and (Test-Path $sitePip))) {
    Step "Setting up Python $PyVer from python.org (one time)"
    if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
    $zip = Join-Path $env:TEMP 'chatbox-studio-python.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $PyZipUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $Runtime -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Get-ChildItem $Runtime -Recurse | Unblock-File -ErrorAction SilentlyContinue

    # The embeddable Python ignores site-packages until "import site" is enabled.
    $pth = Get-ChildItem $Runtime -Filter 'python*._pth' | Select-Object -First 1
    if ($pth) {
        $lines = Get-Content $pth.FullName | ForEach-Object { $_ -replace '^#\s*import site', 'import site' }
        Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII
    }

    $r = Invoke-Native $Py @('-c', 'import sys; print(sys.version)')
    if ($r[0] -ne 0) {
        if (Test-Blocked $r[1]) { Fail-Blocked "python.exe ($($r[1].Trim()))" }
        Fail "Python does not start: $($r[1].Trim())"
    }

    Step 'Installing pip'
    $gp = Join-Path $Runtime 'get-pip.py'
    Invoke-WebRequest -UseBasicParsing -Uri $GetPip -OutFile $gp
    & $Py $gp --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail ('Installing pip failed. Check your internet connection and ' + $Again + '.') }
    Remove-Item $gp -Force -ErrorAction SilentlyContinue
}

# --- 3. the setup window's own packages (Qt). Everything else is picked in the window. --
$base = Join-Path $Src 'req\base.txt'
$sha = [Security.Cryptography.SHA256]::Create()
$hash = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($base))) -replace '-', ''
$have = $null
if (Test-Path $HashFile) { $have = (Get-Content $HashFile -Raw).Trim() }
if ($hash -ne $have) {
    Step 'Installing the setup window (~100 MB)'
    & $Py -m pip install --no-warn-script-location --disable-pip-version-check -r $base
    if ($LASTEXITCODE -ne 0) { Fail ('Installing the packages failed. Check your internet connection and ' + $Again + '.') }
    $r = Invoke-Native $Py @('-c', 'from PySide6 import QtWidgets; import numpy, psutil; print(1)')
    if ($r[0] -ne 0) {
        if (Test-Blocked $r[1]) {
            $line = ($r[1] -split "`n" | Where-Object { $_ -match 'Error|blocked' } | Select-Object -Last 1)
            Fail-Blocked $line.Trim()
        }
        Fail "A library does not load:`n$($r[1].Trim())"
    }
    Set-Content -Path $HashFile -Value $hash -Encoding ASCII
}

# --- 5. app files --------------------------------------------------------------------------
Step 'Copying the app'
if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
Copy-Item $Src $AppDir -Recurse -Force
foreach ($f in 'Uninstall.bat', 'uninstall.ps1', 'Start with console.bat') {
    Copy-Item (Join-Path $Support $f) (Join-Path $Root $f) -Force
}
Copy-Item (Join-Path $Here 'README.txt') (Join-Path $Root 'README.txt') -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Here 'CHANGELOG.md') (Join-Path $Root 'CHANGELOG.md') -Force -ErrorAction SilentlyContinue

# --- 6. shortcuts and "Apps & features" entry ------------------------------------------
Step 'Creating shortcuts'
$icon = Join-Path $AppDir 'assets\icon.ico'
$shell = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk")
)
foreach ($l in $links) {
    $s = $shell.CreateShortcut($l)
    $s.TargetPath = $PyW
    $s.Arguments = '"' + (Join-Path $AppDir 'app.py') + '"'
    $s.WorkingDirectory = $AppDir
    $s.IconLocation = $icon
    $s.Description = 'Voice translation, music, clock and status for your VRChat chatbox'
    $s.Save()
}
$SetupArgs = '"' + (Join-Path $AppDir 'setup_gui.py') + '"'
$s = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName Setup.lnk"))
$s.TargetPath = $PyW
$s.Arguments = $SetupArgs + ' --modify'
$s.WorkingDirectory = $AppDir
$s.IconLocation = $icon
$s.Description = 'Add or remove parts of Chatbox Studio (speech models, translator, AI)'
$s.Save()

$key = $RegKey
New-Item -Path $key -Force | Out-Null
$props = @{
    DisplayName     = $AppName
    DisplayVersion  = $Version
    Publisher       = 'Chatbox Studio'
    DisplayIcon     = $icon
    InstallLocation = $Root
    UninstallString = '"' + (Join-Path $Root 'Uninstall.bat') + '"'
}
foreach ($k in $props.Keys) { Set-ItemProperty -Path $key -Name $k -Value $props[$k] }
Set-ItemProperty -Path $key -Name ModifyPath -Value ('"' + $PyW + '" ' + $SetupArgs + ' --modify')
Set-ItemProperty -Path $key -Name NoModify -Value 0 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord

Set-Content -Path $VerFile -Value $Version -Encoding ASCII

# --- 7. pick the parts ---------------------------------------------------------------------
if ($Defaults) {
    Step 'Installing the recommended parts'
    & $Py (Join-Path $AppDir 'setup_gui.py') --defaults
    if ($LASTEXITCODE -eq 3) { Fail-Blocked 'a library of the speech engine' }
    if ($LASTEXITCODE -ne 0) { Fail ('Installing the packages failed. Check your internet connection and ' + $Again + '.') }
} else {
    Step 'Opening the setup window - pick what you want there'
    $mode = '--first'
    if ($Old -or $Legacy) { $mode = '--update' }
    if ($FromExe) {             # the exe closes; the setup window takes over
        Start-Process -FilePath $PyW -ArgumentList @($SetupArgs, $mode) -WorkingDirectory $AppDir
        exit 0
    }
    $p = Start-Process -FilePath $PyW -ArgumentList @($SetupArgs, $mode) -WorkingDirectory $AppDir -Wait -PassThru
}

Write-Host ''
if ($Old -or $Legacy) { Write-Host "  Updated to $AppName $Version." -ForegroundColor Green }
else      { Write-Host "  Installed $AppName $Version." -ForegroundColor Green }
Write-Host '  Start it from the desktop shortcut or the Start menu.'
Write-Host "  Add or remove parts later: '$AppName Setup' in the Start menu."
if ($Defaults) {
    Start-Process -FilePath $PyW -ArgumentList ('"' + (Join-Path $AppDir 'app.py') + '"') -WorkingDirectory $AppDir
}
exit 0
