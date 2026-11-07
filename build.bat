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

echo [2/3] 打包 exe（约 10-20 秒）
REM 先打到 build_dist，再拷进 dist —— 否则 PyInstaller 会清空 dist\PanTransfer，
REM 连同 data\ 里的数据库、登录态、日志一起删掉。
python -m PyInstaller --noconfirm --onedir --noconsole ^
  --name PanTransfer ^
  --distpath build_dist ^
  --workpath build_work ^
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

echo [3/3] 拷到 dist（保留 data 目录）
robocopy build_dist\PanTransfer dist\PanTransfer /MIR /XD data /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo 拷贝失败，检查 dist\PanTransfer 是否还在被占用（先结束 PanTransfer.exe）。
  pause
  exit /b 1
)
copy /y start_app.vbs "dist\PanTransfer\start_app.vbs" >nul

echo.
echo 打包完成：dist\PanTransfer\PanTransfer.exe
echo 现在创建桌面快捷方式...
call make_shortcut.bat
pause
