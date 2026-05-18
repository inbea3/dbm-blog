# -*- coding: utf-8 -*-
"""博客全文 RAG 索引：分块 + 词项检索（无需额外 Embedding API）。"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from typing import Any

from neon_db import get_neon_database

_index_lock = threading.Lock()
_chunks: list[dict[str, Any]] = []
_loaded_fingerprint: str = ""

CHUNK_MAX = 480
CHUNK_OVERLAP = 72


def tokenize(text: str) -> set[str]:
    raw = (text or "").lower().strip()
    if not raw:
        return set()
    tokens: set[str] = set(re.findall(r"[a-z0-9]{2,}", raw))
    cjk = re.findall(r"[\u4e00-\u9fff]", raw)
    tokens.update(cjk)
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return tokens


def _strip_html(html: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def body_to_plain(body: str, fmt: str) -> str:
    if not body:
        return ""
    if (fmt or "md") == "txt":
        return body.strip()
    s = re.sub(r"```[\s\S]*?```", " ", body)
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[#>*_`~]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def chunk_plain_text(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not parts:
        parts = [text]

    chunks: list[str] = []
    buf = ""
    for p in parts:
        if len(p) > CHUNK_MAX:
            if buf:
                chunks.append(buf)
                buf = ""
            start = 0
            while start < len(p):
                end = min(start + CHUNK_MAX, len(p))
                chunks.append(p[start:end])
                if end >= len(p):
                    break
                start = max(end - CHUNK_OVERLAP, start + 1)
            continue
        candidate = f"{buf}\n\n{p}".strip() if buf else p
        if len(candidate) <= CHUNK_MAX:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def catalog_fingerprint(cur) -> str:
    cur.execute(
        """SELECT COUNT(*)::bigint, COALESCE(MAX(published_at)::text, '')
           FROM article WHERE status = 'published'"""
    )
    n, mx = cur.fetchone()
    return hashlib.sha256(f"rag:{n}|{mx}".encode()).hexdigest()[:32]


def _ensure_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blog_rag_meta (
            id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            catalog_fingerprint VARCHAR(64) NOT NULL,
            chunk_count INT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blog_rag_chunk (
            id UUID PRIMARY KEY,
            article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
            chunk_index INT NOT NULL,
            title VARCHAR(255) NOT NULL DEFAULT '',
            article_date VARCHAR(32) NOT NULL DEFAULT '',
            category_name VARCHAR(100) NOT NULL DEFAULT '',
            chunk_text TEXT NOT NULL,
            UNIQUE (article_id, chunk_index)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_blog_rag_chunk_article ON blog_rag_chunk(article_id)"
    )


def _fetch_published_articles(cur) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT a.id, a.title, a.body, a.summary, a.content_format, a.published_at,
                  c.name
           FROM article a
           LEFT JOIN category c ON c.id = a.category_id
           WHERE a.status = 'published'
           ORDER BY a.published_at DESC NULLS LAST"""
    )
    rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for rid, title, body, summary, fmt, published_at, cat_name in rows:
        fmt = fmt or "md"
        plain = body_to_plain(body or "", fmt)
        summ = (summary or "").strip()
        merged = plain
        if summ and summ not in plain[:200]:
            merged = f"{summ}\n\n{plain}" if plain else summ
        if not merged.strip():
            continue
        d = published_at.date().isoformat() if published_at else ""
        out.append(
            {
                "id": str(rid),
                "title": title or "",
                "date": d,
                "category": cat_name or "",
                "text": merged.strip(),
            }
        )
    return out


def _build_chunks_from_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []
    for art in articles:
        pieces = chunk_plain_text(art["text"])
        for i, piece in enumerate(pieces):
            built.append(
                {
                    "article_id": art["id"],
                    "chunk_index": i,
                    "title": art["title"],
                    "date": art["date"],
                    "category": art["category"],
                    "text": piece,
                    "tokens": tokenize(f"{art['title']} {piece}"),
                }
            )
    return built


def _save_chunks_to_db(cur, fp: str, chunks: list[dict[str, Any]]) -> None:
    cur.execute("DELETE FROM blog_rag_chunk")
    for c in chunks:
        cur.execute(
            """INSERT INTO blog_rag_chunk (
                   id, article_id, chunk_index, title, article_date, category_name, chunk_text
               ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                uuid.uuid4(),
                uuid.UUID(c["article_id"]),
                c["chunk_index"],
                c["title"][:255],
                c["date"][:32],
                c["category"][:100],
                c["text"],
            ),
        )
    cur.execute(
        """INSERT INTO blog_rag_meta (id, catalog_fingerprint, chunk_count, updated_at)
           VALUES (1, %s, %s, NOW())
           ON CONFLICT (id) DO UPDATE SET
               catalog_fingerprint = EXCLUDED.catalog_fingerprint,
               chunk_count = EXCLUDED.chunk_count,
               updated_at = NOW()""",
        (fp, len(chunks)),
    )


def _load_chunks_from_db(cur, fp: str) -> list[dict[str, Any]] | None:
    cur.execute(
        "SELECT catalog_fingerprint, chunk_count FROM blog_rag_meta WHERE id = 1"
    )
    meta = cur.fetchone()
    if not meta or meta[0] != fp or int(meta[1] or 0) == 0:
        return None
    cur.execute(
        """SELECT article_id, chunk_index, title, article_date, category_name, chunk_text
           FROM blog_rag_chunk ORDER BY title, chunk_index"""
    )
    rows = cur.fetchall()
    if not rows:
        return None
    out: list[dict[str, Any]] = []
    for aid, idx, title, d, cat, text in rows:
        out.append(
            {
                "article_id": str(aid),
                "chunk_index": idx,
                "title": title or "",
                "date": d or "",
                "category": cat or "",
                "text": text or "",
                "tokens": tokenize(f"{title} {text}"),
            }
        )
    return out


def rebuild_index() -> dict[str, Any]:
    """全量重建 RAG 索引并持久化到数据库。"""
    global _chunks, _loaded_fingerprint
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                _ensure_tables(cur)
                fp = catalog_fingerprint(cur)
                articles = _fetch_published_articles(cur)
                built = _build_chunks_from_articles(articles)
                _save_chunks_to_db(cur, fp, built)

    with _index_lock:
        _chunks = built
        _loaded_fingerprint = fp
    return {"chunk_count": len(built), "article_count": len(articles), "fingerprint": fp}


def ensure_index() -> None:
    global _chunks, _loaded_fingerprint
    with _index_lock:
        if _chunks and _loaded_fingerprint:
            with get_neon_database().connection() as conn:
                with conn.cursor() as cur:
                    _ensure_tables(cur)
                    fp = catalog_fingerprint(cur)
            if fp == _loaded_fingerprint:
                return

    with get_neon_database().connection() as conn:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            fp = catalog_fingerprint(cur)
            loaded = _load_chunks_from_db(cur, fp)
            if loaded is not None:
                with _index_lock:
                    _chunks = loaded
                    _loaded_fingerprint = fp
                return
            articles = _fetch_published_articles(cur)

    built = _build_chunks_from_articles(articles)
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                _save_chunks_to_db(cur, fp, built)

    with _index_lock:
        _chunks = built
        _loaded_fingerprint = fp


def index_stats() -> dict[str, Any]:
    ensure_index()
    with _index_lock:
        article_ids = {c["article_id"] for c in _chunks}
        return {
            "chunk_count": len(_chunks),
            "article_count": len(article_ids),
            "fingerprint": _loaded_fingerprint,
        }


def retrieve(query: str, *, top_k: int = 6) -> list[dict[str, Any]]:
    ensure_index()
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    with _index_lock:
        pool = list(_chunks)

    scored: list[tuple[float, dict[str, Any]]] = []
    q_lower = query.lower()
    for c in pool:
        tks = c.get("tokens") or set()
        if not tks:
            continue
        inter = len(q_tokens & tks)
        if inter == 0:
            continue
        score = inter / (len(q_tokens) ** 0.5)
        title = (c.get("title") or "").lower()
        if title and any(tok in title for tok in q_tokens if len(str(tok)) > 1):
            score += 0.35
        if q_lower and q_lower in (c.get("text") or "").lower():
            score += 0.5
        scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    seen_art: set[str] = set()
    out: list[dict[str, Any]] = []
    for score, c in scored:
        aid = c["article_id"]
        if aid in seen_art and len(out) >= top_k:
            continue
        seen_art.add(aid)
        snippet = c["text"]
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        out.append(
            {
                "article_id": aid,
                "title": c["title"],
                "date": c["date"],
                "category": c["category"],
                "snippet": snippet,
                "score": round(score, 4),
            }
        )
        if len(out) >= top_k:
            break
    return out
