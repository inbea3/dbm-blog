#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人博客后端API
使用Flask实现RESTful API，提供博客文章数据接口
"""

from flask import Flask, jsonify, send_from_directory, request, session
from flask_cors import CORS
import json
import os
from werkzeug.utils import secure_filename
import re
import html
import markdown as mdlib
from functools import wraps
import secrets
import datetime

# 创建Flask应用
app = Flask(__name__)
app.secret_key = os.environ.get('BLOG_SECRET_KEY') or secrets.token_hex(32)
SERVER_NONCE = secrets.token_hex(16)

# 启用CORS跨域支持
CORS(app, supports_credentials=True)

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data.json')
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'frontend'))
ASSETS_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'assets'))
ARTICLES_DIR = os.path.join(ASSETS_DIR, 'articles')
UPLOADS_DIR = os.path.join(ASSETS_DIR, 'uploads')

def _get_env(name, default=None):
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip()
    return v if v else default

# 管理员账号（来自环境变量；避免把账号密码写进仓库）
ADMIN_EMAIL = _get_env('BLOG_ADMIN_EMAIL')
ADMIN_PASSWORD = _get_env('BLOG_ADMIN_PASSWORD')

# 未配置管理员时：本地开发给一个一次性随机密码（仅进程生命周期有效）
_EPHEMERAL_ADMIN_PASSWORD = None
if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    _EPHEMERAL_ADMIN_PASSWORD = secrets.token_urlsafe(12)
    ADMIN_EMAIL = ADMIN_EMAIL or 'admin@local'
    ADMIN_PASSWORD = ADMIN_PASSWORD or _EPHEMERAL_ADMIN_PASSWORD

def is_admin():
    return session.get('is_admin') is True and session.get('server_nonce') == SERVER_NONCE

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            # 后端重启导致 nonce 不匹配时，清理旧登录态
            session.clear()
            return jsonify({'error': '您作为游客，无法编辑'}), 403
        return fn(*args, **kwargs)
    return wrapper

def load_blog_data():
    """加载博客数据"""
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # 如果数据文件不存在，返回默认数据结构
        return {
            "author": {
                "name": "zimu",
                "bio": "全栈开发工程师，热爱编程和分享",
                "skills": ["Python", "JavaScript", "HTML/CSS", "Flask"],
                "social": {
                    "gitee": "https://gitee.com/zimu",
                    "email": "zimu@example.com",
                    "qq": "12345678"
                }
            },
            "articles": []
        }

def save_blog_data(data):
    """保存博客数据"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _safe_text(v):
    if v is None:
        return ''
    return str(v).strip()

def _parse_frontmatter(text):
    """
    Very small frontmatter parser:
    ---
    key: value
    ---
    body...
    """
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip('\n')
    body = text[end + len('\n---\n'):]
    meta = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        meta[k.strip()] = v.strip()
    return meta, body

def _build_frontmatter(meta):
    lines = ['---']
    for k in ['id', 'title', 'date', 'author', 'summary', 'style', 'format']:
        if k in meta and meta[k] is not None:
            lines.append(f'{k}: {_safe_text(meta[k])}')
    lines.append('---')
    return '\n'.join(lines) + '\n'

def _article_path(article_id, fmt):
    ext = 'md' if fmt == 'md' else 'txt'
    return os.path.join(ARTICLES_DIR, f'{int(article_id)}.{ext}')

def _load_article_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    meta, body = _parse_frontmatter(text)
    fmt = meta.get('format')
    if not fmt:
        _, ext = os.path.splitext(path.lower())
        fmt = 'md' if ext == '.md' else 'txt'
    meta['format'] = fmt
    # id from filename wins
    base = os.path.basename(path)
    m = re.match(r'^(\d+)\.', base)
    if m:
        meta['id'] = int(m.group(1))
    return meta, body

def _render_article_to_html(fmt, body):
    if fmt == 'txt':
        return '<pre>' + html.escape(body) + '</pre>'
    # Markdown (allow raw HTML in content)
    return mdlib.markdown(
        body,
        extensions=['fenced_code', 'tables', 'toc'],
        output_format='html5'
    )

def _compute_summary(body, fmt, limit=120):
    if not body:
        return ''
    if fmt == 'txt':
        s = body.strip().replace('\r\n', '\n').replace('\n', ' ')
        return (s[:limit] + '...') if len(s) > limit else s
    # remove markdown links/images/code fences roughly
    s = re.sub(r'```[\s\S]*?```', '', body)
    s = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', s)
    s = re.sub(r'\[[^\]]*\]\([^)]+\)', '', s)
    s = re.sub(r'[#>*_`]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return (s[:limit] + '...') if len(s) > limit else s

def ensure_articles_storage():
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)

def migrate_articles_from_data_json():
    """
    One-way migration: if assets/articles is empty and data.json has articles,
    write them to assets/articles/{id}.md with frontmatter. Keep data.json articles as-is
    (backward compatible), but new APIs prefer file storage.
    """
    ensure_articles_storage()
    existing = [n for n in os.listdir(ARTICLES_DIR) if n.lower().endswith(('.md', '.txt'))]
    if existing:
        return
    data = load_blog_data()
    articles = data.get('articles') or []
    for a in articles:
        try:
            aid = int(a.get('id'))
        except Exception:
            continue
        meta = {
            'id': aid,
            'title': a.get('title', ''),
            'date': a.get('date', ''),
            'author': a.get('author', ''),
            'summary': a.get('summary', ''),
            'style': a.get('style', 'default'),
            'format': 'md'
        }
        # keep existing HTML as markdown body (markdown allows raw HTML)
        body = a.get('content', '') or ''
        path = _article_path(aid, 'md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_build_frontmatter(meta))
            f.write(body)

def list_articles():
    migrate_articles_from_data_json()
    ensure_articles_storage()
    items = []
    for name in os.listdir(ARTICLES_DIR):
        low = name.lower()
        if not (low.endswith('.md') or low.endswith('.txt')):
            continue
        path = os.path.join(ARTICLES_DIR, name)
        meta, body = _load_article_file(path)
        fmt = meta.get('format', 'md')
        summary = meta.get('summary') or _compute_summary(body, fmt)
        items.append({
            'id': int(meta.get('id')),
            'title': meta.get('title', ''),
            'date': meta.get('date', ''),
            'author': meta.get('author', ''),
            'summary': summary,
            'style': meta.get('style', 'default'),
            'format': fmt
        })
    items.sort(key=lambda x: x['id'])
    return items

def get_article_by_id(article_id):
    migrate_articles_from_data_json()
    ensure_articles_storage()
    for ext in ('.md', '.txt'):
        path = os.path.join(ARTICLES_DIR, f'{int(article_id)}{ext}')
        if os.path.exists(path):
            meta, body = _load_article_file(path)
            fmt = meta.get('format', 'md')
            meta_out = {
                'id': int(meta.get('id', article_id)),
                'title': meta.get('title', ''),
                'date': meta.get('date', ''),
                'author': meta.get('author', ''),
                'summary': meta.get('summary') or _compute_summary(body, fmt),
                'style': meta.get('style', 'default'),
                'format': fmt
            }
            return meta_out, body
    return None, None

def next_article_id():
    items = list_articles()
    return (max([a['id'] for a in items]) + 1) if items else 1

def save_article(article_id, meta, body):
    ensure_articles_storage()
    fmt = meta.get('format', 'md')
    meta = dict(meta)
    meta['id'] = int(article_id)
    path = _article_path(article_id, fmt)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(_build_frontmatter(meta))
        f.write(body or '')
    # if format changed, remove other ext file
    other = _article_path(article_id, 'txt' if fmt == 'md' else 'md')
    if os.path.exists(other):
        try:
            os.remove(other)
        except Exception:
            pass
    return path

@app.route('/api/articles', methods=['GET'])
def get_articles():
    """获取所有文章列表"""
    return jsonify(list_articles())


@app.route('/api/articles', methods=['POST'])
@admin_required
def create_article():
    """创建文章（写作保存）"""
    payload = request.get_json(silent=True) or {}
    title = _safe_text(payload.get('title'))
    body = payload.get('content') or ''
    fmt = _safe_text(payload.get('format')) or 'md'
    if fmt not in ('md', 'txt'):
        fmt = 'md'

    article_id = next_article_id()
    meta = {
        'title': title or f'未命名文章 {article_id}',
        'date': _safe_text(payload.get('date')) or datetime.date.today().isoformat(),
        'author': _safe_text(payload.get('author')) or _safe_text(load_blog_data().get('author', {}).get('name')) or 'author',
        'summary': _safe_text(payload.get('summary')) or '',
        'style': _safe_text(payload.get('style')) or 'default',
        'format': fmt
    }
    if not meta['summary']:
        meta['summary'] = _compute_summary(body, fmt)
    save_article(article_id, meta, body)
    meta_out, raw = get_article_by_id(article_id)
    meta_out['content'] = _render_article_to_html(meta_out.get('format', 'md'), raw)
    meta_out['raw'] = raw
    return jsonify(meta_out), 201

@app.route('/api/articles/<int:article_id>', methods=['GET'])
def get_article(article_id):
    """获取单篇文章详情"""
    meta, raw = get_article_by_id(article_id)
    if not meta:
        return jsonify({'error': '文章未找到'}), 404
    return jsonify({
        **meta,
        'content': _render_article_to_html(meta.get('format', 'md'), raw),
        'raw': raw
    })


@app.route('/api/articles/<int:article_id>', methods=['PUT'])
@admin_required
def update_article(article_id):
    """更新文章（编辑保存）"""
    meta_existing, _raw_existing = get_article_by_id(article_id)
    if not meta_existing:
        return jsonify({'error': '文章未找到'}), 404

    payload = request.get_json(silent=True) or {}
    title = _safe_text(payload.get('title')) or meta_existing.get('title', '')
    body = payload.get('content') if payload.get('content') is not None else ''
    fmt = _safe_text(payload.get('format')) or meta_existing.get('format', 'md')
    if fmt not in ('md', 'txt'):
        fmt = meta_existing.get('format', 'md')

    meta = {
        'title': title,
        'date': _safe_text(payload.get('date')) or meta_existing.get('date', '') or datetime.date.today().isoformat(),
        'author': _safe_text(payload.get('author')) or meta_existing.get('author', ''),
        'summary': _safe_text(payload.get('summary')) or '',
        'style': _safe_text(payload.get('style')) or meta_existing.get('style', 'default'),
        'format': fmt
    }
    if not meta['summary']:
        meta['summary'] = _compute_summary(body, fmt)

    save_article(article_id, meta, body)
    meta_out, raw = get_article_by_id(article_id)
    return jsonify({
        **meta_out,
        'content': _render_article_to_html(meta_out.get('format', 'md'), raw),
        'raw': raw
    })


@app.route('/api/articles/<int:article_id>', methods=['DELETE'])
@admin_required
def delete_article(article_id):
    """删除文章（同时删除 assets/articles 文件）"""
    meta, _raw = get_article_by_id(article_id)
    if not meta:
        return jsonify({'error': '文章未找到'}), 404

    deleted = False
    for ext in ('.md', '.txt'):
        path = os.path.join(ARTICLES_DIR, f'{int(article_id)}{ext}')
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted = True
            except Exception:
                return jsonify({'error': '删除失败'}), 500

    return jsonify({'ok': True, 'deleted': deleted, 'id': int(article_id)})


@app.route('/api/uploads/article-image', methods=['POST'])
@admin_required
def upload_article_image():
    """上传文章图片，返回可引用 URL（用于编辑器插图）"""
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段 file'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename.lower())
    allowed = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    if ext not in allowed:
        return jsonify({'error': f'不支持的文件类型: {ext}'}), 400

    subdir = os.path.join(UPLOADS_DIR, 'articles')
    os.makedirs(subdir, exist_ok=True)
    # unique-ish name
    import time
    saved_name = f'img_{int(time.time()*1000)}{ext}'
    saved_path = os.path.join(subdir, saved_name)
    file.save(saved_path)
    return jsonify({'url': f'/assets/uploads/articles/{saved_name}'})

@app.route('/api/author', methods=['GET'])
def get_author():
    """获取作者信息"""
    data = load_blog_data()
    return jsonify(data['author'])


@app.route('/api/me', methods=['GET'])
def me():
    """当前登录信息"""
    # 后端重启后 nonce 会变化：如果旧 session 里仍标记 is_admin，则清理它
    if session.get('is_admin') is True and session.get('server_nonce') != SERVER_NONCE:
        session.clear()
    return jsonify({'admin': is_admin(), 'email': ADMIN_EMAIL if is_admin() else None})


@app.route('/api/login', methods=['POST'])
def login():
    payload = request.get_json(silent=True) or {}
    email = _safe_text(payload.get('email'))
    password = _safe_text(payload.get('password'))
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
        session['is_admin'] = True
        session['server_nonce'] = SERVER_NONCE
        return jsonify({'ok': True, 'admin': True, 'email': ADMIN_EMAIL})
    session['is_admin'] = False
    session.pop('server_nonce', None)
    return jsonify({'ok': False, 'error': '您作为游客，无法编辑'}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/author/avatar', methods=['POST'])
@admin_required
def upload_author_avatar():
    """上传并更新作者头像（保存到 assets/uploads）"""
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段 file'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename.lower())
    allowed = {'.png', '.jpg', '.jpeg', '.webp'}
    if ext not in allowed:
        return jsonify({'error': f'不支持的文件类型: {ext}，仅支持 png/jpg/jpeg/webp'}), 400

    upload_dir = os.path.join(ASSETS_DIR, 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    # 固定文件名，方便前端引用；ext 跟随上传文件
    saved_name = f'avatar{ext}'
    saved_path = os.path.join(upload_dir, saved_name)
    file.save(saved_path)

    data = load_blog_data()
    data.setdefault('author', {})
    data['author']['avatar'] = f'/assets/uploads/{saved_name}'
    save_blog_data(data)

    return jsonify(data['author'])

@app.route('/')
def index():
    """首页"""
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/blog')
def blog():
    """博客列表页"""
    return send_from_directory(FRONTEND_DIR, 'blog.html')

@app.route('/about')
def about():
    """关于我页面"""
    return send_from_directory(FRONTEND_DIR, 'about.html')

@app.route('/detail/<int:article_id>')
def detail(article_id):
    """文章详情页"""
    return send_from_directory(FRONTEND_DIR, 'detail.html')


@app.route('/index.html')
def index_html():
    """与静态链接 index.html 对齐，避免导航 404"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/blog.html')
def blog_html():
    return send_from_directory(FRONTEND_DIR, 'blog.html')


@app.route('/about.html')
def about_html():
    return send_from_directory(FRONTEND_DIR, 'about.html')


@app.route('/detail.html')
def detail_html():
    """支持 detail.html?id= 形式，查询串由浏览器保留"""
    return send_from_directory(FRONTEND_DIR, 'detail.html')

@app.route('/editor')
def editor():
    """写作/编辑页面"""
    return send_from_directory(FRONTEND_DIR, 'editor.html')


@app.route('/editor.html')
def editor_html():
    return send_from_directory(FRONTEND_DIR, 'editor.html')

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """提供静态资源服务"""
    return send_from_directory(ASSETS_DIR, filename)

if __name__ == '__main__':
    print("启动个人博客后端服务...")
    print("访问地址: http://localhost:5000")
    if _EPHEMERAL_ADMIN_PASSWORD:
        print("检测到未配置管理员环境变量 BLOG_ADMIN_EMAIL/BLOG_ADMIN_PASSWORD")
        print(f"本次启动临时管理员账号: {ADMIN_EMAIL}")
        print(f"本次启动临时管理员密码: {_EPHEMERAL_ADMIN_PASSWORD}")
        print("提示：线上部署请务必在环境变量里设置 BLOG_ADMIN_EMAIL/BLOG_ADMIN_PASSWORD")

    port = int(_get_env('PORT', '5000'))
    app.run(debug=True, host='0.0.0.0', port=port)