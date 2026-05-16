# 个人博客

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

基于 **Flask** 与 **PostgreSQL** 的单体博客：服务端渲染文章 HTML、REST API、会话登录与访客互动；前端为原生 **HTML + Tailwind CDN + 共享 JS/CSS**，无 SPA 构建步骤。

---

## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [仓库结构](#仓库结构)
- [数据库](#数据库)
- [常用脚本](#常用脚本)
- [部署](#部署)
- [开发说明](#开发说明)
- [许可证](#许可证)

---

## 功能特性

- 文章：Markdown/纯文本、分类与标签、草稿与发布、列表与详情、站内上一篇/下一篇
- 媒体：正文插图本地上传（存 `media_asset` BLOB）或外链登记；作者头像上传
- 互动：访客昵称、评论、赞/踩；管理员评论带「博主」标识
- 作者页：资料、技能、联系方式（含邮箱 / QQ 等来自数据库）
- 深色模式、管理员登录与权限守卫

---

## 技术栈

| 类别 | 选用 |
|------|------|
| 运行时 | Python 3.10+（建议） |
| Web | Flask 3、Flask-CORS |
| 数据库 | PostgreSQL（Neon 兼容），psycopg 3 + 连接池 |
| 内容 | Markdown（fenced_code、tables、toc） |
| 前端 | 静态 HTML、Tailwind CSS（CDN）、Font Awesome、共享 `frontend/assets/` |

---

## 环境要求

- **Python** 3.10 或更高（与 `psycopg[binary]` 3.x 兼容）
- 可访问的 **PostgreSQL** 实例（本地或 Neon 等）
- 可选：**pip** 用于安装依赖

---

## 快速开始

### 1. 克隆并安装依赖

```bash
git clone <你的仓库 URL> blog
cd blog
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r backend/requirements.txt
```

### 2. 配置环境变量

在仓库根目录复制并编辑 `.env`（可参考 `.env.example`）：

- 至少配置 **数据库连接**（见下表）
- 若库中还没有管理员，需配置 **`BLOG_ADMIN_EMAIL`** 与 **`BLOG_ADMIN_PASSWORD`** 以便首次启动时创建管理员

### 3. 初始化数据库

在目标库中执行建表脚本（可按需调整）：

- 主结构：`sql/create_postgresql.sql`

应用启动时还会执行 `postgres_store._ensure_schema()`，对已有库做增量列/约束迁移。

### 4. 启动应用

```bash
python backend/app.py
```

在浏览器打开 **`http://127.0.0.1:5000`** 或 **`http://localhost:5000`**（端口可通过 `PORT` 修改）。

> **请勿**用 `file://` 直接打开 `frontend/*.html`，否则无法携带 Cookie、也无法请求同源 `/api`。

---

## 配置说明

环境变量可在系统环境中设置，也可写在项目根目录 **`.env`**（由 `python-dotenv` 在加载 `backend/neon_db.py` 时读取）。解析优先级与兼容写法见 `backend/neon_db.py`。

### 数据库连接（必填其一）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` 或 `BLOG_DATABASE_URL` | PostgreSQL 连接 URI，例如 `postgresql://user:pass@host/db?sslmode=require` |
| `PGHOST`、`PGDATABASE`、`PGUSER`、`PGPASSWORD` | 分字段连接；可选 `PGSSLMODE`（默认 `require`）、`PGCHANNELBINDING` |

### 应用与管理员

| 变量 | 说明 |
|------|------|
| `BLOG_SECRET_KEY` | Flask 会话密钥；不设置则每次进程随机（生产环境务必固定） |
| `PORT` | HTTP 端口，默认 `5000` |
| `BLOG_ADMIN_EMAIL` | 首个管理员邮箱（仅当库中尚无 `role = admin` 的用户时使用） |
| `BLOG_ADMIN_PASSWORD` | 首个管理员明文密码（仅写入哈希到库，不落日志） |

### 重置已有用户密码

若需为已有账号更新 `password_hash`（Werkzeug 格式），可在 `backend` 目录执行：

```bash
cd backend
python set_user_password.py <邮箱> <新密码明文>
```

---

## 仓库结构

```
blog/
├── backend/                 # Flask 应用与数据访问
│   ├── app.py               # 路由、静态页入口、/api、/assets
│   ├── postgres_store.py    # SQL 与业务逻辑
│   ├── neon_db.py           # DSN 解析与连接池
│   ├── requirements.txt
│   ├── run_db_migrations.py # 仅执行 _ensure_schema（不启 Flask）
│   └── set_user_password.py # 命令行重置密码
├── frontend/                # HTML 页面
│   ├── *.html
│   └── assets/              # style.css、script.js（URL 仍为 /assets/...）
├── sql/                     # 建表与示例 / 迁移 SQL
├── .env.example             # 环境变量示例（勿提交真实密钥）
├── LICENSE                  # MIT 许可证全文
├── DEPLOYMENT.md            # 部署补充说明
└── README.md
```

---

## 数据库

- **建表**：`sql/create_postgresql.sql`
- **运行时迁移**：随 `bootstrap_if_needed` / `run_db_migrations.py` 调用 `_ensure_schema`
- **媒体**：文章图与头像元数据在 `media_asset`；本地上传的二进制在 `content`（BYTEA），外链仅记 `public_url`；对外通过 **`GET /api/media/<uuid>`** 提供内容或重定向

---

## 常用脚本

| 脚本 | 用途 |
|------|------|
| `python backend/app.py` | 启动 Web 服务 |
| `python backend/run_db_migrations.py` | 在已配置 `.env` 的前提下仅跑结构迁移 |
| `python backend/set_user_password.py <邮箱> <密码>` | 更新指定用户密码哈希 |

---

## 部署

生产环境建议使用 **Gunicorn** 等 WSGI 服务器，并固定 `BLOG_SECRET_KEY`、使用 HTTPS 与安全的 Cookie 策略。更多说明见 **[DEPLOYMENT.md](./DEPLOYMENT.md)**。

---

## 开发说明

- **前端修改**：编辑 `frontend/*.html` 与 `frontend/assets/`；样式/脚本的 URL 保持 **`/assets/...`** 即可（由 `app.py` 映射到 `frontend/assets/`）。
- **API 修改**：主要位于 `backend/app.py` 与 `backend/postgres_store.py`。
- **依赖变更**：更新 `backend/requirements.txt` 后请在 PR / 提交说明中注明。

---

## 许可证

本项目以 **[MIT License](./LICENSE)** 授权发布。使用、复制、修改与分发时，请保留 `LICENSE` 中的版权声明与许可全文。

版权所有 © 2025 Mujing（以 `LICENSE` 文件记载为准）。
