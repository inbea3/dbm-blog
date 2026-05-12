#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用法: python set_user_password.py <邮箱> <明文密码>（写入 Werkzeug password_hash）"""

from __future__ import annotations

import sys

import psycopg
from neon_db import NeonDatabase
from werkzeug.security import generate_password_hash


def main() -> None:
    if len(sys.argv) != 3:
        print("用法: python set_user_password.py <邮箱> <新密码明文>")
        raise SystemExit(1)
    email, plain = sys.argv[1].strip(), sys.argv[2]
    if not email or not plain:
        print("邮箱与密码不能为空")
        raise SystemExit(1)
    url = NeonDatabase.resolve_dsn()
    if not url:
        print("未配置数据库（DATABASE_URL / BLOG_DATABASE_URL 或 PGHOST+PGDATABASE+PGUSER+PGPASSWORD）")
        raise SystemExit(1)
    ph = generate_password_hash(plain)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "user" SET password_hash = %s WHERE lower(email) = lower(%s)""",
                (ph, email),
            )
            n = cur.rowcount
        conn.commit()
    if n == 0:
        print("未找到该邮箱对应的用户，未做任何修改")
        raise SystemExit(1)
    print("已更新 password_hash，请用该邮箱与刚才的明文密码在站点上登录。")


if __name__ == "__main__":
    main()
