from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from douyin.core.api_client import DouyinAPIClient

from .comments import CommentsCollector
from .cookies import load_cookies_from_storage_state
from .schemas import CommentItem, VideoItem


URL_RE = re.compile(r"https?://[^\s，。、“”\"'<>]+")


def extract_first_url(text: str) -> str:
    match = URL_RE.search(text or "")
    if not match:
        raise ValueError("没有找到抖音主页分享链接")
    return match.group(0).rstrip("/")


def parse_sec_uid(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("sec_uid", "sec_user_id"):
        if query.get(key):
            return query[key][0]
    match = re.search(r"/user/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _first_url_from_aweme(item: Dict[str, Any]) -> str:
    share = item.get("share_info") or {}
    for key in ("share_url", "url"):
        if share.get(key):
            return str(share[key])
    aweme_id = str(item.get("aweme_id") or "")
    return f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""


def normalize_aweme_item(item: Dict[str, Any]) -> VideoItem:
    stats = item.get("statistics") or {}
    desc = str(item.get("desc") or item.get("item_title") or "").strip()
    title = desc or str((item.get("share_info") or {}).get("share_title") or "").strip()
    aweme_id = str(item.get("aweme_id") or item.get("group_id") or "")
    return VideoItem(
        aweme_id=aweme_id,
        url=_first_url_from_aweme(item),
        title=title[:240],
        desc=desc[:800],
        create_time=_safe_int(item.get("create_time")),
        like_count=_safe_int(stats.get("digg_count")),
        comment_count=_safe_int(stats.get("comment_count")),
        share_count=_safe_int(stats.get("share_count")),
        collect_count=_safe_int(stats.get("collect_count")),
        metadata={
            "duration": item.get("duration"),
            "raw_statistics": stats,
        },
    )


def rank_videos_by_comment_count(items: List[VideoItem], limit: int = 20) -> List[VideoItem]:
    return sorted(
        items,
        key=lambda item: (int(item.comment_count or 0), int(item.like_count or 0), int(item.share_count or 0)),
        reverse=True,
    )[: max(1, int(limit or 20))]


async def resolve_profile_url(client: DouyinAPIClient, profile_text_or_url: str) -> str:
    url = extract_first_url(profile_text_or_url)
    if "v.douyin.com" in url or "iesdouyin.com" in url:
        resolved = await client.resolve_short_url(url)
        return resolved or url
    return url


async def collect_profile_videos(
    profile_url: str,
    *,
    top_n: int = 20,
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
) -> tuple[str, str, List[VideoItem]]:
    cookies = load_cookies_from_storage_state(storage_state_path)
    async with DouyinAPIClient(cookies=cookies) as client:
        resolved_url = await resolve_profile_url(client, profile_url)
        sec_uid = parse_sec_uid(resolved_url)
        if not sec_uid:
            raise ValueError(f"无法从主页链接解析 sec_uid: {resolved_url}")
        page = await client.get_user_post(sec_uid, count=max(20, int(top_n or 20)))
        raw_items = page.get("items") or []
        videos = [normalize_aweme_item(item) for item in raw_items if isinstance(item, dict)]
        videos = [item for item in videos if item.aweme_id]
        return resolved_url, sec_uid, rank_videos_by_comment_count(videos, limit=top_n)


def normalize_comment(aweme_id: str, item: Dict[str, Any]) -> CommentItem:
    user = item.get("user") or {}
    return CommentItem(
        aweme_id=aweme_id,
        cid=str(item.get("cid") or item.get("comment_id") or ""),
        text=str(item.get("text") or item.get("content") or "").strip(),
        like_count=_safe_int(item.get("digg_count")),
        create_time=_safe_int(item.get("create_time")),
        user_nickname=str(user.get("nickname") or ""),
        metadata={
            "reply_comment_total": _safe_int(item.get("reply_comment_total")),
            "ip_label": item.get("ip_label") or "",
        },
    )


async def collect_comments_for_videos(
    videos: List[VideoItem],
    *,
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    max_comments_per_video: int = 0,
    page_size: int = 20,
) -> List[CommentItem]:
    cookies = load_cookies_from_storage_state(storage_state_path)
    comments: List[CommentItem] = []
    async with DouyinAPIClient(cookies=cookies) as client:
        collector = CommentsCollector(client, include_replies=False, max_comments=max_comments_per_video, page_size=page_size)
        for index, video in enumerate(videos, 1):
            print(f"[{index}/{len(videos)}] 采集评论：{video.title[:40] or video.aweme_id}")
            raw_items = await collector.collect(video.aweme_id) or []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                comment = normalize_comment(video.aweme_id, item)
                if comment.text:
                    comments.append(comment)
    return comments
