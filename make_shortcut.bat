@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "EXE=%~dp0dist\PanTransfer\PanTransfer.exe"
if not exist "%EXE%" (
  echo 还没有打包：先双击 build.bat
  pause
  exit /b 1
)
if not exist "%~dp0dist\PanTransfer\start_app.vbs" (
  copy /y "%~dp0start_app.vbs" "%~dp0dist\PanTransfer\start_app.vbs" >nul
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcut.ps1" -Exe "%EXE%"
if errorlevel 1 (
  echo 创建快捷方式失败。
  pause
  exit /b 1
)

echo.
echo 完成。桌面上双击 PanTransfer 图标即可打开。
pause
