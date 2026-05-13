#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 Neon 执行与 bootstrap 相同的数据库结构迁移（_ensure_schema / visitor 等）。

用法：在 blog 目录配置好 .env（DATABASE_URL 或 PG*），然后：
  python run_db_migrations.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证可导入 backend 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

import postgres_store  # noqa: E402
from neon_db import get_neon_database  # noqa: E402


def main() -> None:
    with get_neon_database().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (928374651,))
                postgres_store._ensure_schema(cur)
    print("数据库迁移已执行完成（_ensure_schema）。")


if __name__ == "__main__":
    main()
