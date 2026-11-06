@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "EXE=%~dp0dist\PanTransfer\PanTransfer.exe"
if not exist "%EXE%" (
  echo 还没有打包：先双击 build.bat
  pause
  exit /b 1
)

powershell -NoProfile -Command ^
  "$exe='%EXE%'; $wd=Split-Path $exe; $sh=New-Object -ComObject WScript.Shell; foreach($d in @(\"$env:USERPROFILE\Desktop\", \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\")){ $l=Join-Path $d 'PanTransfer.lnk'; $s=$sh.CreateShortcut($l); $s.TargetPath=$exe; $s.WorkingDirectory=$wd; $s.IconLocation=\"$exe,0\"; $s.Description='PanTransfer 网盘批量转存'; $s.Save(); Write-Host \"快捷方式：$l\" }"

echo.
echo 完成。桌面上双击 PanTransfer 图标即可打开。
pause
