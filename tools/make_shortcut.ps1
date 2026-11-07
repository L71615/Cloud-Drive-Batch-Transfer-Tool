# Create PanTransfer shortcuts on Desktop and in Start Menu.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a UTF-8 file
# without BOM as ANSI, and non-ASCII text here breaks the parser.
param(
    [Parameter(Mandatory = $true)][string]$Exe
)

$Exe = (Resolve-Path $Exe).Path
$wd = Split-Path $Exe -Parent
$sh = New-Object -ComObject WScript.Shell

$targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)

foreach ($dir in $targets) {
    if (-not (Test-Path $dir)) { continue }
    $lnk = Join-Path $dir 'PanTransfer.lnk'
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $Exe
    $s.WorkingDirectory = $wd
    $s.IconLocation = "$Exe,0"
    $s.Description = 'PanTransfer batch transfer'
    $s.Save()
    Write-Host "shortcut: $lnk"
}
