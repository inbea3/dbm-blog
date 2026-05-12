# 项目结构说明

- **数据**：PostgreSQL（用户、文章、分类、标签、评论、赞踩、`media_asset` 等）。建表见 `create_postgresql.sql`；增量见 `sql/*.sql`。
- **后端**：`backend/app.py`（路由、会话、静态资源）、`postgres_store.py`、`neon_db.py`。
- **前端**：`frontend/*.html`，共用 `assets/script.js`、`assets/style.css`。
- **静态**：`assets/` 下样式与脚本；管理员头像与文章插图存 `media_asset`（正文 Markdown 可写 `![](/api/media/<uuid>)`），外链图片 URL 亦可直接使用。
- **权限**：游客可读可评可互动；仅 `user.role = admin` 可写文、改资料、上传。登录走 `POST /api/login`，密码存 `password_hash`（Werkzeug）。
