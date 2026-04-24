# 项目总结（Blog）

本项目是一个 **Flask + 原生 HTML/JS** 的轻量博客系统，支持：
- 游客只读浏览文章
- 管理员登录后在线写作/编辑文章、上传插图、上传头像
- 文章以文件形式存储在 `assets/articles/`，便于备份与管理

## 1. 启动与访问

### 启动后端

在项目根目录执行：

```bash
python backend/app.py
```

或先安装依赖：

```bash
python -m pip install -r backend/requirements.txt
python backend/app.py
```

默认访问：
- 首页：`http://localhost:5000/index.html`
- 列表：`http://localhost:5000/blog.html`
- 详情：`http://localhost:5000/detail.html?id=1`（或 `/detail/1`）
- 关于：`http://localhost:5000/about.html`
- 编辑器：`http://localhost:5000/editor.html`

## 2. 项目结构

```text
blog/
  backend/
    app.py                 # Flask 后端 + API + 登录鉴权 + 文章文件化存储
    data.json              # 唯一作者信息源（头像/联系方式/技能等）
    requirements.txt       # Flask + CORS + Markdown

  frontend/
    index.html             # 首页（游客/管理员右上角头像区）
    blog.html              # 文章列表（管理员才显示“写博客/编辑”入口）
    detail.html            # 文章详情（展示后端渲染后的 HTML）
    about.html             # 关于页（头像大图 + 右下角钢笔上传入口）
    editor.html            # 在线写作/编辑器（标题/风格/正文/插图/保存）

  assets/
    style.css              # 全站样式（中英字体分流、头像缩略图、小灰人等）
    script.js              # 全站通用 JS（暗色模式、Auth/UI、Contact、Author、API 封装）
    qq_qr.svg              # QQ 二维码
    articles/              # ✅ 文章文件存储目录（每篇一个文件）
      1.md
      2.md
      ...
    uploads/               # ✅ 上传目录
      avatar.jpg|png|webp  # 头像（固定命名 avatar.xxx）
      articles/            # 文章插图
        img_*.png|jpg|webp|gif
```

## 3. 数据与存储策略

### 3.1 作者信息（唯一真实数据源）

作者信息只维护在：
- `backend/data.json` → `author`

后端通过：
- `GET /api/author`

向前端提供作者资料（含 `social` 与 `avatar`）。

### 3.2 文章存储（文件化）

文章不再存于 `data.json`，而是存于：
- `assets/articles/{id}.md` 或 `assets/articles/{id}.txt`

每篇文章文件包含一个轻量 frontmatter（非 YAML 全量实现，仅支持 `key: value`）：

```text
---
id: 6
title: 标题
date: 2026-04-22
author: zimu
summary: 摘要
style: tech
format: md
---
正文...
```

### 3.3 自动迁移

为了兼容旧数据：后端首次启动时，如果 `assets/articles/` 为空且 `backend/data.json` 有 `articles`，会自动把旧 `articles` **迁移写入** `assets/articles/*.md`（Markdown 允许保留原 HTML 内容，确保不丢失格式）。

## 4. 权限与登录流程（游客 vs 管理员）

### 4.1 默认游客

任何用户初次进入页面都是 **游客**：
- 只能浏览文章
- 右上角显示 **小灰人**
- “写博客 / 编辑 / 上传头像 / 插入图片 / 保存”等功能不可用

### 4.2 管理员账号（固定）

管理员登录仅允许这一组：
- **邮箱**：`sufer76980@163.com`
- **密码**：`12345678`

### 4.3 前端交互

- 点击右上角小灰人，或点击导航“关于我”
  - 弹出登录弹窗
  - 输入账号密码后登录
  - 登录失败提示：`您作为游客，无法编辑`

### 4.4 后端会话

后端使用 **Flask session cookie** 记录管理员状态：
- `GET /api/me`：查看是否已登录管理员
- `POST /api/login`：登录
- `POST /api/logout`：退出

### 4.5 写接口保护（403）

以下接口均要求管理员，否则返回 403：
- `POST /api/articles`（发帖）
- `PUT /api/articles/<id>`（改帖）
- `POST /api/uploads/article-image`（插图上传）
- `POST /api/author/avatar`（头像上传）

## 5. 文章 API（核心）

- `GET /api/articles`
  - 返回文章列表（`id/title/date/author/summary/style/format`）

- `GET /api/articles/<id>`
  - 返回单篇详情：
    - `raw`：源文本（md/txt）
    - `content`：后端渲染后的 HTML（详情页直接展示）

- `POST /api/articles`（管理员）
  - 请求体：`{ title, style, format, content, summary?, date?, author? }`

- `PUT /api/articles/<id>`（管理员）
  - 请求体同上，用于随时修改任意文章

## 6. 在线写作/编辑（editor.html）

### 新建
- 进入 `editor.html`
- 填写标题/正文，选择格式（md/txt）与风格
- 可插入图片（上传后自动插入链接）
- 点击保存后创建文章并跳转为 `editor.html?id=<newId>`

### 编辑
- 通过 `blog.html` 的“编辑”按钮进入 `editor.html?id=<id>`
- 修改后保存，后端会更新 `assets/articles/{id}.md|txt`

## 7. 上传与静态资源

- 静态资源由后端提供：
  - `GET /assets/<path>`

- 头像上传（管理员）：
  - `POST /api/author/avatar`（form-data: `file`）
  - 写入 `assets/uploads/avatar.xxx`
  - 更新 `backend/data.json` 的 `author.avatar`

- 文章插图上传（管理员）：
  - `POST /api/uploads/article-image`（form-data: `file`）
  - 写入 `assets/uploads/articles/img_*.xxx`
  - 返回可直接插入正文的 URL

## 8. 注意事项

- **安全性**：当前为“本地/小范围使用”的轻量鉴权方案（固定账号密码 + session），不适合直接裸奔公网。
- **备份**：备份重点目录：
  - `backend/data.json`
  - `assets/articles/`
  - `assets/uploads/`
- **访问方式**：建议始终通过 `http://localhost:5000` 打开页面；直接 `file://` 打开会缺少后端能力（无法登录/写作/上传）。

