@echo off
chcp 65001 >nul
title 混沌龙虾交易机器人 - 一键安装
color 0A

echo ============================================
echo  🦞 混沌龙虾自动交易机器人 - 一键安装
echo ============================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到Python，请先安装Python 3.10+
    echo 📥 下载地址: https://www.python.org/downloads/
    echo ⚠️  安装时记得勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo ✅ Python已安装

:: 创建目录
echo 📁 创建项目目录...
mkdir C:\chaos-lobster-trading 2>nul
mkdir C:\chaos-lobster-trading\config 2>nul
mkdir C:\chaos-lobster-trading\strategies 2>nul
mkdir C:\chaos-lobster-trading\risk 2>nul
mkdir C:\chaos-lobster-trading\utils 2>nul
mkdir C:\chaos-lobster-trading\logs 2>nul
mkdir C:\chaos-lobster-trading\signals 2>nul
mkdir C:\chaos-lobster-trading\ui 2>nul

:: 复制文件
echo 📦 复制程序文件...
xcopy /Y /E "%~dp0src\*" "C:\chaos-lobster-trading\" >nul 2>&1

:: 安装依赖
echo 📥 安装依赖包 (需要2-3分钟)...
cd C:\chaos-lobster-trading
pip install -r requirements.txt -q

if errorlevel 1 (
    echo ❌ 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)

echo ✅ 依赖安装完成

:: 创建快捷方式
echo 🚀 创建桌面快捷方式...
powershell -Command "$WshShell = New-Object -comObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\混沌龙虾交易机器人.lnk'); $Shortcut.TargetPath = 'C:\chaos-lobster-trading\start_bot.bat'; $Shortcut.IconLocation = 'C:\chaos-lobster-trading\ui\lobster.ico'; $Shortcut.Save()"

echo.
echo ============================================
echo  ✅ 安装完成！
echo ============================================
echo.
echo 📍 安装路径: C:\chaos-lobster-trading\
echo 🖥️  桌面快捷方式已创建
echo.
echo ⚠️  重要：请先配置API密钥！
echo    编辑文件: C:\chaos-lobster-trading\config\okx_config.json
echo.
echo 🚀 启动方式:
echo    1. 双击桌面快捷方式
echo    2. 或运行: C:\chaos-lobster-trading\start_bot.bat
echo.
pause
