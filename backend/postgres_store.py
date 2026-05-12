# -*- coding: utf-8 -*-
"""PostgreSQL 博客存储。"""

from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import date, datetime, time
from typing import Any

import html as html_mod
import markdown as mdlib
from werkzeug.security import check_password_hash, generate_password_hash

from neon_db import get_neon_database

GUEST_COMMENT_EMAIL = "comments-guest@system.blog"
_guest_comment_user_id: uuid.UUID | None = None
_bootstrapped = False


def close_pool():
    get_neon_database().close()


def _render_article_to_html(fmt: str, body: str) -> str:
    if fmt == "txt":
        return "<pre>" + html_mod.escape(body or "") + "</pre>"
    return mdlib.markdown(
        body or "",
        extensions=["fenced_code", "tables", "toc"],
        output_format="html5",
    )


def _compute_summary(body: str, fmt: str, limit: int = 120) -> str:
    if not body:
        return ""
    if fmt == "txt":
        s = body.strip().replace("\r\n", "\n").replace("\n", " ")
        return (s[:limit] + "...") if len(s) > limit else s
    s = re.sub(r"```[\s\S]*?```", "", body)
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", s)
    s = re.sub(r"\[[^\]]*\]\([^)]+\)", "", s)
    s = re.sub(r"[#>*_`]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:limit] + "...") if len(s) > limit else s


def _safe_text(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _slug_base(title: str) -> str:
    t = _safe_text(title) or "post"
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", t, flags=re.UNICODE).strip("-").lower()
    return (s[:80] if s else "post")


def _ensure_visitor_reaction_and_comment_guest(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS article_visitor_reaction (
            article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
            visitor_key VARCHAR(64) NOT NULL,
            kind reaction_kind NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (article_id, visitor_key)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_article
        ON article_visitor_reaction(article_id)
        """
    )
    cur.execute("ALTER TABLE comment ADD COLUMN IF NOT EXISTS guest_name VARCHAR(100)")


def _ensure_schema(cur) -> None:
    cur.execute("ALTER TABLE article ADD COLUMN IF NOT EXISTS summary TEXT")
    cur.execute(
        "ALTER TABLE article ADD COLUMN IF NOT EXISTS style VARCHAR(64) NOT NULL DEFAULT 'default'"
    )
    cur.execute(
        "ALTER TABLE article ADD COLUMN IF NOT EXISTS content_format VARCHAR(8) NOT NULL DEFAULT 'md'"
    )
    cur.execute("ALTER TABLE article DROP CONSTRAINT IF EXISTS article_content_format_check")
    cur.execute(
        """ALTER TABLE article ADD CONSTRAINT article_content_format_check
           CHECK (content_format IN ('md', 'txt'))"""
    )
    cur.execute("ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS article_id UUID")
    cur.execute(
        """
        DO $bd$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'media_asset_article_id_fkey'
            ) THEN
                ALTER TABLE media_asset
                    ADD CONSTRAINT media_asset_article_id_fkey
                    FOREIGN KEY (article_id) REFERENCES article(id) ON DELETE CASCADE;
            END IF;
        END $bd$;
        """
    )
    cur.execute(
        "ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128) DEFAULT 'application/octet-stream'"
    )
    cur.execute(
        "UPDATE media_asset SET mime_type = 'application/octet-stream' WHERE mime_type IS NULL"
    )
    cur.execute("ALTER TABLE media_asset ALTER COLUMN mime_type SET NOT NULL")
    cur.execute("ALTER TABLE media_asset ADD COLUMN IF NOT EXISTS content BYTEA")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_asset_article_id ON media_asset(article_id) WHERE article_id IS NOT NULL"
    )
    _ensure_visitor_reaction_and_comment_guest(cur)


def bootstrap_if_needed(admin_email: str, admin_password_plain: str) -> None:
    global _bootstrapped
    if _bootstrapped:
        return
    from psycopg import errors as pg_errors

    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (928374651,))
                _ensure_schema(cur)
                cur.execute("SELECT 1 FROM category WHERE slug = %s LIMIT 1", ("uncategorized",))
                if not cur.fetchone():
                    cid = uuid.uuid4()
                    cur.execute(
                        """INSERT INTO category (id, name, slug)
                           VALUES (%s, %s, %s)""",
                        (cid, "未分类", "uncategorized"),
                    )
                cur.execute("""SELECT COUNT(*) FROM "user" WHERE role = 'admin'""")
                admin_count = cur.fetchone()[0]
                if admin_count == 0:
                    em = _safe_text(admin_email).lower()
                    pw = admin_password_plain or ""
                    if not em or not pw:
                        raise RuntimeError(
                            "数据库中尚无 role=admin 的用户。请任选其一后重启：\n"
                            "（1）设置环境变量 BLOG_ADMIN_EMAIL 与 BLOG_ADMIN_PASSWORD，用于自动创建首个管理员（密码仅写入 password_hash）；\n"
                            "（2）自行在 PostgreSQL 的 \"user\" 表插入 role=admin 的账号并设置 password_hash，之后用该邮箱与密码登录（无需再设上述环境变量）。"
                        )
                    uid = uuid.uuid4()
                    ph = generate_password_hash(pw)
                    try:
                        cur.execute(
                            """INSERT INTO "user" (id, email, password_hash, role)
                               VALUES (%s, %s, %s, 'admin')""",
                            (uid, em, ph),
                        )
                        cur.execute(
                            "INSERT INTO user_profile (user_id, nickname, signature) VALUES (%s, %s, %s)",
                            (uid, em.split("@")[0], ""),
                        )
                    except pg_errors.UniqueViolation:
                        pass
                _ensure_guest_comment_user(cur)
    _bootstrapped = True


def _ensure_guest_comment_user(cur) -> uuid.UUID:
    global _guest_comment_user_id
    cur.execute("""SELECT id FROM "user" WHERE lower(email) = lower(%s) LIMIT 1""", (GUEST_COMMENT_EMAIL,))
    row = cur.fetchone()
    if row:
        _guest_comment_user_id = row[0]
        return row[0]
    gid = uuid.uuid5(uuid.NAMESPACE_URL, "blog:guest-comment-user")
    ph = generate_password_hash(secrets.token_urlsafe(16))
    cur.execute(
        """INSERT INTO "user" (id, email, password_hash, role)
           VALUES (%s, %s, %s, 'member')""",
        (gid, GUEST_COMMENT_EMAIL, ph),
    )
    cur.execute(
        "INSERT INTO user_profile (user_id, nickname, signature) VALUES (%s, %s, %s)",
        (gid, "访客", ""),
    )
    _guest_comment_user_id = gid
    return gid


def get_admin_user_id() -> uuid.UUID | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM "user" WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1"""
            )
            row = cur.fetchone()
            return row[0] if row else None


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.email, u.password_hash, u.role::text,
                          COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1))
                   FROM "user" u
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   WHERE lower(u.email) = lower(%s)""",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                return None
            uid, em, ph, role, nickname = row
            if not check_password_hash(ph, password):
                return None
            if role != "admin":
                return None
            return {
                "id": str(uid),
                "email": em,
                "role": role,
                "nickname": nickname or "",
            }


def try_login(email: str, password: str) -> dict[str, Any] | None:
    return authenticate_user(email, password)


def verify_admin_user_id(user_id: uuid.UUID) -> bool:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM "user" WHERE id = %s AND role = 'admin' LIMIT 1""",
                (user_id,),
            )
            return cur.fetchone() is not None


def get_author_json() -> dict[str, Any]:
    aid = get_admin_user_id()
    if not aid:
        return {
            "name": "",
            "bio": "",
            "avatar": "",
            "skills": [],
            "social": {},
        }
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.email, up.nickname, up.signature, ma.public_url AS avatar_url
                   FROM "user" u
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   LEFT JOIN media_asset ma ON ma.id = up.avatar_media_id
                   WHERE u.id = %s""",
                (aid,),
            )
            row = cur.fetchone()
            email, nickname, signature, avatar_url = row
            name = _safe_text(nickname) or _safe_text(email).split("@")[0]
            bio = _safe_text(signature)
            avatar = _safe_text(avatar_url)

            cur.execute(
                """SELECT name FROM profile_skill WHERE profile_id = %s
                   ORDER BY sort_order ASC, name ASC""",
                (aid,),
            )
            skills = [r[0] for r in cur.fetchall()]

            cur.execute(
                """SELECT channel::text, label, value FROM profile_contact
                   WHERE profile_id = %s ORDER BY sort_order ASC, channel ASC""",
                (aid,),
            )
            social: dict[str, str] = {}
            for ch, label, val in cur.fetchall():
                lb = (label or "").strip().lower()
                if ch == "other" and lb == "qqqr":
                    social["qqQr"] = val
                elif ch in ("gitee", "email", "qq", "wechat"):
                    social[ch] = val

            return {"name": name, "bio": bio, "avatar": avatar, "skills": skills, "social": social}


def update_author_avatar(admin_id: uuid.UUID, *, file_bytes: bytes, mime_type: str) -> dict[str, Any]:
    mid = uuid.uuid4()
    public_url = f"/api/media/{mid}"
    storage_key = f"inline:{mid}"
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO media_asset (id, storage_key, public_url, kind, article_id, mime_type, content)
                       VALUES (%s, %s, %s, 'avatar', NULL, %s, %s)""",
                    (mid, storage_key, public_url, mime_type, file_bytes),
                )
                cur.execute(
                    """UPDATE user_profile SET avatar_media_id = %s WHERE user_id = %s""",
                    (mid, admin_id),
                )
    return get_author_json()


def insert_article_image_blob(
    file_bytes: bytes, mime_type: str, article_id: uuid.UUID | None
) -> uuid.UUID:
    mid = uuid.uuid4()
    public_url = f"/api/media/{mid}"
    storage_key = f"inline:{mid}"
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO media_asset (id, storage_key, public_url, kind, article_id, mime_type, content)
                   VALUES (%s, %s, %s, 'article_image', %s, %s, %s)""",
                (mid, storage_key, public_url, article_id, mime_type, file_bytes),
            )
    return mid


def insert_article_image_external(
    public_url: str, storage_key: str, article_id: uuid.UUID | None
) -> uuid.UUID:
    mid = uuid.uuid4()
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO media_asset (id, storage_key, public_url, kind, article_id, mime_type, content)
                   VALUES (%s, %s, %s, 'article_image', %s, 'text/plain', NULL)""",
                (mid, storage_key, public_url, article_id),
            )
    return mid


def get_media_payload(media_id: uuid.UUID) -> dict[str, Any] | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content, mime_type, public_url FROM media_asset WHERE id = %s",
                (media_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            blob, mime, url = row
            if blob is not None and isinstance(blob, memoryview):
                blob = blob.tobytes()
            return {
                "content": blob,
                "mime_type": (mime or "application/octet-stream").strip(),
                "public_url": url or "",
            }


def article_exists(article_id: uuid.UUID) -> bool:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM article WHERE id = %s LIMIT 1", (article_id,))
            return cur.fetchone() is not None


def _default_category_id(cur) -> uuid.UUID:
    cur.execute("SELECT id FROM category WHERE slug = %s LIMIT 1", ("uncategorized",))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("missing default category")
    return row[0]


def _pick_unique_slug(cur, base: str) -> str:
    b = _slug_base(base)
    for _ in range(20):
        cand = f"{b}-{uuid.uuid4().hex[:8]}"
        cur.execute("SELECT 1 FROM article WHERE slug = %s LIMIT 1", (cand,))
        if not cur.fetchone():
            return cand
    return f"{b}-{uuid.uuid4().hex}"


def _row_to_list_item(row) -> dict[str, Any]:
    (
        aid,
        title,
        summary,
        published_at,
        fmt,
        style,
        author_display,
        body,
        cat_id,
        cat_name,
        cat_slug,
        tags_j,
        cc,
        lk,
        dk,
    ) = row
    d = published_at.date().isoformat() if published_at else ""
    fmt = fmt or "md"
    summ = _safe_text(summary) if summary else _compute_summary(body or "", fmt)
    tags: list[dict[str, Any]] = []
    if tags_j is not None:
        if isinstance(tags_j, list):
            tags = [{"id": str(t["id"]), "name": t["name"], "slug": t["slug"]} for t in tags_j]
        elif isinstance(tags_j, str):
            import json

            try:
                arr = json.loads(tags_j)
                if isinstance(arr, list):
                    tags = [{"id": str(t["id"]), "name": t["name"], "slug": t["slug"]} for t in arr]
            except Exception:
                pass
    cat = None
    if cat_id:
        cat = {"id": str(cat_id), "name": cat_name or "", "slug": cat_slug or ""}
    return {
        "id": str(aid),
        "title": title or "",
        "date": d,
        "author": author_display or "",
        "summary": summ,
        "style": style or "default",
        "format": fmt or "md",
        "category": cat,
        "tags": tags,
        "comment_count": int(cc or 0),
        "likes": int(lk or 0),
        "dislikes": int(dk or 0),
    }


def _list_sql_frag(include_drafts: bool) -> str:
    draft_clause = "" if include_drafts else "WHERE a.status = 'published'"
    return f"""
        SELECT a.id, a.title, a.summary, a.published_at, a.content_format, a.style,
               COALESCE(up.nickname, u.email) AS author_display, a.body,
               c.id, c.name, c.slug,
               COALESCE(
                   (SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'slug', t.slug) ORDER BY t.name)
                    FROM article_tag at JOIN tag t ON t.id = at.tag_id WHERE at.article_id = a.id),
                   '[]'::json
               ) AS tags,
               (SELECT COUNT(*)::int FROM comment cm WHERE cm.article_id = a.id),
               (SELECT COUNT(*)::int FROM article_reaction ar WHERE ar.article_id = a.id AND ar.kind = 'like')
             + (SELECT COALESCE(COUNT(*)::int, 0) FROM article_visitor_reaction vr WHERE vr.article_id = a.id AND vr.kind = 'like'),
               (SELECT COUNT(*)::int FROM article_reaction ar WHERE ar.article_id = a.id AND ar.kind = 'dislike')
             + (SELECT COALESCE(COUNT(*)::int, 0) FROM article_visitor_reaction vr WHERE vr.article_id = a.id AND vr.kind = 'dislike')
        FROM article a
        JOIN "user" u ON u.id = a.author_id
        LEFT JOIN user_profile up ON up.user_id = u.id
        JOIN category c ON c.id = a.category_id
        {draft_clause}
        ORDER BY a.published_at DESC NULLS LAST, a.title ASC
    """


def list_articles(include_drafts: bool) -> list[dict[str, Any]]:
    sql = _list_sql_frag(include_drafts)
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [_row_to_list_item(r) for r in cur.fetchall()]


def get_article_dict(
    aid: uuid.UUID,
    allow_draft: bool,
    viewer_user_id: uuid.UUID | None = None,
    visitor_key: str | None = None,
) -> dict[str, Any] | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.title, a.summary, a.body, a.content_format, a.style,
                          a.published_at, a.status::text, a.slug,
                          COALESCE(up.nickname, u.email) AS author_display,
                          c.id, c.name, c.slug
                   FROM article a
                   JOIN "user" u ON u.id = a.author_id
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   JOIN category c ON c.id = a.category_id
                   WHERE a.id = %s""",
                (aid,),
            )
            row = cur.fetchone()
            if not row:
                return None
            (
                rid,
                title,
                summary,
                body,
                fmt,
                style,
                published_at,
                status,
                _slug,
                author_display,
                cat_id,
                cat_name,
                cat_slug,
            ) = row
            if status == "draft" and not allow_draft:
                return None
            fmt = fmt or "md"
            summ = _safe_text(summary) if summary else _compute_summary(body or "", fmt)
            d = published_at.date().isoformat() if published_at else ""

            cur.execute(
                """SELECT COALESCE(
                       (SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'slug', t.slug) ORDER BY t.name)
                        FROM article_tag at JOIN tag t ON t.id = at.tag_id WHERE at.article_id = %s),
                       '[]'::json)""",
                (aid,),
            )
            tags_j = cur.fetchone()[0]
            tags: list[dict[str, Any]] = []
            if isinstance(tags_j, list):
                tags = [{"id": str(t["id"]), "name": t["name"], "slug": t["slug"]} for t in tags_j]

            cur.execute(
                """SELECT (SELECT COUNT(*)::int FROM comment cm WHERE cm.article_id = %s),
                          (SELECT COUNT(*)::int FROM article_reaction ar WHERE ar.article_id = %s AND ar.kind = 'like')
                        + (SELECT COALESCE(COUNT(*)::int, 0) FROM article_visitor_reaction vr WHERE vr.article_id = %s AND vr.kind = 'like'),
                          (SELECT COUNT(*)::int FROM article_reaction ar WHERE ar.article_id = %s AND ar.kind = 'dislike')
                        + (SELECT COALESCE(COUNT(*)::int, 0) FROM article_visitor_reaction vr WHERE vr.article_id = %s AND vr.kind = 'dislike')
                """,
                (aid, aid, aid, aid, aid),
            )
            cc, lk, dk = cur.fetchone()

            cur.execute(
                """SELECT c.id, c.body, c.created_at, c.guest_name, u.id AS uid, u.email,
                          COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1)) AS nick
                   FROM comment c
                   JOIN "user" u ON u.id = c.user_id
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   WHERE c.article_id = %s
                   ORDER BY c.created_at ASC""",
                (aid,),
            )
            comments_out: list[dict[str, Any]] = []
            for r in cur.fetchall():
                cid, cbody, cat, gn, uid, uemail, nick = r
                if (uemail or "").lower() == GUEST_COMMENT_EMAIL.lower():
                    label = _safe_text(gn) or "访客"
                else:
                    label = _safe_text(nick) or (uemail or "").split("@")[0]
                comments_out.append(
                    {
                        "id": str(cid),
                        "body": cbody or "",
                        "created_at": cat.isoformat() if cat else "",
                        "author": label,
                    }
                )

            mine = None
            if viewer_user_id:
                cur.execute(
                    "SELECT kind::text FROM article_reaction WHERE article_id = %s AND user_id = %s",
                    (aid, viewer_user_id),
                )
                r2 = cur.fetchone()
                if r2:
                    mine = r2[0]
            elif visitor_key:
                cur.execute(
                    "SELECT kind::text FROM article_visitor_reaction WHERE article_id = %s AND visitor_key = %s",
                    (aid, visitor_key),
                )
                r2 = cur.fetchone()
                if r2:
                    mine = r2[0]

            return {
                "id": str(rid),
                "title": title or "",
                "date": d,
                "author": author_display or "",
                "summary": summ,
                "style": style or "default",
                "format": fmt,
                "raw": body or "",
                "content": _render_article_to_html(fmt, body or ""),
                "status": status,
                "category": {"id": str(cat_id), "name": cat_name or "", "slug": cat_slug or ""},
                "tags": tags,
                "comment_count": int(cc or 0),
                "likes": int(lk or 0),
                "dislikes": int(dk or 0),
                "comments": comments_out,
                "my_reaction": mine,
            }


def _parse_tag_id_list(payload: dict[str, Any]) -> list[uuid.UUID]:
    raw = payload.get("tag_ids") or payload.get("tags")
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.split(",") if s.strip()]
    out: list[uuid.UUID] = []
    for x in raw:
        try:
            out.append(uuid.UUID(str(x)))
        except Exception:
            continue
    return out


def _replace_article_tags(cur, article_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> None:
    cur.execute("DELETE FROM article_tag WHERE article_id = %s", (article_id,))
    for tid in tag_ids:
        cur.execute("SELECT 1 FROM tag WHERE id = %s", (tid,))
        if cur.fetchone():
            cur.execute(
                "INSERT INTO article_tag (article_id, tag_id) VALUES (%s, %s) ON CONFLICT (article_id, tag_id) DO NOTHING",
                (article_id, tid),
            )


def _resolve_category_id(cur, payload: dict[str, Any]) -> uuid.UUID:
    raw = payload.get("category_id")
    if raw:
        try:
            cid = uuid.UUID(str(raw))
        except Exception:
            cid = None
        if cid:
            cur.execute("SELECT 1 FROM category WHERE id = %s", (cid,))
            if cur.fetchone():
                return cid
    return _default_category_id(cur)


def get_guest_comment_user_id() -> uuid.UUID:
    global _guest_comment_user_id
    if _guest_comment_user_id is not None:
        return _guest_comment_user_id
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            return _ensure_guest_comment_user(cur)


def create_article(payload: dict[str, Any], author_id: uuid.UUID) -> dict[str, Any]:
    title = _safe_text(payload.get("title")) or "未命名文章"
    body = payload.get("content") or ""
    fmt = _safe_text(payload.get("format")) or "md"
    if fmt not in ("md", "txt"):
        fmt = "md"
    style = _safe_text(payload.get("style")) or "default"
    date_s = _safe_text(payload.get("date")) or date.today().isoformat()
    summary = _safe_text(payload.get("summary"))
    if not summary:
        summary = _compute_summary(body, fmt)

    try:
        y, m, d = [int(x) for x in date_s.split("-")[:3]]
        pub_dt = datetime(y, m, d, 12, 0, 0)
    except Exception:
        pub_dt = datetime.combine(date.today(), time(12, 0, 0))

    tag_ids = _parse_tag_id_list(payload)
    aid = uuid.uuid4()
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cat = _resolve_category_id(cur, payload)
                slug = _pick_unique_slug(cur, title)
                cur.execute(
                    """INSERT INTO article (
                           id, title, body, summary, slug, published_at, status,
                           style, content_format, author_id, category_id
                       ) VALUES (
                           %s, %s, %s, %s, %s, %s, 'published', %s, %s, %s, %s
                       )""",
                    (aid, title, body, summary, slug, pub_dt, style, fmt, author_id, cat),
                )
                _replace_article_tags(cur, aid, tag_ids)

    out = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_key=None)
    assert out
    return out


def update_article(aid: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_key=None)
    if not existing:
        return None

    title = _safe_text(payload.get("title")) or existing["title"]
    body = payload.get("content")
    if body is None:
        body = existing["raw"]
    fmt = _safe_text(payload.get("format")) or existing.get("format", "md")
    if fmt not in ("md", "txt"):
        fmt = existing.get("format", "md")
    style = _safe_text(payload.get("style")) or existing.get("style", "default")
    date_s = _safe_text(payload.get("date")) or existing.get("date") or date.today().isoformat()
    summary = _safe_text(payload.get("summary"))
    if not summary:
        summary = _compute_summary(body, fmt)

    try:
        y, m, d = [int(x) for x in date_s.split("-")[:3]]
        pub_dt = datetime(y, m, d, 12, 0, 0)
    except Exception:
        pub_dt = datetime.combine(date.today(), time(12, 0, 0))

    tag_ids = _parse_tag_id_list(payload) if ("tag_ids" in payload or "tags" in payload) else None

    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cat_sql = ""
                params: list[Any] = [title, body, summary, pub_dt, style, fmt]
                if "category_id" in payload:
                    cat = _resolve_category_id(cur, payload)
                    cat_sql = ", category_id = %s"
                    params.append(cat)
                params.append(aid)
                cur.execute(
                    f"""UPDATE article SET title=%s, body=%s, summary=%s,
                           published_at=%s, style=%s, content_format=%s, status='published'
                           {cat_sql}
                       WHERE id=%s""",
                    tuple(params),
                )
                if cur.rowcount == 0:
                    return None
                if tag_ids is not None:
                    _replace_article_tags(cur, aid, tag_ids)

    out = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_key=None)
    assert out
    return out


def delete_article(aid: uuid.UUID) -> bool:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM article WHERE id = %s", (aid,))
            return cur.rowcount > 0


def user_email_by_id(uid: uuid.UUID) -> str | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM \"user\" WHERE id = %s", (uid,))
            row = cur.fetchone()
            return row[0] if row else None


def user_nickname_by_id(uid: uuid.UUID) -> str:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1))
                   FROM "user" u
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   WHERE u.id = %s""",
                (uid,),
            )
            row = cur.fetchone()
            return (row[0] or "") if row else ""


def list_categories() -> list[dict[str, Any]]:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, slug FROM category ORDER BY name ASC")
            return [{"id": str(r[0]), "name": r[1], "slug": r[2]} for r in cur.fetchall()]


def list_tags() -> list[dict[str, Any]]:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, slug FROM tag ORDER BY name ASC")
            return [{"id": str(r[0]), "name": r[1], "slug": r[2]} for r in cur.fetchall()]


def add_article_comment(
    article_id: uuid.UUID,
    body: str,
    *,
    user_id: uuid.UUID | None,
    guest_name: str | None,
) -> dict[str, Any]:
    text = _safe_text(body)
    if not text:
        raise ValueError("empty body")
    gid = get_guest_comment_user_id()
    cid = uuid.uuid4()
    gn = _safe_text(guest_name)[:80] if guest_name else None
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM article WHERE id = %s", (article_id,))
                if not cur.fetchone():
                    raise LookupError("article not found")
                if user_id and user_id != gid:
                    cur.execute(
                        """INSERT INTO comment (id, body, article_id, user_id, guest_name)
                           VALUES (%s, %s, %s, %s, NULL)""",
                        (cid, text, article_id, user_id),
                    )
                else:
                    if not gn:
                        raise ValueError("guest_name required")
                    cur.execute(
                        """INSERT INTO comment (id, body, article_id, user_id, guest_name)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (cid, text, article_id, gid, gn),
                    )
    return {"id": str(cid), "ok": True}


def set_article_reaction(
    article_id: uuid.UUID,
    kind: str,
    *,
    user_id: uuid.UUID | None,
    visitor_key: str | None,
) -> dict[str, Any]:
    if kind not in ("like", "dislike", "none"):
        raise ValueError("bad kind")
    vk = _safe_text(visitor_key)[:80] if visitor_key else None
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM article WHERE id = %s", (article_id,))
                if not cur.fetchone():
                    raise LookupError("article not found")
                if user_id:
                    if kind == "none":
                        cur.execute(
                            "DELETE FROM article_reaction WHERE article_id = %s AND user_id = %s",
                            (article_id, user_id),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO article_reaction (user_id, article_id, kind, updated_at)
                               VALUES (%s, %s, %s, NOW())
                               ON CONFLICT (user_id, article_id) DO UPDATE SET kind = EXCLUDED.kind, updated_at = NOW()""",
                            (user_id, article_id, kind),
                        )
                else:
                    if not vk:
                        raise ValueError("visitor_key required")
                    if kind == "none":
                        cur.execute(
                            "DELETE FROM article_visitor_reaction WHERE article_id = %s AND visitor_key = %s",
                            (article_id, vk),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO article_visitor_reaction (article_id, visitor_key, kind, updated_at)
                               VALUES (%s, %s, %s, NOW())
                               ON CONFLICT (article_id, visitor_key) DO UPDATE SET kind = EXCLUDED.kind, updated_at = NOW()""",
                            (article_id, vk, kind),
                        )
    row = get_article_dict(article_id, True, user_id, vk if not user_id else None)
    return {
        "ok": True,
        "likes": row["likes"] if row else 0,
        "dislikes": row["dislikes"] if row else 0,
        "my_reaction": row.get("my_reaction") if row else None,
    }
