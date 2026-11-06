@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  打包 PanTransfer 桌面版
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo 没找到 python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo 正在安装 PyInstaller...
  python -m pip install pyinstaller
)

echo [1/2] 生成图标
python tools\make_icon.py

echo [2/2] 打包 exe（约 10-20 秒）
python -m PyInstaller --noconfirm --onedir --noconsole ^
  --name PanTransfer ^
  --icon "app\static\favicon.ico" ^
  --add-data "app\static;app\static" ^
  --hidden-import app.http_api ^
  --hidden-import run ^
  launcher.pyw

if errorlevel 1 (
  echo.
  echo 打包失败，看上面的报错。
  pause
  exit /b 1
)

echo.
echo 打包完成：dist\PanTransfer\PanTransfer.exe
echo 现在创建桌面快捷方式...
call make_shortcut.bat
pause
