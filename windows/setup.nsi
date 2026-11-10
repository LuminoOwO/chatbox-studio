; Chatbox Studio - Windows setup program (built with NSIS by build_release.py)
;
; A small, per-user installer (no admin rights). It unpacks the app into a temporary
; folder and runs install.ps1, which sets up the official Python from python.org in
; %LOCALAPPDATA%\Chatbox Studio and copies the app there. Then the Chatbox Studio Setup
; window opens, where you pick the parts (speech model, translator, ...).
;
; makensis -DVERSION=1.27 -DPAYLOAD=<folder> -DOUTFILE=<exe> -DICON=<ico> setup.nsi

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
ManifestDPIAware true

!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "Chatbox Studio ${VERSION}"
Caption "Chatbox Studio ${VERSION} Setup"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Chatbox Studio"
BrandingText "Chatbox Studio ${VERSION}"
ShowInstDetails show

VIProductVersion "${VERSION}.0.0"
VIAddVersionKey "ProductName" "Chatbox Studio"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "Chatbox Studio Setup"
VIAddVersionKey "LegalCopyright" "Chatbox Studio"

!define MUI_ICON "${ICON}"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${__FILEDIR__}\welcome.bmp"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Chatbox Studio ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Speak in VRChat - your words show up in the chatbox, translated if you like. Everything runs on your PC.$\r$\n$\r$\nThis installs Chatbox Studio for your Windows user (no admin rights needed). It downloads the official Python from python.org (about 25 MB) and the setup window (about 100 MB).$\r$\n$\r$\nAfterwards the Chatbox Studio Setup window opens: choose Standard (speech recognition and translation) or pick the parts yourself.$\r$\n$\r$\nAn existing install is updated - your settings and models are kept."
!define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Ready"
!define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "The Chatbox Studio Setup window opens now."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Chatbox Studio"
    SetDetailsPrint both
    DetailPrint "Unpacking Chatbox Studio ${VERSION} ..."
    SetDetailsPrint listonly
    InitPluginsDir
    SetOutPath "$PLUGINSDIR\pkg"
    File /r "${PAYLOAD}\*"
    SetDetailsPrint both
    DetailPrint "Setting up (this downloads Python the first time - a few minutes) ..."
    SetDetailsPrint listonly
    nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\pkg\install.ps1" -FromExe'
    Pop $0
    SetDetailsPrint both
    ${If} $0 == "0"
        DetailPrint "Done - the Chatbox Studio Setup window opens."
        SetOutPath "$TEMP"
        SetAutoClose true
    ${Else}
        DetailPrint "The installation failed (code $0). See the messages above."
        SetOutPath "$TEMP"
        MessageBox MB_ICONEXCLAMATION|MB_OK "Chatbox Studio couldn't be installed - the messages in this window say why.$\r$\n$\r$\nMost often it's the internet connection or Windows Smart App Control. You can run the installer again; it continues where it stopped."
        Abort
    ${EndIf}
SectionEnd
