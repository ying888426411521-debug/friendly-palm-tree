#!/bin/sh
# macOS / Linux 启动脚本
cd "$(dirname "$0")"
nohup python3 netwatch.py >/dev/null 2>&1 &
