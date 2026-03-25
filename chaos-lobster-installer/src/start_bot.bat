@echo off
chcp 65001 >nul
title 混沌龙虾自动交易机器人
color 0A

echo ============================================
echo  🦞 混沌龙虾自动交易机器人 v2.0
echo ============================================
echo.

cd /d C:\chaos-lobster-trading

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到Python
    echo 📥 请先安装Python 3.10+: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 启动UI
echo 🚀 启动UI界面...
python ui\main_ui.py

pause
