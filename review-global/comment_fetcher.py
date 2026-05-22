from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from fetcher_steam import collect_from_steam
from fetcher_x import collect_from_x_url
from fetcher_youtube import (
    CollectedComment,
    collect_from_source as collect_from_youtube_or_generic,
    deduplicate_comments,
    is_url,
    normalize_space,
    write_jsonl,
    write_txt,
)


STEAM_HOSTS = {"steamcommunity.com", "www.steamcommunity.com"}
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.x.com", "mobile.twitter.com"}


def collect_from_source(source: str, limit: int, sort: str, timeout: float) -> list[CollectedComment]:
    if is_url(source):
        host = urlparse(source).netloc.lower().split(":")[0]
        if host in X_HOSTS or host.endswith("x.com") or host.endswith("twitter.com"):
            return collect_from_x_url(source, limit=limit, timeout=timeout)
        if host in STEAM_HOSTS or host.endswith("steamcommunity.com"):
            return collect_from_steam_url(source, limit=limit, timeout=timeout)
    return collect_from_youtube_or_generic(source=source, limit=limit, sort=sort, timeout=timeout)


def collect_from_steam_url(url: str, limit: int, timeout: float) -> list[CollectedComment]:
    reviews = collect_from_steam(url=url, limit=limit, timeout=timeout)
    comments: list[CollectedComment] = []
    for item in reviews:
        comments.append(
            CollectedComment(
                text=normalize_space(item.text),
                source=item.source,
                source_type="steam",
                author=item.author,
                published_at=item.posted_at,
                like_count=item.helpful_votes,
                recommended=item.recommended,
            )
        )
    return deduplicate_comments(comments)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一评论采集器：按来源分发到 YouTube 或 Steam 专用抓取器。")
    parser.add_argument("--source", required=True, help="输入来源，可为 txt/csv/jsonl 路径或网页链接")
    parser.add_argument("--output", type=Path, default=Path("fetched_comments.jsonl"), help="输出 JSONL 路径")
    parser.add_argument("--text-output", type=Path, help="额外输出纯文本评论，每行一条")
    parser.add_argument("--limit", type=int, default=100, help="最多抓取多少条评论")
    parser.add_argument("--sort", choices=["popular", "recent"], default="recent", help="YouTube 评论排序方式，默认 recent")
    parser.add_argument("--timeout", type=float, default=15.0, help="网页请求超时时间，单位秒")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    comments = collect_from_source(
        source=args.source,
        limit=args.limit,
        sort=args.sort,
        timeout=args.timeout,
    )

    write_jsonl(comments, args.output)
    if args.text_output is not None:
        write_txt(comments, args.text_output)

    summary = {
        "source": args.source,
        "count": len(comments),
        "output": str(args.output),
        "text_output": str(args.text_output) if args.text_output is not None else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if comments:
        print("评论预览:")
        for item in comments[:5]:
            print(f"- {item.text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
