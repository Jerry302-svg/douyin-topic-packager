from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


class CommentsCollector:
    """Collect comments for one Douyin video.

    This mirrors the pagination behavior used in the main Loudazhuang project:
    page through `/comment/list/`, dedupe by comment id, and stop when the
    cursor no longer moves.
    """

    def __init__(
        self,
        api_client,
        *,
        include_replies: bool = False,
        max_comments: int = 0,
        page_size: int = 20,
        retry_delay: float = 1.0,
    ):
        self.api_client = api_client
        self.include_replies = include_replies
        self.max_comments = max_comments
        self.page_size = max(1, page_size)
        self.retry_delay = retry_delay

    async def collect(self, aweme_id: str) -> Optional[List[Dict[str, Any]]]:
        all_comments: List[Dict[str, Any]] = []
        cursor = 0
        seen_ids: set[str] = set()

        while True:
            try:
                page = await self.api_client.get_aweme_comments(
                    aweme_id,
                    cursor=cursor,
                    count=self.page_size,
                    include_replies=self.include_replies,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Comments fetch error for {aweme_id} cursor={cursor}: {exc}")
                return None

            items = page.get("items") or []
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("cid") or item.get("comment_id")
                key = str(cid) if cid else ""
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                all_comments.append(item)
                if 0 < self.max_comments <= len(all_comments):
                    return all_comments[: self.max_comments]

            if not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                print(f"[WARN] Comments cursor stuck for {aweme_id} at cursor={cursor}, stopping.")
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay * 0.1)

        return all_comments
