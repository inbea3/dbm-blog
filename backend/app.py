#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import secrets
import uuid
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, redirect, send_from_directory, request, session
from flask_cors import CORS
from werkzeug.utils import secure_filename

from neon_db import NeonDatabase
import postgres_store

if not NeonDatabase.resolve_dsn():
    raise RuntimeError(
        "未配置数据库：请设置环境变量 DATABASE_URL（或 BLOG_DATABASE_URL），"
        "或同时设置 PGHOST、PGDATABASE、PGUSER、PGPASSWORD（可选 PGSSLMODE、PGCHANNELBINDING）。"
    )

app = Flask(__name__)
app.secret_key = os.environ.get('BLOG_SECRET_KEY') or secrets.token_hex(32)
SERVER_NONCE = secrets.token_hex(16)

_ALLOWED_ORIGINS = [o.rstrip('/') for o in os.environ.get(
    'BLOG_ALLOWED_ORIGINS',
    'https://inbea3.github.io,http://localhost:5000,http://127.0.0.1:5000'
).split(',') if o.strip()]
CORS(app, supports_credentials=True, origins=_ALLOWED_ORIGINS)

app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'frontend'))
ASSETS_DIR = os.path.abspath(os.path.join(FRONTEND_DIR, 'assets'))


def _get_env(name, default=None):
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip()
    return v if v else default


ADMIN_EMAIL = _get_env('BLOG_ADMIN_EMAIL')
ADMIN_PASSWORD = _get_env('BLOG_ADMIN_PASSWORD')

postgres_store.bootstrap_if_needed(ADMIN_EMAIL or '', ADMIN_PASSWORD or '')


@app.before_request
def _ensure_guest_visitor():
    """未登录用户：保证 session 中有 visitor_id，并在库中有对应 visitor 行（首次访问即 first_seen_at）。"""
    if _session_user_id():
        return
    postgres_store.ensure_session_visitor_id(session)
    session.permanent = True


def _session_visitor_id() -> uuid.UUID | None:
    raw = session.get("visitor_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except Exception:
        return None


def _session_user_id() -> uuid.UUID | None:
    if session.get('server_nonce') != SERVER_NONCE:
        return None
    uid = session.get('user_id')
    if not uid:
        return None
    try:
        return uuid.UUID(str(uid))
    except Exception:
        return None


def is_admin():
    if session.get('server_nonce') != SERVER_NONCE:
        return False
    if session.get('user_role') != 'admin':
        return False
    uid = _session_user_id()
    if not uid:
        return False
    return postgres_store.verify_admin_user_id(uid)


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({'error': '您作为游客，无法编辑'}), 403
        return fn(*args, **kwargs)
    return wrapper


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _parse_article_id_param(raw: str | None) -> tuple[uuid.UUID | None, str | None]:
    if not raw or not str(raw).strip():
        return None, None
    try:
        u = uuid.UUID(str(raw).strip())
    except ValueError:
        return None, "bad_uuid"
    if not postgres_store.article_exists(u):
        return None, "missing_article"
    return u, None


@app.route('/api/media/<uuid:media_id>')
def serve_media(media_id: uuid.UUID):
    row = postgres_store.get_media_payload(media_id)
    if not row:
        return "Not Found", 404
    blob = row.get("content")
    if blob:
        return Response(blob, mimetype=row["mime_type"])
    url = (row.get("public_url") or "").strip()
    if url.startswith(("http://", "https://")):
        return redirect(url, code=302)
    return "Not Found", 404


@app.route('/api/articles', methods=['GET'])
def get_articles():
    return jsonify(postgres_store.list_articles(include_drafts=is_admin()))


@app.route('/api/articles', methods=['POST'])
@admin_required
def create_article():
    payload = request.get_json(silent=True) or {}
    uid = _session_user_id()
    if not uid:
        return jsonify({'error': '未登录'}), 401
    try:
        out = postgres_store.create_article(payload, uid)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(out), 201


def _viewer_for_article():
    uid = _session_user_id()
    if uid:
        return uid, None
    postgres_store.ensure_session_visitor_id(session)
    return None, _session_visitor_id()


@app.route('/api/articles/<article_id>', methods=['GET'])
def get_article(article_id):
    try:
        aid = uuid.UUID(str(article_id))
    except Exception:
        return jsonify({'error': '文章未找到'}), 404
    uid, vid = _viewer_for_article()
    row = postgres_store.get_article_dict(aid, allow_draft=is_admin(), viewer_user_id=uid, visitor_id=vid)
    if not row:
        return jsonify({'error': '文章未找到'}), 404
    return jsonify(row)


@app.route('/api/articles/<article_id>/comments', methods=['POST'])
def post_comment(article_id):
    try:
        aid = uuid.UUID(str(article_id))
    except Exception:
        return jsonify({'error': '文章未找到'}), 404
    payload = request.get_json(silent=True) or {}
    body = (payload.get('body') or '').strip()
    guest_name = (payload.get('guest_name') or '').strip()
    uid = _session_user_id()
    try:
        if is_admin() and uid:
            postgres_store.add_article_comment(aid, body, user_id=uid, guest_name=None)
        else:
            if not guest_name:
                return jsonify({'error': '请填写昵称后再发表评论'}), 400
            vid = _session_visitor_id()
            if not vid:
                return jsonify({'error': '会话异常，请刷新页面'}), 400
            postgres_store.add_article_comment(
                aid, body, user_id=None, guest_name=guest_name, visitor_id=vid
            )
    except LookupError:
        return jsonify({'error': '文章未找到'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    uid2, vid2 = _viewer_for_article()
    row = postgres_store.get_article_dict(aid, allow_draft=is_admin(), viewer_user_id=uid2, visitor_id=vid2)
    return jsonify({'ok': True, 'comments': row.get('comments', []) if row else []})


@app.route('/api/articles/<article_id>/reactions', methods=['POST'])
def post_reaction(article_id):
    try:
        aid = uuid.UUID(str(article_id))
    except Exception:
        return jsonify({'error': '文章未找到'}), 404
    payload = request.get_json(silent=True) or {}
    kind = (payload.get('kind') or 'none').strip()
    uid = _session_user_id()
    if uid:
        vid = None
    else:
        postgres_store.ensure_session_visitor_id(session)
        vid = _session_visitor_id()
    try:
        out = postgres_store.set_article_reaction(
            aid, kind, user_id=uid, visitor_id=vid if not uid else None
        )
    except LookupError:
        return jsonify({'error': '文章未找到'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(out)


@app.route('/api/articles/<article_id>', methods=['PUT'])
@admin_required
def update_article(article_id):
    try:
        aid = uuid.UUID(str(article_id))
    except Exception:
        return jsonify({'error': '文章未找到'}), 404
    try:
        out = postgres_store.update_article(aid, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not out:
        return jsonify({'error': '文章未找到'}), 404
    return jsonify(out)


@app.route('/api/articles/<article_id>', methods=['DELETE'])
@admin_required
def delete_article(article_id):
    try:
        aid = uuid.UUID(str(article_id))
    except Exception:
        return jsonify({'error': '文章未找到'}), 404
    if not postgres_store.delete_article(aid):
        return jsonify({'error': '文章未找到'}), 404
    return jsonify({'ok': True, 'deleted': True, 'id': str(aid)})


@app.route('/api/categories', methods=['GET'])
def api_categories():
    return jsonify(postgres_store.list_categories())


@app.route('/api/tags', methods=['GET'])
def api_tags():
    return jsonify(postgres_store.list_tags())


@app.route('/api/uploads/article-image', methods=['POST'])
@admin_required
def upload_article_image():
    payload = request.get_json(silent=True) or {}
    raw_url = (payload.get('url') or '').strip()
    if raw_url:
        article_id, err = _parse_article_id_param(str(payload.get('article_id') or ''))
        if err == "bad_uuid":
            return jsonify({"error": "article_id 不是合法 UUID"}), 400
        if err == "missing_article":
            return jsonify({"error": "未找到对应文章"}), 404
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return jsonify({"error": "url 须为 http(s) 且包含主机名"}), 400
        storage_key = f"external:{uuid.uuid4().hex}"
        postgres_store.insert_article_image_external(raw_url, storage_key, article_id)
        return jsonify({"url": raw_url})

    article_id, err = _parse_article_id_param(request.form.get("article_id"))
    if err == "bad_uuid":
        return jsonify({"error": "article_id 不是合法 UUID"}), 400
    if err == "missing_article":
        return jsonify({"error": "未找到对应文章"}), 404

    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": '请上传 file，或发送 JSON：{"url":"https://..."}'}), 400

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename.lower())
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    if ext not in allowed:
        return jsonify({"error": f"不支持的文件类型: {ext}"}), 400

    data = file.read()
    if not data:
        return jsonify({"error": "空文件"}), 400
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    mid = postgres_store.insert_article_image_blob(data, mime, article_id)
    return jsonify({"url": f"/api/media/{mid}"})


@app.route('/api/author', methods=['GET'])
def get_author():
    return jsonify(postgres_store.get_author_json())


@app.route('/api/me', methods=['GET'])
def me():
    if session.get('server_nonce') != SERVER_NONCE and session.get('user_id'):
        session.pop('user_id', None)
        session.pop('user_role', None)
        session.pop('is_admin', None)
        session.pop('server_nonce', None)
    uid = _session_user_id()
    if not uid:
        postgres_store.ensure_session_visitor_id(session)
        vid = _session_visitor_id()
        vinf = postgres_store.get_visitor_public(vid) if vid else None
        return jsonify(
            {
                'admin': False,
                'email': None,
                'nickname': None,
                'user_id': None,
                'visitor': vinf,
            }
        )
    role = session.get('user_role')
    admin = role == 'admin' and postgres_store.verify_admin_user_id(uid)
    if not admin:
        session.pop('user_id', None)
        session.pop('user_role', None)
        session.pop('is_admin', None)
        session.pop('server_nonce', None)
        postgres_store.ensure_session_visitor_id(session)
        vid = _session_visitor_id()
        vinf = postgres_store.get_visitor_public(vid) if vid else None
        return jsonify(
            {
                'admin': False,
                'email': None,
                'nickname': None,
                'user_id': None,
                'visitor': vinf,
            }
        )
    em = postgres_store.user_email_by_id(uid)
    nick = postgres_store.user_nickname_by_id(uid)
    return jsonify(
        {
            'admin': True,
            'email': em,
            'nickname': nick,
            'user_id': str(uid),
            'visitor': None,
        }
    )


@app.route('/api/visitor/nickname', methods=['POST'])
def visitor_set_nickname():
    if _session_user_id():
        return jsonify({'error': '已登录用户请使用「关于我」修改资料'}), 400
    payload = request.get_json(silent=True) or {}
    nick = (payload.get('nickname') or '').strip()
    if not nick:
        return jsonify({'error': '请填写昵称'}), 400
    postgres_store.ensure_session_visitor_id(session)
    vid = _session_visitor_id()
    if not vid:
        return jsonify({'error': '会话异常，请刷新页面'}), 400
    try:
        postgres_store.update_visitor_nickname(vid, nick)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'visitor': postgres_store.get_visitor_public(vid)})


@app.route('/api/login', methods=['POST'])
def login():
    payload = request.get_json(silent=True) or {}
    email = (payload.get('email') or '').strip()
    password = (payload.get('password') or '').strip()
    row = postgres_store.authenticate_user(email, password)
    if row:
        session.pop('visitor_id', None)
        session['user_id'] = row['id']
        session['user_role'] = row['role']
        session['is_admin'] = row['role'] == 'admin'
        session['server_nonce'] = SERVER_NONCE
        return jsonify(
            {
                'ok': True,
                'admin': True,
                'email': row['email'],
                'nickname': row.get('nickname') or '',
            }
        )
    session.pop('user_id', None)
    session.pop('user_role', None)
    session.pop('is_admin', None)
    session.pop('server_nonce', None)
    return jsonify({'ok': False, 'error': '账号或密码错误'}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/author/avatar', methods=['POST'])
@admin_required
def upload_author_avatar():
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

    data = file.read()
    if not data:
        return jsonify({"error": "空文件"}), 400
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")

    admin_id = postgres_store.get_admin_user_id()
    if not admin_id:
        return jsonify({'error': '未找到管理员账号'}), 500
    try:
        postgres_store.update_author_avatar(admin_id, file_bytes=data, mime_type=mime)
    except Exception:
        return jsonify({'error': '头像更新失败'}), 500
    return jsonify(postgres_store.get_author_json())


@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/blog')
def blog():
    return send_from_directory(FRONTEND_DIR, 'blog.html')


@app.route('/about')
def about():
    return send_from_directory(FRONTEND_DIR, 'about.html')


@app.route('/detail/<article_id>')
def detail(article_id):
    return send_from_directory(FRONTEND_DIR, 'detail.html')


@app.route('/index.html')
def index_html():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/blog.html')
def blog_html():
    return send_from_directory(FRONTEND_DIR, 'blog.html')


@app.route('/about.html')
def about_html():
    return send_from_directory(FRONTEND_DIR, 'about.html')


@app.route('/detail.html')
def detail_html():
    return send_from_directory(FRONTEND_DIR, 'detail.html')


@app.route('/editor')
def editor():
    return send_from_directory(FRONTEND_DIR, 'editor.html')


@app.route('/editor.html')
def editor_html():
    return send_from_directory(FRONTEND_DIR, 'editor.html')


@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(ASSETS_DIR, filename)


if __name__ == '__main__':
    print("启动个人博客后端服务（PostgreSQL）...")
    print("访问地址: http://localhost:5000")

    port = int(_get_env('PORT', '5000'))
    app.run(debug=True, host='0.0.0.0', port=port)
