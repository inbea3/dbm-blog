# 个人博客 - 启动和部署指南

## 本地启动

### 前置要求

- Python 3.7+
- pip（Python包管理器）

### 安装步骤

1. **克隆或下载项目**

```bash
cd blog
```

2. **安装后端依赖**

```bash
cd backend
pip install -r requirements.txt
```

3. **启动后端服务**

```bash
python app.py
```

4. **访问博客**

打开浏览器，访问以下地址：
- 首页：http://localhost:5000
- 博客列表：http://localhost:5000/blog
- 关于我：http://localhost:5000/about

### 修改配置

**修改作者信息：**
编辑 `backend/data.json` 文件，修改 `author` 部分：

```json
{
  "author": {
    "name": "你的名字",
    "bio": "你的个人简介",
    "skills": ["技能1", "技能2", "技能3"],
    "social": {
      "github": "https://github.com/yourname",
      "twitter": "https://twitter.com/yourname",
      "email": "yourname@example.com"
    }
  }
}
```

**添加新文章：**
在 `backend/data.json` 的 `articles` 数组中添加新文章：

```json
{
  "id": 6,
  "title": "新文章标题",
  "date": "2024-04-22",
  "author": "作者名",
  "summary": "文章摘要",
  "content": "<p>文章内容，支持HTML格式</p>"
}
```

### 端口修改

如果需要修改端口号，优先使用环境变量 `PORT`（云端平台通常会自动注入）。
本地也可以直接设置 `PORT=5001` 之类再启动。

如果你一定要写死端口号，再编辑 `backend/app.py` 最后一行：

```python
app.run(debug=True, host='0.0.0.0', port=5000)  # 修改端口号
```

## 部署到云端

### 前端部署到 GitHub Pages

1. **创建GitHub仓库**

   在GitHub上创建一个新仓库，命名为 `yourname.github.io`

2. **上传前端文件**

```bash
cd frontend
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/yourname/yourname.github.io.git
git push -u origin main
```

3. **启用GitHub Pages**

   - 进入仓库的 Settings
   - 找到 "Pages" 选项
   - 在 "Source" 下选择 "main" 分支
   - 点击 "Save"

4. **访问你的网站**

   访问 `https://yourname.github.io`

**注意：** 前端部署到GitHub Pages后，需要修改API地址为你的后端服务器地址。

### 后端部署到 Render

1. **创建Render账号**

   访问 [render.com](https://render.com) 并注册账号

2. **创建新的Web服务**

   - 点击 "New +" 按钮
   - 选择 "Web Service"
   - 连接你的GitHub仓库
   - 配置以下设置：

   **Build Command:**
   ```bash
   pip install -r backend/requirements.txt
   ```

   **Start Command:**
   ```bash
   cd backend && gunicorn app:app --bind 0.0.0.0:$PORT
   ```

3. **配置环境变量**

   在 "Environment" 部分添加：
   - `PYTHON_VERSION`: 3.9.0
   - `BLOG_SECRET_KEY`: （强烈建议设置为随机长字符串）
   - `BLOG_ADMIN_EMAIL`: 管理员邮箱（用于编辑器登录）
   - `BLOG_ADMIN_PASSWORD`: 管理员密码（用于编辑器登录）

4. **部署**

   点击 "Create Web Service" 开始部署

5. **获取API地址**

   部署完成后，Render会提供你的服务地址，类似：
   `https://your-app.onrender.com`

6. **更新前端API地址**

   修改前端HTML文件中的API地址：

```javascript
// 将所有文件中的
const API_BASE_URL = 'http://localhost:5000/api';

// 修改为
const API_BASE_URL = 'https://your-app.onrender.com/api';
```

### 后端部署到 Heroku

1. **安装Heroku CLI**

   下载并安装 [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)

2. **登录Heroku**

```bash
heroku login
```

3. **创建Heroku应用**

```bash
heroku create your-blog-name
```

4. **创建Procfile**

   在项目根目录创建 `Procfile` 文件：

```
web: cd backend && python app.py
```

5. **创建requirements.txt**

   确保在 `backend/requirements.txt` 中包含：
```
flask==3.0.0
flask-cors==4.0.0
gunicorn==21.2.0
```

6. **部署到Heroku**

```bash
git init
git add .
git commit -m "Initial commit"
git push heroku main
```

7. **获取API地址**

   访问 `https://your-blog-name.herokuapp.com`

## 域名配置

### 为GitHub Pages配置自定义域名

1. **购买域名**

   在域名注册商处购买域名

2. **配置DNS**

   添加以下DNS记录：
   - A记录：`@` → `185.199.108.153`
   - A记录：`@` → `185.199.109.153`
   - A记录：`@` → `185.199.110.153`
   - A记录：`@` → `185.199.111.153`

3. **在GitHub中配置**

   - 进入仓库Settings → Pages
   - 在 "Custom domain" 中输入你的域名
   - 点击 "Save"

## 维护和更新

### 更新博客内容

1. 修改 `backend/data.json` 文件
2. 提交更改到Git仓库
3. 如果部署了，等待自动部署完成

### 添加新功能

1. 在 `backend/app.py` 中添加新的API端点
2. 在前端HTML文件中添加相应功能
3. 测试功能是否正常
4. 部署更新

## 常见问题

### Q: 后端服务启动失败

**A:** 检查以下几点：
- Python版本是否正确
- 是否安装了所有依赖
- 端口是否被占用
- 文件路径是否正确

### Q: 前端无法连接后端

**A:** 确认：
- 后端服务是否正在运行
- API地址是否正确
- 是否存在跨域问题（已使用flask-cors解决）

### Q: 部署后页面空白

**A:** 检查：
- 静态文件路径是否正确
- API地址是否已更新为生产环境地址
- 浏览器控制台是否有错误信息

## 技术支持

如遇到问题，请检查：
1. 浏览器开发者工具的控制台错误
2. 后端服务日志
3. 网络连接状态

## 许可证

本项目仅供学习和个人使用。