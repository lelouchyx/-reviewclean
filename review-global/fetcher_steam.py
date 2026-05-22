from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
SPACE_PATTERN = re.compile(r"\s+")
HELPFUL_PATTERN = re.compile(r"(\d[\d,]*)\s+people found this review helpful", re.IGNORECASE)
FUNNY_PATTERN = re.compile(r"(\d[\d,]*)\s+people found this review funny", re.IGNORECASE)
UPDATED_PATTERN = re.compile(r"last edited\s*:\s*(.+)$", re.IGNORECASE)
POSTED_PATTERN = re.compile(r"posted\s*:\s*(.+?)(?:\s+last edited\s*:|$)", re.IGNORECASE)
ZH_UPDATED_PATTERN = re.compile(r"最后编辑\s*[:：]\s*(.+)$")
ZH_POSTED_PATTERN = re.compile(r"发布于\s*[:：]\s*(.+?)(?:\s+最后编辑\s*[:：]|$)")
STEAM_APP_ID_PATTERN = re.compile(r"/app/(\d+)")
STEAM_REVIEWS_API = "https://store.steampowered.com/appreviews/{app_id}"


@dataclass
class SteamReview:
    text: str
    source: str
    source_type: str = "steam"
    author: str | None = None
    posted_at: str | None = None
    updated_at: str | None = None
    is_updated: bool = False
    recommended: bool | None = None
    helpful_votes: int | None = None
    funny_votes: int | None = None
    playtime_hours: float | None = None


def normalize_space(text: str) -> str:
    return SPACE_PATTERN.sub(" ", text).strip()


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def parse_playtime_hours(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d,.]+)\s*hrs?", text, re.IGNORECASE)
    if not match:
        match = re.search(r"([\d,.]+)\s*小时", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_date_line(date_line: str) -> tuple[str | None, str | None, bool]:
    text = normalize_space(date_line)
    if not text:
        return None, None, False

    posted = None
    updated = None

    m_posted = POSTED_PATTERN.search(text)
    if m_posted:
        posted = normalize_space(m_posted.group(1))
    m_updated = UPDATED_PATTERN.search(text)
    if m_updated:
        updated = normalize_space(m_updated.group(1))

    if posted is None:
        m_posted_zh = ZH_POSTED_PATTERN.search(text)
        if m_posted_zh:
            posted = normalize_space(m_posted_zh.group(1))
    if updated is None:
        m_updated_zh = ZH_UPDATED_PATTERN.search(text)
        if m_updated_zh:
            updated = normalize_space(m_updated_zh.group(1))

    if posted is None:
        posted = text

    is_updated = updated is not None or bool(re.search(r"last edited|最后编辑|edited", text, re.IGNORECASE))
    return posted, updated, is_updated


def strip_review_text(raw_text: str) -> str:
    text = raw_text.replace("Recommended", "").replace("Not Recommended", "")
    text = re.sub(r"^Posted:\s*.+?(?=\n)", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^发布于[:：].+?(?=\n)", "", text, flags=re.DOTALL)
    return normalize_space(text)


def extract_steam_app_id(url: str) -> str | None:
    parsed = urlparse(url)
    match = STEAM_APP_ID_PATTERN.search(parsed.path)
    return match.group(1) if match else None


def format_timestamp(timestamp: int | float | None) -> str | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def to_steam_review_from_api(item: dict, source_url: str) -> SteamReview | None:
    text = normalize_space(str(item.get("review") or ""))
    if not text:
        return None

    author_info = item.get("author") if isinstance(item.get("author"), dict) else {}
    playtime_minutes = author_info.get("playtime_forever") if isinstance(author_info, dict) else None
    playtime_hours = None
    if isinstance(playtime_minutes, (int, float)):
        playtime_hours = round(float(playtime_minutes) / 60.0, 1)

    created_ts = item.get("timestamp_created")
    updated_ts = item.get("timestamp_updated")
    created_int = int(created_ts) if isinstance(created_ts, (int, float)) else None
    updated_int = int(updated_ts) if isinstance(updated_ts, (int, float)) else None

    posted_at = format_timestamp(created_int)
    updated_at = format_timestamp(updated_int) if updated_int and created_int and updated_int > created_int else None
    is_updated = bool(updated_int and created_int and updated_int > created_int)

    helpful_votes = item.get("votes_up") if isinstance(item.get("votes_up"), int) else None
    funny_votes = item.get("votes_funny") if isinstance(item.get("votes_funny"), int) else None
    recommended = item.get("voted_up") if isinstance(item.get("voted_up"), bool) else None

    return SteamReview(
        text=text,
        source=source_url,
        author=None,
        posted_at=posted_at,
        updated_at=updated_at,
        is_updated=is_updated,
        recommended=recommended,
        helpful_votes=helpful_votes,
        funny_votes=funny_votes,
        playtime_hours=playtime_hours,
    )


def collect_from_steam_api(url: str, limit: int = 200, timeout: float = 20.0) -> list[SteamReview]:
    app_id = extract_steam_app_id(url)
    if not app_id:
        return []

    endpoint = STEAM_REVIEWS_API.format(app_id=app_id)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    cursor = "*"
    collected: list[SteamReview] = []
    seen_texts: set[str] = set()

    while len(collected) < limit:
        remaining = min(100, limit - len(collected))
        params = {
            "json": 1,
            "language": "all",
            "filter": "recent",
            "review_type": "all",
            "purchase_type": "all",
            "day_range": 7,
            "num_per_page": remaining,
            "cursor": cursor,
        }
        response = session.get(endpoint, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()

        reviews_payload = payload.get("reviews") if isinstance(payload, dict) else None
        if not isinstance(reviews_payload, list) or not reviews_payload:
            break

        added = 0
        for item in reviews_payload:
            if not isinstance(item, dict):
                continue
            review = to_steam_review_from_api(item, source_url=url)
            if review is None:
                continue
            key = normalize_space(review.text)
            if not key or key in seen_texts:
                continue
            seen_texts.add(key)
            collected.append(review)
            added += 1
            if len(collected) >= limit:
                break

        if added == 0:
            break

        next_cursor = payload.get("cursor") if isinstance(payload, dict) else None
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return collected[:limit]


def set_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["p"] = [str(page)]
    new_query = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def extract_more_content_request(html: str, source_url: str) -> tuple[str, dict[str, str]] | None:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#MoreContentForm1, form[name='MoreContentForm1'], form[action*='/homecontent/']")
    if form is None:
        return None

    action = str(form.get("action") or "").strip()
    if not action:
        return None

    params: dict[str, str] = {}
    for input_tag in form.select("input[name]"):
        name = str(input_tag.get("name") or "").strip()
        if not name:
            continue
        params[name] = str(input_tag.get("value") or "")

    if not params:
        return None

    return urljoin(source_url, action), params


def extract_reviews_from_page(html: str, source_url: str) -> list[SteamReview]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.apphub_Card")
    reviews: list[SteamReview] = []

    for card in cards:
        body = card.select_one("div.apphub_CardTextContent")
        if body is None:
            continue

        text = strip_review_text(body.get_text("\n", strip=True))
        if not text:
            continue

        author = None
        author_tag = card.select_one("div.apphub_CardContentAuthorName a")
        if author_tag is not None:
            author = normalize_space(author_tag.get_text(" ", strip=True)) or None

        title = normalize_space((card.select_one("div.title") or card).get_text(" ", strip=True)).lower()
        recommended: bool | None
        if "not recommended" in title or "不推荐" in title:
            recommended = False
        elif "recommended" in title or "推荐" in title:
            recommended = True
        else:
            recommended = None

        date_line = ""
        date_tag = card.select_one("div.date_posted")
        if date_tag is not None:
            date_line = date_tag.get_text(" ", strip=True)
        posted_at, updated_at, is_updated = parse_date_line(date_line)

        footer_text = normalize_space((card.select_one("div.found_helpful") or card).get_text(" ", strip=True))
        helpful_votes = parse_int(HELPFUL_PATTERN.search(footer_text).group(1)) if HELPFUL_PATTERN.search(footer_text) else None
        funny_votes = parse_int(FUNNY_PATTERN.search(footer_text).group(1)) if FUNNY_PATTERN.search(footer_text) else None

        hours_text = normalize_space((card.select_one("div.hours") or card).get_text(" ", strip=True))
        playtime_hours = parse_playtime_hours(hours_text)

        reviews.append(
            SteamReview(
                text=text,
                source=source_url,
                author=author,
                posted_at=posted_at,
                updated_at=updated_at,
                is_updated=is_updated,
                recommended=recommended,
                helpful_votes=helpful_votes,
                funny_votes=funny_votes,
                playtime_hours=playtime_hours,
            )
        )

    return reviews


def deduplicate_reviews(reviews: list[SteamReview]) -> list[SteamReview]:
    seen: set[str] = set()
    result: list[SteamReview] = []
    for review in reviews:
        key = normalize_space(f"{review.author or ''}\n{review.text}")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(review)
    return result


def collect_from_steam_html(url: str, limit: int = 200, timeout: float = 20.0, max_pages: int = 30) -> list[SteamReview]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    response = session.get(url, timeout=timeout)
    response.raise_for_status()

    initial_html = response.text
    collected = deduplicate_reviews(extract_reviews_from_page(initial_html, url))
    if len(collected) >= limit:
        return collected[:limit]

    more_content_request = extract_more_content_request(initial_html, url)
    page_round = 0

    while page_round < max_pages and len(collected) < limit and more_content_request is not None:
        action_url, params = more_content_request
        more_response = session.get(action_url, params=params, timeout=timeout)
        more_response.raise_for_status()

        fragment_html = more_response.text
        page_reviews = extract_reviews_from_page(fragment_html, url)
        if not page_reviews:
            break

        before = len(collected)
        collected = deduplicate_reviews(collected + page_reviews)
        if len(collected) == before:
            break

        more_content_request = extract_more_content_request(fragment_html, url)
        page_round += 1

    if len(collected) < limit:
        page = 2
        while page <= max_pages and len(collected) < limit:
            page_url = set_page(url, page)
            fallback_response = session.get(page_url, timeout=timeout)
            fallback_response.raise_for_status()

            page_reviews = extract_reviews_from_page(fallback_response.text, page_url)
            if not page_reviews:
                break

            before = len(collected)
            collected = deduplicate_reviews(collected + page_reviews)
            if len(collected) == before:
                break

            page += 1

    return collected[:limit]


def collect_from_steam(url: str, limit: int = 200, timeout: float = 20.0, max_pages: int = 30) -> list[SteamReview]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host.endswith("steamcommunity.com") and "/reviews" in parsed.path:
        html_reviews = collect_from_steam_html(url=url, limit=limit, timeout=timeout, max_pages=max_pages)
        if html_reviews:
            return html_reviews[:limit]

    api_reviews = collect_from_steam_api(url=url, limit=limit, timeout=timeout)
    if api_reviews:
        return api_reviews[:limit]

    return collect_from_steam_html(url=url, limit=limit, timeout=timeout, max_pages=max_pages)


def write_jsonl(reviews: list[SteamReview], output_path: Path) -> None:
    lines = [json.dumps(asdict(item), ensure_ascii=False) for item in reviews]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(reviews: list[SteamReview], output_path: Path) -> None:
    output_path.write_text("\n".join(item.text for item in reviews), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Steam 评论抓取器（含追评/编辑标记）。")
    parser.add_argument(
        "--source",
        default="https://steamcommunity.com/app/1260320/reviews/?browsefilter=trendweek&p=1&filterLanguage=default",
        help="Steam 评论页链接",
    )
    parser.add_argument("--output", type=Path, default=Path("steam_reviews.jsonl"), help="输出 JSONL 路径")
    parser.add_argument("--text-output", type=Path, help="额外输出纯文本评论，每行一条")
    parser.add_argument("--limit", type=int, default=200, help="最多抓取评论条数")
    parser.add_argument("--timeout", type=float, default=20.0, help="请求超时（秒）")
    parser.add_argument("--max-pages", type=int, default=30, help="最多翻页数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reviews = collect_from_steam(
        url=args.source,
        limit=args.limit,
        timeout=args.timeout,
        max_pages=args.max_pages,
    )

    write_jsonl(reviews, args.output)
    if args.text_output is not None:
        write_txt(reviews, args.text_output)

    updated_count = sum(1 for item in reviews if item.is_updated)
    summary = {
        "source": args.source,
        "count": len(reviews),
        "updated_reviews": updated_count,
        "output": str(args.output),
        "text_output": str(args.text_output) if args.text_output is not None else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if reviews:
        print("评论预览:")
        for item in reviews[:5]:
            updated_hint = " [追评/编辑]" if item.is_updated else ""
            print(f"- {item.text}{updated_hint}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
