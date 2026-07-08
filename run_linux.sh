#!/usr/bin/env bash
# 绝区零一条龙 Linux 启动脚本
# 用法:
#   ./run_linux.sh          正常启动 GUI
#   ./run_linux.sh --sync   先同步依赖再启动（首次运行、或更新依赖后用）
# 详见 docs/develop/one_dragon/linux_port_design.md
set -euo pipefail

# 切到脚本所在目录（项目根），使脚本可从任意位置调用
cd "$(dirname "$(readlink -f "$0")")"

# 首次或依赖更新后：同步依赖
if [[ "${1:-}" == "--sync" ]]; then
    uv sync --group dev
    # pyautogui 会连带装旧的 python3-xlib，与 python-xlib 撞名，重装修复
    uv sync --reinstall-package python-xlib
fi

# 输入注入需要 /dev/uinput 可写（用户在 input 组）
if [[ ! -w /dev/uinput ]]; then
    echo "警告: /dev/uinput 不可写，输入注入将失败。" >&2
    echo "      把当前用户加入 input 组后重新登录: sudo usermod -aG input \"\$USER\"" >&2
fi

# PYTHONPATH=src        src-layout，让 Python 找到 one_dragon / zzz_od 包
# QT_QPA_PLATFORM=xcb   GUI 走 XWayland，与游戏窗口 / 截图 / 输入注入同一坐标世界
exec env PYTHONPATH=src QT_QPA_PLATFORM=xcb uv run python src/zzz_od/gui/app.py
