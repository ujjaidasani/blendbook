; Windows installer (Inno Setup), built in CI after PyInstaller. Per-user: no admin rights needed.
#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{7E0D2B3A-6B0E-4B7E-9D6F-DECB0DD1E100}
AppName=Blendbook
AppVersion={#Version}
AppPublisher=Blendbook
DefaultDirName={localappdata}\Programs\Blendbook
DefaultGroupName=Blendbook
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Blendbook-{#Version}-windows-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Blendbook.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\Blendbook\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\Blendbook"; Filename: "{app}\Blendbook.exe"

[Run]
Filename: "{app}\Blendbook.exe"; Description: "Start Blendbook"; Flags: nowait postinstall skipifsilent
