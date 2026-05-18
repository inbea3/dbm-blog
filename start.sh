#!/usr/bin/env bash
# 启动博客后端（同时提供前端静态页面）
set -e
cd "$(dirname "$0")/backend"
echo "正在启动博客服务 → http://127.0.0.1:5000"
echo "请在浏览器访问: http://localhost:5000  （AutoDL 需先配置端口映射）"
exec python3 app.py
