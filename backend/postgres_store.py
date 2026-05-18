# -*- coding: utf-8 -*-
"""PostgreSQL 博客存储。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from datetime import date, datetime, time
from typing import Any

import html as html_mod
import markdown as mdlib
from werkzeug.security import check_password_hash, generate_password_hash

from neon_db import get_neon_database

import llm_client
import rag_index

GUEST_COMMENT_EMAIL = "comments-guest@system.blog"
_guest_comment_user_id: uuid.UUID | None = None
_bootstrapped = False

_related_refresh_lock = threading.Lock()
_related_refresh_inflight: set[str] = set()


def _schedule_rag_reindex() -> None:
    def _run() -> None:
        try:
            rag_index.rebuild_index()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="rag-reindex").start()


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


def _table_has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s AND column_name = %s LIMIT 1""",
        (table, column),
    )
    return cur.fetchone() is not None


def _ensure_visitor_platform(cur) -> None:
    """游客表 visitor + 评论/访客赞踩外键；将旧 article_visitor_reaction(visitor_key) 迁到 visitor_id。"""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visitor (
            id UUID PRIMARY KEY,
            nickname VARCHAR(100),
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute("ALTER TABLE comment ADD COLUMN IF NOT EXISTS guest_name VARCHAR(100)")
    cur.execute("ALTER TABLE comment ADD COLUMN IF NOT EXISTS visitor_id UUID")
    cur.execute(
        """
        DO $bd$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'comment_visitor_id_fkey') THEN
                ALTER TABLE comment
                    ADD CONSTRAINT comment_visitor_id_fkey
                    FOREIGN KEY (visitor_id) REFERENCES visitor(id) ON DELETE SET NULL;
            END IF;
        END $bd$;
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comment_visitor_id ON comment(visitor_id)")

    cur.execute(
        """SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'article_visitor_reaction'
        )"""
    )
    avr_exists = cur.fetchone()[0]

    if not avr_exists:
        cur.execute(
            """
            CREATE TABLE article_visitor_reaction (
                article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
                visitor_id UUID NOT NULL REFERENCES visitor(id) ON DELETE CASCADE,
                kind reaction_kind NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (article_id, visitor_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_article ON article_visitor_reaction(article_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_visitor ON article_visitor_reaction(visitor_id)"
        )
        return

    has_vkey = _table_has_column(cur, "article_visitor_reaction", "visitor_key")
    has_vid = _table_has_column(cur, "article_visitor_reaction", "visitor_id")

    if has_vkey and not has_vid:
        cur.execute("SELECT DISTINCT visitor_key FROM article_visitor_reaction WHERE visitor_key IS NOT NULL")
        vkeys = [r[0] for r in cur.fetchall()]
        ns = uuid.NAMESPACE_URL
        for vk in vkeys:
            vk_s = _safe_text(vk)
            if not vk_s:
                continue
            vid = uuid.uuid5(ns, "blog:vkey:" + vk_s)
            cur.execute("SELECT 1 FROM visitor WHERE id = %s", (vid,))
            if not cur.fetchone():
                cur.execute(
                    "SELECT MIN(updated_at) FROM article_visitor_reaction WHERE visitor_key = %s",
                    (vk_s,),
                )
                rmin = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO visitor (id, nickname, first_seen_at) VALUES (%s, NULL, COALESCE(%s, NOW()))",
                    (vid, rmin),
                )
        cur.execute("ALTER TABLE article_visitor_reaction ADD COLUMN visitor_id UUID")
        for vk in vkeys:
            vk_s = _safe_text(vk)
            if not vk_s:
                continue
            vid = uuid.uuid5(ns, "blog:vkey:" + vk_s)
            cur.execute(
                "UPDATE article_visitor_reaction SET visitor_id = %s WHERE visitor_key = %s",
                (vid, vk_s),
            )
        cur.execute("DELETE FROM article_visitor_reaction WHERE visitor_id IS NULL")
        cur.execute("ALTER TABLE article_visitor_reaction DROP CONSTRAINT IF EXISTS article_visitor_reaction_pkey")
        cur.execute("ALTER TABLE article_visitor_reaction DROP COLUMN visitor_key")
        cur.execute("ALTER TABLE article_visitor_reaction ALTER COLUMN visitor_id SET NOT NULL")
        cur.execute(
            "ALTER TABLE article_visitor_reaction ADD CONSTRAINT article_visitor_reaction_pkey PRIMARY KEY (article_id, visitor_id)"
        )
        cur.execute(
            """
            DO $bd$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'article_visitor_reaction_visitor_id_fkey'
                ) THEN
                    ALTER TABLE article_visitor_reaction
                        ADD CONSTRAINT article_visitor_reaction_visitor_id_fkey
                        FOREIGN KEY (visitor_id) REFERENCES visitor(id) ON DELETE CASCADE;
                END IF;
            END $bd$;
            """
        )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_article ON article_visitor_reaction(article_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_visitor_reaction_visitor ON article_visitor_reaction(visitor_id)"
    )


def _ensure_schema(cur) -> None:
    # 若曾把枚举改成 profile_image，启动时改回 avatar（与 create_postgresql.sql 一致）
    cur.execute(
        """
        DO $mk$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public' AND t.typname = 'media_kind' AND e.enumlabel = 'profile_image'
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public' AND t.typname = 'media_kind' AND e.enumlabel = 'avatar'
            ) THEN
                ALTER TYPE media_kind RENAME VALUE 'profile_image' TO 'avatar';
            END IF;
        END $mk$;
        """
    )
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
    _ensure_visitor_platform(cur)
    _ensure_highlight_tables(cur)
    _ensure_related_cache_table(cur)


def _ensure_related_cache_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS article_related_cache (
            article_id UUID PRIMARY KEY REFERENCES article(id) ON DELETE CASCADE,
            recommendations JSONB NOT NULL,
            source_fingerprint VARCHAR(64) NOT NULL,
            catalog_fingerprint VARCHAR(64) NOT NULL,
            match_source VARCHAR(16) NOT NULL DEFAULT 'llm',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_highlight_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS article_highlight (
            id UUID PRIMARY KEY,
            article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
            visitor_id UUID REFERENCES visitor(id) ON DELETE SET NULL,
            user_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            exact_text TEXT NOT NULL,
            prefix_text TEXT NOT NULL DEFAULT '',
            suffix_text TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS highlight_comment (
            id UUID PRIMARY KEY,
            highlight_id UUID NOT NULL REFERENCES article_highlight(id) ON DELETE CASCADE,
            parent_id UUID REFERENCES highlight_comment(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            guest_name VARCHAR(100),
            visitor_id UUID REFERENCES visitor(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_highlight_article ON article_highlight(article_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_highlight_comment_highlight ON highlight_comment(highlight_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_highlight_comment_parent ON highlight_comment(parent_id)"
    )


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


def ensure_session_visitor_id(session_obj: Any) -> uuid.UUID:
    """为未登录访客在 DB 中创建一行 visitor，并把 UUID 写入 Flask session['visitor_id']。"""
    raw = session_obj.get("visitor_id")
    if raw:
        return uuid.UUID(str(raw))
    vid = uuid.uuid4()
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visitor (id, nickname, first_seen_at) VALUES (%s, NULL, NOW())",
                    (vid,),
                )
    session_obj["visitor_id"] = str(vid)
    return vid


def get_visitor_public(visitor_id: uuid.UUID | None) -> dict[str, Any] | None:
    if not visitor_id:
        return None
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nickname, first_seen_at FROM visitor WHERE id = %s",
                (visitor_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            vid, nick, fs = row
            return {
                "id": str(vid),
                "nickname": (nick or "").strip(),
                "first_seen_at": fs.isoformat() if fs else "",
            }


def update_visitor_nickname(visitor_id: uuid.UUID, nickname: str) -> None:
    nn = _safe_text(nickname)[:80]
    if not nn:
        raise ValueError("请填写昵称")
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("UPDATE visitor SET nickname = %s WHERE id = %s", (nn, visitor_id))
                if cur.rowcount == 0:
                    raise ValueError("visitor not found")


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
                if ch in ("gitee", "email", "qq", "wechat"):
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


def _tokenize_for_similarity(text: str) -> set[str]:
    """中英文混合文本的简单分词（字符 + 双字），用于相似度计算。"""
    raw = _safe_text(text).lower()
    if not raw:
        return set()
    tokens: set[str] = set(re.findall(r"[a-z0-9]{2,}", raw))
    cjk = re.findall(r"[\u4e00-\u9fff]", raw)
    tokens.update(cjk)
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return tokens


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _related_match_reason(
    *,
    shared_tag_names: list[str],
    same_category: bool,
    category_name: str,
    text_score: float,
) -> str:
    if shared_tag_names:
        shown = "、".join(shared_tag_names[:3])
        if len(shared_tag_names) > 3:
            shown += f" 等{len(shared_tag_names)}个"
        return f"共同标签：{shown}"
    if same_category and category_name:
        return f"同分类：{category_name}"
    if text_score >= 0.12:
        return "主题内容相近"
    return "推荐阅读"


def _rule_score_candidates(
    *,
    src_cat_id,
    src_title: str,
    src_summ: str,
    src_tokens: set[str],
    src_tag_ids: set,
    src_tag_names: dict,
    src_pub,
    rows: list,
) -> list[tuple[float, float, dict[str, Any]]]:
    scored: list[tuple[float, float, dict[str, Any]]] = []
    for row in rows:
        aid, title, summary, body, fmt, published_at, cat_id, cat_name, cat_slug, tags_j = row
        fmt = fmt or "md"
        summ = _safe_text(summary) if summary else _compute_summary(body or "", fmt)
        tags: list[dict[str, Any]] = []
        if isinstance(tags_j, list):
            tags = [{"id": str(t["id"]), "name": t["name"], "slug": t["slug"]} for t in tags_j]

        cand_tag_ids = {uuid.UUID(str(t["id"])) for t in tags}
        shared_ids = src_tag_ids & cand_tag_ids
        tag_score = len(shared_ids) / len(src_tag_ids | cand_tag_ids) if (src_tag_ids or cand_tag_ids) else 0.0
        cat_score = 1.0 if src_cat_id and cat_id == src_cat_id else 0.0
        text_score = _jaccard_similarity(src_tokens, _tokenize_for_similarity(f"{title} {summ}"))

        pub_ts = published_at.timestamp() if published_at else 0.0
        recency = 0.0
        if src_pub and published_at:
            days = abs((src_pub - published_at).days)
            recency = max(0.0, 1.0 - min(days, 365) / 365.0)

        score = tag_score * 45.0 + cat_score * 25.0 + text_score * 25.0 + recency * 5.0
        shared_names = [src_tag_names[tid] for tid in shared_ids if tid in src_tag_names]
        reason = _related_match_reason(
            shared_tag_names=shared_names,
            same_category=bool(cat_score),
            category_name=cat_name or "",
            text_score=text_score,
        )
        scored.append(
            (
                score,
                pub_ts,
                {
                    "id": str(aid),
                    "title": title or "",
                    "date": published_at.date().isoformat() if published_at else "",
                    "summary": summ,
                    "category": {"id": str(cat_id), "name": cat_name or "", "slug": cat_slug or ""},
                    "tags": tags,
                    "match_reason": reason,
                    "relevance": round(min(score, 99.9), 1),
                    "match_source": "rule",
                },
            )
        )
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return scored


def _related_catalog_fingerprint(cur) -> str:
    cur.execute(
        """SELECT COUNT(*)::bigint, COALESCE(MAX(published_at)::text, '')
           FROM article WHERE status = 'published'"""
    )
    n, mx = cur.fetchone()
    return hashlib.sha256(f"cat:{n}|{mx}".encode()).hexdigest()[:32]


def _related_source_fingerprint(title: str, summary: str, tag_ids: set) -> str:
    tag_part = ",".join(sorted(str(t) for t in tag_ids))
    raw = f"{title}\n{summary}\n{tag_part}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _read_related_cache(
    cur,
    article_id: uuid.UUID,
    source_fp: str,
    catalog_fp: str,
) -> list[dict[str, Any]] | None:
    cur.execute(
        """SELECT recommendations, source_fingerprint, catalog_fingerprint
           FROM article_related_cache WHERE article_id = %s""",
        (article_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    recs, sfp, cfp = row
    if sfp != source_fp or cfp != catalog_fp:
        return None
    if isinstance(recs, list):
        return recs
    if isinstance(recs, str):
        try:
            parsed = json.loads(recs)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    return None


def _write_related_cache(
    cur,
    article_id: uuid.UUID,
    articles: list[dict[str, Any]],
    source_fp: str,
    catalog_fp: str,
    match_source: str,
) -> None:
    cur.execute(
        """INSERT INTO article_related_cache (
               article_id, recommendations, source_fingerprint, catalog_fingerprint,
               match_source, updated_at
           ) VALUES (%s, %s::jsonb, %s, %s, %s, NOW())
           ON CONFLICT (article_id) DO UPDATE SET
               recommendations = EXCLUDED.recommendations,
               source_fingerprint = EXCLUDED.source_fingerprint,
               catalog_fingerprint = EXCLUDED.catalog_fingerprint,
               match_source = EXCLUDED.match_source,
               updated_at = NOW()""",
        (article_id, json.dumps(articles, ensure_ascii=False), source_fp, catalog_fp, match_source),
    )


def _load_related_scored(article_id: uuid.UUID) -> dict[str, Any] | None:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.category_id, a.title, a.summary, a.body, a.content_format,
                          a.published_at, a.status, c.name
                   FROM article a
                   LEFT JOIN category c ON c.id = a.category_id
                   WHERE a.id = %s""",
                (article_id,),
            )
            src = cur.fetchone()
            if not src:
                return None
            src_cat_id, src_title, src_summary, src_body, src_fmt, src_pub, status, src_cat_name = src
            if status != "published":
                return None
            src_fmt = src_fmt or "md"
            src_summ = _safe_text(src_summary) if src_summary else _compute_summary(src_body or "", src_fmt)
            src_tokens = _tokenize_for_similarity(f"{src_title} {src_summ}")

            cur.execute(
                "SELECT tag_id FROM article_tag WHERE article_id = %s",
                (article_id,),
            )
            src_tag_ids = {r[0] for r in cur.fetchall()}

            cur.execute(
                """SELECT t.id, t.name FROM tag t
                   JOIN article_tag at ON at.tag_id = t.id
                   WHERE at.article_id = %s""",
                (article_id,),
            )
            src_tag_names = {r[0]: r[1] for r in cur.fetchall()}

            cur.execute(
                """SELECT a.id, a.title, a.summary, a.body, a.content_format, a.published_at,
                          c.id, c.name, c.slug,
                          COALESCE(
                              (SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'slug', t.slug)
                                               ORDER BY t.name)
                               FROM article_tag at JOIN tag t ON t.id = at.tag_id
                               WHERE at.article_id = a.id),
                              '[]'::json
                          ) AS tags
                   FROM article a
                   JOIN category c ON c.id = a.category_id
                   WHERE a.status = 'published' AND a.id <> %s""",
                (article_id,),
            )
            rows = cur.fetchall()

    scored = _rule_score_candidates(
        src_cat_id=src_cat_id,
        src_title=src_title or "",
        src_summ=src_summ,
        src_tokens=src_tokens,
        src_tag_ids=src_tag_ids,
        src_tag_names=src_tag_names,
        src_pub=src_pub,
        rows=rows,
    )
    return {
        "article_id": article_id,
        "src_cat_id": src_cat_id,
        "src_title": src_title or "",
        "src_summ": src_summ,
        "src_tag_ids": src_tag_ids,
        "src_tag_names": src_tag_names,
        "src_cat_name": src_cat_name or "",
        "scored": scored,
    }


def _related_rule_list(data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    scored = data["scored"]
    rule_ranked = [item for _, _, item in scored]
    top = [item for score, _, item in scored if score > 0][:limit]
    if len(top) >= limit:
        return top
    seen = {item["id"] for item in top}
    for item in rule_ranked:
        if item["id"] in seen:
            continue
        top.append(item)
        seen.add(item["id"])
        if len(top) >= limit:
            break
    return top


def _related_llm_list(data: dict[str, Any], limit: int) -> list[dict[str, Any]] | None:
    if not llm_client.is_configured():
        return None
    scored = data["scored"]
    rule_ranked = [item for _, _, item in scored]
    prefilter = rule_ranked[:15]
    if not prefilter:
        return None
    article_id = data["article_id"]
    src_tag_ids = data["src_tag_ids"]
    src_tag_names = data["src_tag_names"]
    source_article = {
        "id": str(article_id),
        "title": data["src_title"],
        "summary": data["src_summ"],
        "category": {"name": data["src_cat_name"]},
        "tags": [{"name": src_tag_names[tid]} for tid in src_tag_ids if tid in src_tag_names],
    }
    llm_ranked = llm_client.rank_related_articles(source_article, prefilter, limit=limit)
    if not llm_ranked:
        return None
    seen = {x["id"] for x in llm_ranked}
    for item in rule_ranked:
        if len(llm_ranked) >= limit:
            break
        if item["id"] not in seen:
            llm_ranked.append(item)
            seen.add(item["id"])
    return llm_ranked[:limit]


def get_related_articles(article_id: uuid.UUID, limit: int = 6) -> list[dict[str, Any]]:
    """相关文章：规则预筛 + LLM 精排（供后台预热缓存；前台请用 get_related_articles_response）。"""
    limit = max(1, min(int(limit or 6), 12))
    data = _load_related_scored(article_id)
    if not data:
        return []
    llm_ranked = _related_llm_list(data, limit)
    if llm_ranked:
        return llm_ranked
    return _related_rule_list(data, limit)


def refresh_related_articles_cache(article_id: uuid.UUID, limit: int = 6) -> bool:
    """计算并写入相关阅读缓存（后台线程调用）。"""
    limit = max(1, min(int(limit or 6), 12))
    data = _load_related_scored(article_id)
    if not data:
        return False
    articles = _related_llm_list(data, limit)
    match_source = "llm"
    if not articles:
        articles = _related_rule_list(data, limit)
        match_source = "rule"
    source_fp = _related_source_fingerprint(
        data["src_title"], data["src_summ"], data["src_tag_ids"]
    )
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                catalog_fp = _related_catalog_fingerprint(cur)
                _write_related_cache(
                    cur, article_id, articles, source_fp, catalog_fp, match_source
                )
    return True


def schedule_related_cache_refresh(article_id: uuid.UUID) -> None:
    """在后台异步预热相关阅读 LLM 缓存。"""
    if not llm_client.is_configured():
        return
    aid = str(article_id)
    with _related_refresh_lock:
        if aid in _related_refresh_inflight:
            return
        _related_refresh_inflight.add(aid)

    def _run() -> None:
        try:
            refresh_related_articles_cache(article_id)
        finally:
            with _related_refresh_lock:
                _related_refresh_inflight.discard(aid)

    threading.Thread(target=_run, daemon=True, name=f"related-{aid[:8]}").start()


def get_related_articles_response(
    article_id: uuid.UUID,
    limit: int = 6,
) -> dict[str, Any]:
    """
    供 API 使用：优先读库内缓存；未命中则立即返回规则推荐并后台预热 LLM。
    """
    limit = max(1, min(int(limit or 6), 12))
    data = _load_related_scored(article_id)
    if not data:
        return {"articles": [], "source": "none", "pending": False}

    source_fp = _related_source_fingerprint(
        data["src_title"], data["src_summ"], data["src_tag_ids"]
    )
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            catalog_fp = _related_catalog_fingerprint(cur)
            cached = _read_related_cache(cur, article_id, source_fp, catalog_fp)

    if cached:
        return {"articles": cached[:limit], "source": "cache", "pending": False}

    rule_articles = _related_rule_list(data, limit)
    pending = False
    if llm_client.is_configured():
        schedule_related_cache_refresh(article_id)
        pending = True
    return {"articles": rule_articles, "source": "rule", "pending": pending}


def get_article_dict(
    aid: uuid.UUID,
    allow_draft: bool,
    viewer_user_id: uuid.UUID | None = None,
    visitor_id: uuid.UUID | None = None,
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
                """SELECT c.id, c.body, c.created_at, c.guest_name, c.visitor_id, u.id, u.email,
                          v.nickname AS visitor_nick,
                          COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1)) AS nick,
                          u.role::text AS user_role
                   FROM comment c
                   JOIN "user" u ON u.id = c.user_id
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   LEFT JOIN visitor v ON v.id = c.visitor_id
                   WHERE c.article_id = %s
                   ORDER BY c.created_at ASC""",
                (aid,),
            )
            comments_out: list[dict[str, Any]] = []
            for r in cur.fetchall():
                cid, cbody, cat, gn, vis_id, uid, uemail, visitor_nick, nick, user_role = r
                guest_sys = (uemail or "").lower() == GUEST_COMMENT_EMAIL.lower()
                if guest_sys:
                    label = _safe_text(visitor_nick) or _safe_text(gn) or "访客"
                    is_admin_comment = False
                else:
                    label = _safe_text(nick) or (uemail or "").split("@")[0]
                    is_admin_comment = (user_role or "").strip().lower() == "admin"
                comments_out.append(
                    {
                        "id": str(cid),
                        "body": cbody or "",
                        "created_at": cat.isoformat() if cat else "",
                        "author": label,
                        "is_admin": is_admin_comment,
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
            elif visitor_id:
                cur.execute(
                    "SELECT kind::text FROM article_visitor_reaction WHERE article_id = %s AND visitor_id = %s",
                    (aid, visitor_id),
                )
                r2 = cur.fetchone()
                if r2:
                    mine = r2[0]

            cur.execute(
                """WITH ordered AS (
                       SELECT id,
                              LAG(id) OVER (ORDER BY published_at DESC NULLS LAST, title ASC) AS prev_id,
                              LEAD(id) OVER (ORDER BY published_at DESC NULLS LAST, title ASC) AS next_id
                       FROM article WHERE status = 'published'
                   )
                   SELECT prev_id, next_id FROM ordered WHERE id = %s""",
                (aid,),
            )
            nav = cur.fetchone()
            prev_id = str(nav[0]) if nav and nav[0] else None
            next_id = str(nav[1]) if nav and nav[1] else None

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
                "prev_id": prev_id,
                "next_id": next_id,
                "highlights": list_article_highlights(aid),
            }


def _comment_author_label(
    guest_name: str | None,
    visitor_nick: str | None,
    nick: str | None,
    uemail: str | None,
    user_role: str | None,
    guest_sys: bool,
) -> tuple[str, bool]:
    if guest_sys:
        label = _safe_text(visitor_nick) or _safe_text(guest_name) or "访客"
        return label, False
    label = _safe_text(nick) or (uemail or "").split("@")[0]
    is_admin = (user_role or "").strip().lower() == "admin"
    return label, is_admin


def _fetch_highlight_comments(cur, highlight_id: uuid.UUID) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT hc.id, hc.body, hc.created_at, hc.parent_id, hc.guest_name,
                  v.nickname AS visitor_nick,
                  COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1)) AS nick,
                  u.email, u.role::text AS user_role
           FROM highlight_comment hc
           JOIN "user" u ON u.id = hc.user_id
           LEFT JOIN user_profile up ON up.user_id = u.id
           LEFT JOIN visitor v ON v.id = hc.visitor_id
           WHERE hc.highlight_id = %s
           ORDER BY hc.created_at ASC""",
        (highlight_id,),
    )
    guest_sys_email = GUEST_COMMENT_EMAIL.lower()
    flat: list[dict[str, Any]] = []
    for r in cur.fetchall():
        cid, body, cat, parent_id, gn, visitor_nick, nick, uemail, user_role = r
        guest_sys = (uemail or "").lower() == guest_sys_email
        label, is_admin = _comment_author_label(gn, visitor_nick, nick, uemail, user_role, guest_sys)
        flat.append(
            {
                "id": str(cid),
                "body": body or "",
                "created_at": cat.isoformat() if cat else "",
                "parent_id": str(parent_id) if parent_id else None,
                "author": label,
                "is_admin": is_admin,
            }
        )
    by_id = {c["id"]: {**c, "replies": []} for c in flat}
    roots: list[dict[str, Any]] = []
    for c in flat:
        node = by_id[c["id"]]
        if c["parent_id"] and c["parent_id"] in by_id:
            by_id[c["parent_id"]]["replies"].append(node)
        else:
            roots.append(node)
    return roots


def list_article_highlights(article_id: uuid.UUID) -> list[dict[str, Any]]:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT h.id, h.exact_text, h.prefix_text, h.suffix_text, h.created_at,
                          h.visitor_id, v.nickname AS visitor_nick,
                          COALESCE(NULLIF(TRIM(up.nickname), ''), SPLIT_PART(u.email, '@', 1)) AS user_nick,
                          u.email
                   FROM article_highlight h
                   LEFT JOIN visitor v ON v.id = h.visitor_id
                   LEFT JOIN "user" u ON u.id = h.user_id
                   LEFT JOIN user_profile up ON up.user_id = u.id
                   WHERE h.article_id = %s
                   ORDER BY h.created_at ASC""",
                (article_id,),
            )
            rows = cur.fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                hid, exact, prefix, suffix, cat, vid, vnick, unick, uemail = r
                if vid and vnick:
                    author = _safe_text(vnick) or "访客"
                elif uemail:
                    author = _safe_text(unick) or uemail.split("@")[0]
                else:
                    author = "访客"
                out.append(
                    {
                        "id": str(hid),
                        "exact_text": exact or "",
                        "prefix_text": prefix or "",
                        "suffix_text": suffix or "",
                        "created_at": cat.isoformat() if cat else "",
                        "author": author,
                        "comments": _fetch_highlight_comments(cur, hid),
                    }
                )
            return out


def create_article_highlight(
    article_id: uuid.UUID,
    *,
    exact_text: str,
    prefix_text: str,
    suffix_text: str,
    user_id: uuid.UUID | None,
    visitor_id: uuid.UUID | None,
) -> dict[str, Any]:
    exact = _safe_text(exact_text)
    if not exact:
        raise ValueError("划线内容不能为空")
    prefix = _safe_text(prefix_text)[:200]
    suffix = _safe_text(suffix_text)[:200]
    hid = uuid.uuid4()
    gid = get_guest_comment_user_id()
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM article WHERE id = %s", (article_id,))
                if not cur.fetchone():
                    raise LookupError("article not found")
                uid = user_id if user_id and user_id != gid else None
                vid = visitor_id if not uid else None
                if not uid and not vid:
                    raise ValueError("需要访客或登录身份")
                cur.execute(
                    """INSERT INTO article_highlight
                       (id, article_id, visitor_id, user_id, exact_text, prefix_text, suffix_text)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (hid, article_id, vid, uid, exact, prefix, suffix),
                )
    highlights = list_article_highlights(article_id)
    for h in highlights:
        if h["id"] == str(hid):
            return h
    return {
        "id": str(hid),
        "exact_text": exact,
        "prefix_text": prefix,
        "suffix_text": suffix,
        "created_at": datetime.now().isoformat(),
        "author": "访客",
        "comments": [],
    }


def add_highlight_comment(
    highlight_id: uuid.UUID,
    body: str,
    *,
    parent_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    guest_name: str | None,
    visitor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    text = _safe_text(body)
    if not text:
        raise ValueError("评论内容不能为空")
    gid = get_guest_comment_user_id()
    cid = uuid.uuid4()
    gn = _safe_text(guest_name)[:80] if guest_name else None
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT article_id FROM article_highlight WHERE id = %s", (highlight_id,))
                row = cur.fetchone()
                if not row:
                    raise LookupError("highlight not found")
                article_id = row[0]
                if parent_id:
                    cur.execute(
                        "SELECT 1 FROM highlight_comment WHERE id = %s AND highlight_id = %s",
                        (parent_id, highlight_id),
                    )
                    if not cur.fetchone():
                        raise ValueError("父评论不存在")
                if user_id and user_id != gid:
                    cur.execute(
                        """INSERT INTO highlight_comment
                           (id, highlight_id, parent_id, body, user_id, guest_name, visitor_id)
                           VALUES (%s, %s, %s, %s, %s, NULL, NULL)""",
                        (cid, highlight_id, parent_id, text, user_id),
                    )
                else:
                    if not visitor_id:
                        raise ValueError("visitor_id required")
                    if not gn:
                        raise ValueError("guest_name required")
                    cur.execute("UPDATE visitor SET nickname = %s WHERE id = %s", (gn, visitor_id))
                    cur.execute(
                        """INSERT INTO highlight_comment
                           (id, highlight_id, parent_id, body, user_id, guest_name, visitor_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (cid, highlight_id, parent_id, text, gid, gn, visitor_id),
                    )
    highlights = list_article_highlights(article_id)
    for h in highlights:
        if h["id"] == str(highlight_id):
            return {"ok": True, "highlight": h}
    raise LookupError("highlight not found")


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
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError("请提供有效的 category_id（须为已存在的分类 UUID）")
    try:
        cid = uuid.UUID(str(raw).strip())
    except Exception:
        raise ValueError("category_id 格式无效，须为 UUID") from None
    cur.execute("SELECT 1 FROM category WHERE id = %s", (cid,))
    if not cur.fetchone():
        raise ValueError("分类不存在，请检查 category_id")
    return cid


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

    out = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_id=None)
    assert out
    schedule_related_cache_refresh(aid)
    _schedule_rag_reindex()
    return out


def update_article(aid: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_id=None)
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

    out = get_article_dict(aid, allow_draft=True, viewer_user_id=None, visitor_id=None)
    assert out
    schedule_related_cache_refresh(aid)
    _schedule_rag_reindex()
    return out


def delete_article(aid: uuid.UUID) -> bool:
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM article WHERE id = %s", (aid,))
            ok = cur.rowcount > 0
    if ok:
        _schedule_rag_reindex()
    return ok


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
    visitor_id: uuid.UUID | None = None,
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
                        """INSERT INTO comment (id, body, article_id, user_id, guest_name, visitor_id)
                           VALUES (%s, %s, %s, %s, NULL, NULL)""",
                        (cid, text, article_id, user_id),
                    )
                else:
                    if not visitor_id:
                        raise ValueError("visitor_id required")
                    if not gn:
                        raise ValueError("guest_name required")
                    cur.execute("UPDATE visitor SET nickname = %s WHERE id = %s", (gn, visitor_id))
                    cur.execute(
                        """INSERT INTO comment (id, body, article_id, user_id, guest_name, visitor_id)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (cid, text, article_id, gid, gn, visitor_id),
                    )
    return {"id": str(cid), "ok": True}


def set_article_reaction(
    article_id: uuid.UUID,
    kind: str,
    *,
    user_id: uuid.UUID | None,
    visitor_id: uuid.UUID | None,
) -> dict[str, Any]:
    if kind not in ("like", "dislike", "none"):
        raise ValueError("bad kind")
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
                    if not visitor_id:
                        raise ValueError("visitor_id required")
                    if kind == "none":
                        cur.execute(
                            "DELETE FROM article_visitor_reaction WHERE article_id = %s AND visitor_id = %s",
                            (article_id, visitor_id),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO article_visitor_reaction (article_id, visitor_id, kind, updated_at)
                               VALUES (%s, %s, %s, NOW())
                               ON CONFLICT (article_id, visitor_id) DO UPDATE SET kind = EXCLUDED.kind, updated_at = NOW()""",
                            (article_id, visitor_id, kind),
                        )
    row = get_article_dict(article_id, True, user_id, visitor_id if not user_id else None)
    return {
        "ok": True,
        "likes": row["likes"] if row else 0,
        "dislikes": row["dislikes"] if row else 0,
        "my_reaction": row.get("my_reaction") if row else None,
    }


def get_blog_stats() -> dict[str, int]:
    """返回博客概览统计：文章数、互动总次数、累计访问人数。"""
    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM article WHERE status = 'published'")
            article_count: int = cur.fetchone()[0]  # type: ignore[index]

            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM article_reaction        WHERE kind <> 'none') +
                    (SELECT COUNT(*) FROM article_visitor_reaction WHERE kind <> 'none') +
                    (SELECT COUNT(*) FROM comment)
            """)
            interactions: int = cur.fetchone()[0]  # type: ignore[index]

            cur.execute("SELECT COUNT(*) FROM visitor")
            visitors: int = cur.fetchone()[0]  # type: ignore[index]

    return {
        "articles": int(article_count),
        "interactions": int(interactions),
        "visitors": int(visitors),
    }
