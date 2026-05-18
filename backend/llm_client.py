# -*- coding: utf-8 -*-
"""OpenAI 兼容 LLM 调用（DeepSeek 等）。"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    v = os.environ.get(name)
    if v is None:
        return ""
    return str(v).strip().strip("'\"")


def is_configured() -> bool:
    return bool(_env("API_KEY") and _env("BASE_URL") and _env("MODEL_NAME"))


def _chat_completions_url() -> str:
    base = _env("BASE_URL").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 90,
) -> str | None:
    """多轮对话，返回助手回复文本；失败返回 None。"""
    if not is_configured() or not messages:
        return None

    payload = {
        "model": _env("MODEL_NAME"),
        "temperature": max(0.0, min(float(temperature), 2.0)),
        "max_tokens": max(256, min(int(max_tokens), 4096)),
        "messages": messages,
    }

    req = urllib.request.Request(
        _chat_completions_url(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_env('API_KEY')}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("LLM HTTP error %s: %s", e.code, err)
        return None
    except Exception as e:
        logger.warning("LLM chat failed: %s", e)
        return None

    choices = body.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    return str(content).strip() if content else None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _article_brief(article: dict[str, Any]) -> dict[str, Any]:
    tags = article.get("tags") or []
    tag_names = [t.get("name") for t in tags if isinstance(t, dict) and t.get("name")]
    cat = article.get("category") or {}
    return {
        "id": str(article.get("id") or ""),
        "title": str(article.get("title") or "")[:200],
        "summary": str(article.get("summary") or "")[:400],
        "category": str(cat.get("name") or "")[:80],
        "tags": tag_names[:12],
    }


def rank_related_articles(
    source: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    limit: int = 6,
) -> list[dict[str, Any]] | None:
    """
    使用 LLM 对候选文章按阅读相关性打分并排序。
    成功时返回带 match_reason、relevance(llm_score) 的列表；失败返回 None。
    """
    if not is_configured() or not candidates:
        return None

    limit = max(1, min(int(limit or 6), 12))
    pool = candidates[:15]

    payload = {
        "model": _env("MODEL_NAME"),
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是博客编辑助手，负责为读者推荐「读完当前文章后最可能继续阅读」的相关文章。"
                    "请根据主题延续性、标签/分类关联、内容互补性综合判断，不要只看标题字面相似。"
                    "只输出 JSON，不要输出其它文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前文章：\n{json.dumps(_article_brief(source), ensure_ascii=False)}\n\n"
                    f"候选文章（id 必须从下列列表中选择）：\n"
                    f"{json.dumps([_article_brief(c) for c in pool], ensure_ascii=False)}\n\n"
                    f"请挑选最相关的 {limit} 篇，按相关度从高到低排序。"
                    "输出格式："
                    '{"recommendations":[{"id":"候选id","score":0-100,"reason":"一句话中文推荐理由"}]}'
                ),
            },
        ],
    }

    req = urllib.request.Request(
        _chat_completions_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_env('API_KEY')}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("LLM HTTP error %s: %s", e.code, err)
        return None
    except Exception as e:
        logger.warning("LLM request failed: %s", e)
        return None

    choices = body.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content") or ""
    parsed = _extract_json_object(content)
    if not parsed:
        logger.warning("LLM response is not valid JSON")
        return None

    recs = parsed.get("recommendations") or parsed.get("articles") or []
    if not isinstance(recs, list):
        return None

    by_id = {str(c["id"]): c for c in pool if c.get("id")}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in recs:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid or cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        base = dict(by_id[cid])
        try:
            llm_score = float(item.get("score", 0))
        except (TypeError, ValueError):
            llm_score = 0.0
        llm_score = max(0.0, min(llm_score, 100.0))
        reason = str(item.get("reason") or "").strip()
        if reason:
            base["match_reason"] = reason[:120]
        base["relevance"] = round(llm_score, 1)
        base["match_source"] = "llm"
        out.append(base)
        if len(out) >= limit:
            break

    if not out:
        return None
    return out
