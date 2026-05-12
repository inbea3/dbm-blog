# 部署说明

## 本地

```bash
cd backend
pip install -r requirements.txt
python app.py
```

用环境变量 `PORT` 改端口；数据库连接见 `README.md`。

## 线上要点

1. 配置 `DATABASE_URL`（或 Neon 等连接串）。
2. 设置 `BLOG_SECRET_KEY`、`BLOG_ADMIN_EMAIL`、`BLOG_ADMIN_PASSWORD`（仅当库中还没有管理员时需要后两项以创建首管）。
3. 同一域名下由 Flask 提供前端静态页与 `/api`、`/assets`（样式与脚本）。若前后端域名不同，需正确配置 CORS 与 Cookie（本项目默认同源 `credentials`）。头像与正文插图存于 `media_asset`（`GET /api/media/<uuid>`），外链图用 `POST /api/uploads/article-image` 的 JSON `url`。

GitHub Pages 仅静态托管时无法直接调 Flask，需把 API 指到独立后端域名并在网关处理跨域与 Cookie 策略。
