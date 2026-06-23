; Ubiquity — Windows installer
; Requires Inno Setup 6+: https://jrsoftware.org/isinfo.php
;
; Build steps:
;   1. pyinstaller ubiquity.spec --clean        (produces dist\ubiquity.exe)
;   2. iscc installer\windows\setup.iss         (produces dist\UbiquitySetup.exe)
;
; The installer requires NO administrator rights:
;   - installs to %LOCALAPPDATA%\Ubiquity
;   - autostart via HKCU registry key (not Task Scheduler)
;   - uninstall available from Settings > Apps without admin

#define AppName      "Ubiquity"
#define AppVersion   "1.0"
#define AppExeName   "ubiquity.exe"
#define AppURL       "https://github.com/your-repo/ubiquity"

[Setup]
AppId={{6F3C1A2B-4D5E-4F6A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
; Install into %LOCALAPPDATA% — no admin prompt
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; No UAC elevation
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
OutputDir=..\..\dist
OutputBaseFilename=UbiquitySetup
SetupIconFile=
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSmallImageFile=

[Languages]
Name: "french";  MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart";    Description: "Démarrer Ubiquity avec Windows"; GroupDescription: "Options :"; Flags: checked
Name: "desktopicon";  Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Icônes supplémentaires :"

[Files]
Source: "..\..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Autostart at login — HKCU, no admin needed, removed on uninstall
Root: HKCU; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; \
  ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; \
  Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "Lancer {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill the running tray app before uninstalling
Filename: "taskkill.exe"; Parameters: "/f /im {#AppExeName}"; Flags: runhidden; RunOnceId: "KillApp"
