from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional


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
        max_retries: int = 3,
        target_valid_comments: int = 0,
        max_pages: int = 0,
        saturation_pages: int = 3,
        saturation_min_new_ratio: float = 0.08,
        quality_predicate: Callable[[str], bool] | None = None,
    ):
        self.api_client = api_client
        self.include_replies = include_replies
        self.max_comments = max_comments
        self.page_size = max(1, page_size)
        self.retry_delay = retry_delay
        self.max_retries = max(1, int(max_retries or 1))
        self.target_valid_comments = max(0, int(target_valid_comments or 0))
        self.max_pages = max(0, int(max_pages or 0))
        self.saturation_pages = max(2, int(saturation_pages or 2))
        self.saturation_min_new_ratio = max(0.0, min(float(saturation_min_new_ratio), 1.0))
        self.quality_predicate = quality_predicate or (lambda text: len("".join(text.split())) >= 6)
        self.last_error = ""
        self.stats: Dict[str, Any] = {}

    async def collect(self, aweme_id: str) -> Optional[List[Dict[str, Any]]]:
        all_comments: List[Dict[str, Any]] = []
        cursor = 0
        seen_ids: set[str] = set()
        seen_texts: set[str] = set()
        valid_count = 0
        page_count = 0
        novelty_history: List[float] = []
        stop_reason = "completed"

        while True:
            page = None
            for attempt in range(self.max_retries):
                try:
                    page = await self.api_client.get_aweme_comments(
                        aweme_id,
                        cursor=cursor,
                        count=self.page_size,
                        include_replies=self.include_replies,
                    )
                    self.last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    if attempt >= self.max_retries - 1:
                        print(f"[WARN] Comments fetch error for {aweme_id} cursor={cursor}: {exc}")
                        self._set_stats(page_count, all_comments, valid_count, "request_failed", novelty_history)
                        return None
                    await asyncio.sleep(self.retry_delay * (2**attempt))
            if page is None:
                self._set_stats(page_count, all_comments, valid_count, "empty_response", novelty_history)
                return None

            items = page.get("items") or []
            if not items:
                stop_reason = "no_more_items"
                break

            page_count += 1
            new_valid = 0

            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("cid") or item.get("comment_id")
                text = " ".join(str(item.get("text") or item.get("content") or "").split()).strip()
                text_key = "".join(text.lower().split())
                key = str(cid) if cid else f"text:{text_key}"
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                if text_key and text_key in seen_texts:
                    continue
                if text_key:
                    seen_texts.add(text_key)
                all_comments.append(item)
                if text and self.quality_predicate(text):
                    valid_count += 1
                    new_valid += 1
                if 0 < self.max_comments <= len(all_comments):
                    stop_reason = "max_comments"
                    result = all_comments[: self.max_comments]
                    self._set_stats(page_count, result, valid_count, stop_reason, novelty_history)
                    return result

            novelty_history.append(new_valid / max(1, len(items)))
            if self.target_valid_comments and valid_count >= self.target_valid_comments:
                stop_reason = "target_valid_comments"
                break
            if self.max_pages and page_count >= self.max_pages:
                stop_reason = "max_pages"
                break
            if (
                page_count >= self.saturation_pages
                and len(novelty_history) >= self.saturation_pages
                and all(value <= self.saturation_min_new_ratio for value in novelty_history[-self.saturation_pages :])
            ):
                stop_reason = "signal_saturation"
                break

            if not page.get("has_more"):
                stop_reason = "no_more_pages"
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                print(f"[WARN] Comments cursor stuck for {aweme_id} at cursor={cursor}, stopping.")
                stop_reason = "cursor_stuck"
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay * 0.1)

        self._set_stats(page_count, all_comments, valid_count, stop_reason, novelty_history)
        return all_comments

    def _set_stats(
        self,
        page_count: int,
        comments: List[Dict[str, Any]],
        valid_count: int,
        stop_reason: str,
        novelty_history: List[float],
    ) -> None:
        self.stats = {
            "pages_scanned": page_count,
            "comment_count": len(comments),
            "valid_comment_count": valid_count,
            "stop_reason": stop_reason,
            "last_page_new_ratio": round(novelty_history[-1], 3) if novelty_history else None,
        }
