from __future__ import annotations

import hashlib
import re
from typing import Any, Dict

from .schemas import CommentItem


SENSITIVE_PATTERNS = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已隐藏]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[邮箱已隐藏]"),
    (
        re.compile(
            r"(?i)(?:微信|vx|v信|wechat)(?:\s*(?:微信|vx|v信|wechat))*\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,19}"
        ),
        "[联系方式已隐藏]",
    ),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[证件号已隐藏]"),
)


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def stable_user_hash(*values: Any) -> str:
    identity = "|".join(str(value or "").strip() for value in values if str(value or "").strip())
    if not identity:
        return ""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def sanitize_comment(comment: CommentItem) -> CommentItem:
    metadata: Dict[str, Any] = dict(comment.metadata or {})
    identity_hash = str(metadata.get("user_hash") or stable_user_hash(comment.user_nickname, comment.cid))
    metadata.pop("ip_label", None)
    for key in ("uid", "sec_uid", "short_id", "unique_id"):
        metadata.pop(key, None)
    if identity_hash:
        metadata["user_hash"] = identity_hash
    return CommentItem(
        aweme_id=comment.aweme_id,
        text=redact_sensitive_text(comment.text),
        cid=comment.cid,
        like_count=comment.like_count,
        create_time=comment.create_time,
        user_nickname="",
        metadata=metadata,
    )
