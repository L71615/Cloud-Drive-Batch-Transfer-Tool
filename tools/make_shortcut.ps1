# Create PanTransfer shortcuts on Desktop and in Start Menu.
# The shortcut runs start_app.vbs through wscript.exe: the script starts the
# server and then opens the UI window. Launching the browser from the exe
# itself does not work (blank window), from wscript it does.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a UTF-8 file
# without BOM as ANSI, and non-ASCII text here breaks the parser.
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Vbs = ''
)

$Exe = (Resolve-Path $Exe).Path
$wd = Split-Path $Exe -Parent
if (-not $Vbs) { $Vbs = Join-Path $wd 'start_app.vbs' }
$Vbs = (Resolve-Path $Vbs).Path
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'

$sh = New-Object -ComObject WScript.Shell

$targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)

foreach ($dir in $targets) {
    if (-not (Test-Path $dir)) { continue }
    $lnk = Join-Path $dir 'PanTransfer.lnk'
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $wscript
    $s.Arguments = '"' + $Vbs + '"'
    $s.WorkingDirectory = $wd
    $s.IconLocation = "$Exe,0"
    $s.Description = 'PanTransfer batch transfer'
    $s.Save()
    Write-Host "shortcut: $lnk -> wscript $Vbs"
}
