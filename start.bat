@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist data mkdir data
echo Starting PanTransfer on http://127.0.0.1:8765/ ...
start "" http://127.0.0.1:8765/
python run.py --port 8765 --db "%~dp0data\pantransfer.db" --open
if errorlevel 1 (
  echo.
  echo 启动失败：确认已安装 Python 3.10+ 并已加入 PATH。
  pause
)
