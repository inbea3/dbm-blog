# -*- coding: utf-8 -*-
"""Neon / PostgreSQL：从环境变量解析 DSN，并懒加载 psycopg 连接池。"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

_singleton: NeonDatabase | None = None


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


_load_dotenv()


class NeonDatabase:
    """优先 ``DATABASE_URL`` / ``BLOG_DATABASE_URL``；否则用标准 ``PG*`` 环境变量拼 DSN。"""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        min_size: int = 1,
        max_size: int = 8,
        connect_timeout: int = 20,
    ) -> None:
        self._dsn_override = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._connect_timeout = connect_timeout
        self._pool: ConnectionPool | None = None

    @classmethod
    def _dsn_from_pg_env(cls) -> str | None:
        host = (os.environ.get("PGHOST") or "").strip()
        db = (os.environ.get("PGDATABASE") or "").strip()
        user = (os.environ.get("PGUSER") or "").strip()
        pw = (os.environ.get("PGPASSWORD") or "").strip()
        if not (host and db and user and pw):
            return None
        ssl = (os.environ.get("PGSSLMODE") or "require").strip() or "require"
        ch = (os.environ.get("PGCHANNELBINDING") or "").strip()
        base = (
            f"postgresql://{quote_plus(user)}:{quote_plus(pw)}@{host}/"
            f"{quote_plus(db)}?sslmode={quote_plus(ssl)}"
        )
        if ch:
            base += f"&channel_binding={quote_plus(ch)}"
        return base

    @classmethod
    def resolve_dsn(cls) -> str | None:
        url = (os.environ.get("DATABASE_URL") or os.environ.get("BLOG_DATABASE_URL") or "").strip()
        if url:
            return url
        return cls._dsn_from_pg_env()

    def _effective_dsn(self) -> str:
        if self._dsn_override is not None:
            if not self._dsn_override.strip():
                raise RuntimeError("NeonDatabase: 显式传入的 DSN 为空")
            return self._dsn_override
        dsn = self.resolve_dsn()
        if not dsn:
            raise RuntimeError(
                "database URL 为空：请设置 DATABASE_URL / BLOG_DATABASE_URL，"
                "或同时设置 PGHOST、PGDATABASE、PGUSER、PGPASSWORD（可选 PGSSLMODE、PGCHANNELBINDING）。"
            )
        return dsn

    def _ensure_pool(self) -> ConnectionPool:
        if self._pool is None:
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                self._effective_dsn(),
                min_size=self._min_size,
                max_size=self._max_size,
                kwargs={"connect_timeout": self._connect_timeout},
            )
        return self._pool

    @property
    def pool(self) -> ConnectionPool:
        return self._ensure_pool()

    def connection(self):
        """与 ``with neon.connection() as conn:`` 用法一致。"""
        return self.pool.connection()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None


def get_neon_database() -> NeonDatabase:
    global _singleton
    if _singleton is None:
        _singleton = NeonDatabase()
    return _singleton
