# 个人博客

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

基于 **Flask** 与 **PostgreSQL** 的单体博客：服务端渲染文章 HTML、REST API、会话登录与访客互动；前端为原生 **HTML + Tailwind（浏览器运行时）+ 共享 JS/CSS**，无 Node 构建步骤。

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
- [API 概览](#api-概览)
- [部署](#部署)
- [开发说明](#开发说明)
- [许可证](#许可证)

---

## 功能特性

### 内容与展示

- 文章：Markdown / 纯文本、分类与标签、发布、列表与详情、站内上一篇 / 下一篇
- 媒体：正文插图本地上传（`media_asset` BLOB）或外链；作者头像上传
- 博客列表：热门标签可视化（条形图 + 标签云）
- 字体：UI 区楷体、正文 Noto Serif SC；拉丁字母可选 Centaur（见 `frontend/assets/fonts.css`）

### 互动

- 访客昵称、评论、赞 / 踩；管理员评论带「博主」标识
- **划线评论**：正文选中文本右键「划线并评论」，支持跟帖；高亮锚点与跳转

### 智能能力（可选 LLM）

需在 `.env` 配置 OpenAI 兼容接口（DeepSeek 等）。未配置时相关功能自动降级。

| 功能 | 说明 |
|------|------|
| **相关阅读** | 规则预筛 + LLM 精排；结果缓存于数据库，详情页异步加载 |
| **小zimu** | 全站浮窗助手，基于已发布文章的 RAG + 多轮对话（右下角入口） |

### 其它

- 作者页：资料、技能、联系方式（数据库驱动）
- 深色模式、管理员登录与权限守卫

---

## 技术栈

| 类别 | 选用 |
|------|------|
| 运行时 | Python 3.10+（建议） |
| Web | Flask 3、Flask-CORS |
| 数据库 | PostgreSQL（Neon 兼容），psycopg 3 + 连接池 |
| 内容 | Markdown（fenced_code、tables、toc） |
| 前端 | 静态 HTML、`assets/vendor/tailwind.min.js`、Font Awesome、共享 `frontend/assets/` |
| LLM | OpenAI 兼容 Chat Completions（`urllib`，无额外 SDK） |

---

## 环境要求

- **Python** 3.10 或更高
- 可访问的 **PostgreSQL** 实例（本地或 Neon 等）
- 可选：**pip** 安装依赖；可选 **LLM API** 启用推荐与小zimu

---

## 快速开始

### 1. 克隆并安装依赖

```bash
git clone <你的仓库 URL> dbm-blog
cd dbm-blog
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r backend/requirements.txt
```

### 2. 配置环境变量

在仓库根目录复制并编辑 `.env`（可参考 `.env.example`）：

- **必填**：数据库连接（见下表）
- **首次建库**：`BLOG_ADMIN_EMAIL` 与 `BLOG_ADMIN_PASSWORD`（库中尚无管理员时自动创建）
- **可选**：`MODEL_NAME`、`BASE_URL`、`API_KEY`（相关阅读 + 小zimu）

### 3. 初始化数据库

在目标库中执行建表脚本（可按需调整）：

- 主结构：`sql/create_postgresql.sql`

应用启动时还会执行 `postgres_store._ensure_schema()`，对已有库做增量表 / 列迁移（含划线评论、相关阅读缓存、RAG 分块等）。

### 4. 启动应用

```bash
bash start.sh
```

或：

```bash
python backend/app.py
```

在浏览器打开 **`http://127.0.0.1:5000`**（端口可通过 `PORT` 修改）。  
**AutoDL** 等云环境需先在控制台配置端口映射到 `5000`。

> **请勿**用 `file://` 直接打开 `frontend/*.html`，否则无法携带 Cookie，也无法请求同源 `/api`。

---

## 配置说明

环境变量写在项目根目录 **`.env`**，由 `backend/neon_db.py` 通过 `python-dotenv` 加载。

### 数据库连接（必填其一）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` 或 `BLOG_DATABASE_URL` | PostgreSQL URI，如 `postgresql://user:pass@host/db?sslmode=require` |
| `PGHOST`、`PGDATABASE`、`PGUSER`、`PGPASSWORD` | 分字段连接；可选 `PGSSLMODE`（默认 `require`） |

### 应用与管理员

| 变量 | 说明 |
|------|------|
| `BLOG_SECRET_KEY` | Flask 会话密钥；生产环境务必固定 |
| `PORT` | HTTP 端口，默认 `5000` |
| `BLOG_ADMIN_EMAIL` | 首个管理员邮箱 |
| `BLOG_ADMIN_PASSWORD` | 首个管理员密码（仅存哈希） |

### LLM（可选）

| 变量 | 说明 |
|------|------|
| `MODEL_NAME` | 模型名，如 `deepseek-chat` |
| `BASE_URL` | API 根地址，如 `https://api.deepseek.com` |
| `API_KEY` | API 密钥 |

### 重置已有用户密码

```bash
cd backend
python set_user_password.py <邮箱> <新密码明文>
```

---

## 仓库结构

```
dbm-blog/
├── start.sh                 # 一键启动（cd backend && python3 app.py）
├── backend/
│   ├── app.py               # 路由、静态页、/api、/assets
│   ├── postgres_store.py    # 数据访问与业务逻辑
│   ├── neon_db.py           # DSN 与连接池
│   ├── llm_client.py        # OpenAI 兼容 LLM 调用
│   ├── rag_index.py         # 博客全文 RAG 分块与检索
│   ├── brain_service.py     # 小zimu 对话编排
│   ├── requirements.txt
│   ├── run_db_migrations.py
│   └── set_user_password.py
├── frontend/
│   ├── index.html / blog.html / detail.html / about.html / editor.html
│   └── assets/
│       ├── script.js          # 鉴权、作者信息、深色模式等
│       ├── style.css / fonts.css
│       ├── highlights.js      # 划线评论
│       ├── zimu-brain.js      # 小zimu 浮窗
│       └── vendor/tailwind.min.js
├── sql/                     # 建表与迁移 SQL（勿删）
├── .env.example
├── DEPLOYMENT.md
├── LICENSE
└── README.md
```

---

## 数据库

- **建表**：`sql/create_postgresql.sql`
- **运行时迁移**：`bootstrap_if_needed` / `run_db_migrations.py` → `_ensure_schema`
- **媒体**：`GET /api/media/<uuid>` 提供 BLOB 或外链重定向
- **扩展表**（可由迁移自动创建）：`article_highlight`、`highlight_comment`、`article_related_cache`、`blog_rag_chunk` 等

发布 / 更新 / 删除文章后，会在后台自动刷新相关阅读缓存与 RAG 索引。

---

## 常用脚本

| 命令 | 用途 |
|------|------|
| `bash start.sh` | 启动 Web 服务（推荐） |
| `python backend/app.py` | 同上 |
| `python backend/run_db_migrations.py` | 仅执行结构迁移 |
| `python backend/set_user_password.py <邮箱> <密码>` | 重置用户密码 |

管理员可调用 `POST /api/brain/reindex` 手动重建小zimu 知识库索引。

---

## API 概览

| 路径 | 说明 |
|------|------|
| `GET/POST /api/articles` | 列表 / 创建 |
| `GET/PUT/DELETE /api/articles/<id>` | 详情 / 更新 / 删除 |
| `GET /api/articles/<id>/related` | 相关阅读（缓存 + 异步 LLM） |
| `POST /api/articles/<id>/highlights` | 创建划线 |
| `POST /api/highlights/<id>/comments` | 划线评论 / 跟帖 |
| `GET /api/brain/status` | 小zimu 就绪状态 |
| `POST /api/brain/chat` | 小zimu 对话（`message`、`history`、`page_context`） |
| `POST /api/brain/reindex` | 重建 RAG 索引（管理员） |
| `GET /api/stats` | 首页统计 |
| `GET /api/author`、`POST /api/login` 等 | 作者信息与鉴权 |

完整路由见 `backend/app.py`。

---

## 部署

生产环境建议使用 **Gunicorn** 等 WSGI 服务器，并固定 `BLOG_SECRET_KEY`、使用 HTTPS。详见 **[DEPLOYMENT.md](./DEPLOYMENT.md)**。

---

## 开发说明

- **前端**：编辑 `frontend/*.html` 与 `frontend/assets/`；静态资源 URL 使用 **`/assets/...`**
- **样式**：使用 `assets/vendor/tailwind.min.js`，各页内联 `tailwind.config = { darkMode: 'class' }`；无需 Tailwind CLI 构建
- **后端**：`app.py` 路由 + `postgres_store.py` / `rag_index.py` / `brain_service.py`
- **依赖**：变更后更新 `backend/requirements.txt`

---

## 许可证

本项目以 **[MIT License](./LICENSE)** 授权发布。

版权所有 © 2025 Mujing（以 `LICENSE` 文件记载为准）。
