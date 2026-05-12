# 个人博客

Flask + 原生 HTML/JS，数据在 **PostgreSQL**。本地 `assets/` 为样式与脚本目录。

## 启动

```bash
pip install -r backend/requirements.txt
python backend/app.py
```

浏览器访问 `http://localhost:5000`（不要 `file://`）。

## 配置

- 数据库：`DATABASE_URL` / `BLOG_DATABASE_URL`，或环境变量 `PGHOST`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`（可选 `PGSSLMODE`、`PGCHANNELBINDING`）；逻辑见 `backend/neon_db.py`。本地可把值写在项目根目录 **`.env`**（参考 `.env.example`，已由 `python-dotenv` 在导入 `neon_db` 时加载）。
- 首个管理员：库中尚无 `role=admin` 时，需设 `BLOG_ADMIN_EMAIL`、`BLOG_ADMIN_PASSWORD`；已有管理员则用库里邮箱+密码登录。`password_hash` 须为 Werkzeug 哈希，可用 `python backend/set_user_password.py 邮箱 明文密码`。
- 可选：`BLOG_SECRET_KEY`、`PORT`。

## 目录

- `frontend/`：页面  
- `backend/`：`app.py`、`postgres_store.py`、`neon_db.py`  
- `assets/`：`style.css`、`script.js`（头像与文章插图二进制在库表 `media_asset`，经 `GET /api/media/<id>` 输出）
- `create_postgresql.sql`、`sql/`：库结构 / 迁移  

更多部署见 `DEPLOYMENT.md`，结构说明见 `summary.md`。
