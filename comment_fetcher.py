from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from comment_io import load_comments_from_path as load_comments_file

try:
    from youtube_comment_downloader import SORT_BY_POPULAR, SORT_BY_RECENT, YoutubeCommentDownloader  # type: ignore
except ImportError:
    SORT_BY_POPULAR = None
    SORT_BY_RECENT = None
    YoutubeCommentDownloader = None

try:
    from selenium import webdriver  # type: ignore
    from selenium.common.exceptions import TimeoutException, WebDriverException  # type: ignore
    from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
    from selenium.webdriver.common.by import By  # type: ignore
    from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
except ImportError:
    webdriver = None
    TimeoutException = None
    WebDriverException = None
    ChromeOptions = None
    EdgeOptions = None
    By = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
SPACE_PATTERN = re.compile(r"\s+")
LD_JSON_TYPE_PATTERN = re.compile(r"ld\+json", re.IGNORECASE)
COMMENT_HINT_PATTERN = re.compile(r"comment|review|reply|feedback|testimonial", re.IGNORECASE)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
NUMBER_PATTERN = re.compile(r"\d[\d,.]*")
YOUTUBE_COMMENT_THREAD_SELECTOR = "ytd-comment-thread-renderer"
YOUTUBE_COMMENT_COUNT_SELECTORS = (
    "ytd-comments-header-renderer #count h2 yt-formatted-string",
    "ytd-comments-header-renderer #title h2 yt-formatted-string",
    "ytd-comments-header-renderer #count",
)
YOUTUBE_SORT_BUTTON_SELECTORS = (
    "ytd-comments-header-renderer ytd-sort-filter-sub-menu-renderer yt-button-shape button",
    "ytd-comments-header-renderer #sort-menu button",
)
YOUTUBE_SORT_MENU_ITEM_SELECTOR = "ytd-menu-popup-renderer ytd-menu-service-item-renderer, tp-yt-paper-listbox ytd-menu-service-item-renderer"
YOUTUBE_EXPAND_BUTTON_SELECTOR = "ytd-comment-renderer #more, ytd-comment-renderer tp-yt-paper-button#more"
YOUTUBE_REPLY_EXPAND_BUTTON_SELECTORS = (
    "ytd-comment-replies-renderer #more-replies button",
    "ytd-comment-replies-renderer ytd-button-renderer#more-replies button",
    "ytd-comment-replies-renderer #expander yt-button-shape button",
    "ytd-comment-replies-renderer #expander button",
    "ytd-comment-replies-renderer yt-button-shape button",
    "ytd-comment-replies-renderer #more-replies",
)
YOUTUBE_REPLY_COLLAPSE_HINT = re.compile(r"reply|replies|回复|回覆|repl", re.IGNORECASE)


@dataclass
class CollectedComment:
    text: str
    source: str
    source_type: str
    author: str | None = None
    published_at: str | None = None
    like_count: int | None = None
    comment_id: str | None = None
    parent_id: str | None = None
    is_reply: bool = False
    thread_topic: str | None = None


def normalize_space(text: str) -> str:
    return SPACE_PATTERN.sub(" ", text).strip()


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def collect_from_source(source: str, limit: int, sort: str, timeout: float) -> list[CollectedComment]:
    if is_url(source):
        return collect_from_url(source, limit=limit, sort=sort, timeout=timeout)
    return collect_from_local_path(Path(source), limit=limit)


def collect_from_local_path(path: Path, limit: int) -> list[CollectedComment]:
    comments = load_comments_file(path)

    return [
        CollectedComment(text=text, source=str(path), source_type="file")
        for text in comments[:limit]
    ]


def collect_from_url(url: str, limit: int, sort: str, timeout: float) -> list[CollectedComment]:
    host = urlparse(url).netloc.lower()
    collector = resolve_url_collector(host)
    if collector == "youtube":
        return collect_from_youtube(url, limit=limit, sort=sort)
    return collect_from_generic_web(url, limit=limit, timeout=timeout)


def resolve_url_collector(host: str) -> str:
    if host in YOUTUBE_HOSTS:
        return "youtube"
    return "generic"


def collect_from_youtube(url: str, limit: int, sort: str) -> list[CollectedComment]:
    browser_comments: list[CollectedComment] = []
    displayed_total: int | None = None

    if webdriver is not None and ChromeOptions is not None and EdgeOptions is not None and By is not None:
        try:
            browser_comments, displayed_total = collect_from_youtube_with_browser(url, limit=limit, sort=sort)
        except Exception as error:
            print(f"YouTube 浏览器抓取失败，回退到 API 抓取: {error}")

    target_count = min(limit, displayed_total) if displayed_total is not None else limit
    if browser_comments and len(browser_comments) >= target_count:
        return browser_comments[:limit]

    api_comments = collect_from_youtube_api(url, limit=max(limit, target_count), sort=sort)
    if browser_comments:
        return deduplicate_comments(browser_comments + api_comments)[:limit]
    return api_comments[:limit]


def collect_from_youtube_api(url: str, limit: int, sort: str) -> list[CollectedComment]:
    if YoutubeCommentDownloader is None or SORT_BY_POPULAR is None or SORT_BY_RECENT is None:
        raise RuntimeError(
            "抓取 YouTube 评论需要安装 selenium 或 youtube-comment-downloader。\n"
            "推荐安装: pip install selenium youtube-comment-downloader"
        )

    downloader = YoutubeCommentDownloader()
    sort_by = SORT_BY_RECENT if sort == "recent" else SORT_BY_POPULAR
    comments: list[CollectedComment] = []

    for item in downloader.get_comments_from_url(url, sort_by=sort_by):
        text = normalize_space(str(item.get("text") or ""))
        if not text:
            continue
        comments.append(
            CollectedComment(
                text=text,
                source=url,
                source_type="youtube",
                author=normalize_space(str(item.get("author") or "")) or None,
                published_at=normalize_space(str(item.get("time") or "")) or None,
                like_count=parse_like_count(item.get("votes")),
            )
        )
        if len(comments) >= limit:
            break

    return deduplicate_comments(comments)


def collect_from_youtube_with_browser(
    url: str,
    limit: int,
    sort: str,
) -> tuple[list[CollectedComment], int | None]:
    driver = build_youtube_driver()
    try:
        driver.get(url)
        wait_for_youtube_comments(driver)
        displayed_total = read_youtube_comment_total(driver)
        apply_youtube_sort(driver, sort)
        target_count = min(limit, displayed_total) if displayed_total is not None else limit
        comments = scroll_youtube_comments(driver, url=url, target_count=target_count)
        return comments[:limit], displayed_total
    finally:
        driver.quit()


def build_youtube_driver() -> Any:
    if webdriver is None or ChromeOptions is None or EdgeOptions is None:
        raise RuntimeError("当前环境未安装 selenium，无法执行浏览器滚动抓取。")

    launch_errors: list[str] = []

    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1600,2400")
    chrome_options.add_argument(f"--user-agent={USER_AGENT}")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    edge_options = EdgeOptions()
    edge_options.add_argument("--headless=new")
    edge_options.add_argument("--disable-blink-features=AutomationControlled")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--window-size=1600,2400")
    edge_options.add_argument(f"--user-agent={USER_AGENT}")

    for browser_name, factory in (
        ("chrome", lambda: webdriver.Chrome(options=chrome_options)),
        ("edge", lambda: webdriver.Edge(options=edge_options)),
    ):
        try:
            driver = factory()
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return driver
        except Exception as error:
            launch_errors.append(f"{browser_name}: {error}")

    raise RuntimeError("无法启动 Chrome/Edge 浏览器: " + " | ".join(launch_errors))


def wait_for_youtube_comments(driver: Any, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_first_element(driver, YOUTUBE_COMMENT_COUNT_SELECTORS) is not None:
            return
        driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
        time.sleep(1.0)
    raise RuntimeError("未能加载到 YouTube 评论区。")


def read_youtube_comment_total(driver: Any, timeout: float = 10.0) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        element = find_first_element(driver, YOUTUBE_COMMENT_COUNT_SELECTORS)
        if element is not None:
            count = parse_displayed_count(element.text)
            if count is not None:
                return count
        time.sleep(0.5)
    return None


def apply_youtube_sort(driver: Any, sort: str, timeout: float = 8.0) -> None:
    target_index = 1 if sort == "recent" else 0
    button = find_first_element(driver, YOUTUBE_SORT_BUTTON_SELECTORS)
    if button is None:
        return

    driver.execute_script("arguments[0].click();", button)
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = driver.find_elements(By.CSS_SELECTOR, YOUTUBE_SORT_MENU_ITEM_SELECTOR)
        if len(items) > target_index:
            driver.execute_script("arguments[0].click();", items[target_index])
            time.sleep(1.5)
            return
        time.sleep(0.3)


def scroll_youtube_comments(
    driver: Any,
    url: str,
    target_count: int,
    scroll_pause: float = 1.0,
    max_idle_rounds: int = 5,
) -> list[CollectedComment]:
    collected: dict[str, CollectedComment] = {}
    idle_rounds = 0

    while idle_rounds < max_idle_rounds:
        expand_youtube_comment_bodies(driver)
        expand_youtube_replies(driver)
        before_count = len(collected)
        for comment in extract_visible_youtube_comments(driver, url):
            key = build_comment_key(comment)
            if key not in collected:
                collected[key] = comment

        if len(collected) >= target_count:
            break

        if len(collected) == before_count:
            idle_rounds += 1
        else:
            idle_rounds = 0

        scroll_youtube_comment_list(driver)
        time.sleep(scroll_pause)

    return list(collected.values())


def expand_youtube_comment_bodies(driver: Any) -> None:
    for button in driver.find_elements(By.CSS_SELECTOR, YOUTUBE_EXPAND_BUTTON_SELECTOR):
        try:
            if button.is_displayed() and button.is_enabled():
                driver.execute_script("arguments[0].click();", button)
        except Exception:
            continue


def expand_youtube_replies(driver: Any, max_clicks_per_round: int = 12) -> None:
    clicked = 0
    for selector in YOUTUBE_REPLY_EXPAND_BUTTON_SELECTORS:
        for button in driver.find_elements(By.CSS_SELECTOR, selector):
            if clicked >= max_clicks_per_round:
                return
            try:
                if not button.is_displayed() or not button.is_enabled():
                    continue
                label = normalize_space(button.text)
                if label and not YOUTUBE_REPLY_COLLAPSE_HINT.search(label):
                    continue
                driver.execute_script("arguments[0].click();", button)
                clicked += 1
            except Exception:
                continue

    if clicked < max_clicks_per_round:
        for button in driver.find_elements(By.CSS_SELECTOR, "button"):
            if clicked >= max_clicks_per_round:
                break
            try:
                if not button.is_displayed() or not button.is_enabled():
                    continue
                label = normalize_space(button.text)
                if not label or not YOUTUBE_REPLY_COLLAPSE_HINT.search(label):
                    continue
                driver.execute_script("arguments[0].click();", button)
                clicked += 1
            except Exception:
                continue


def extract_visible_youtube_comments(driver: Any, url: str) -> list[CollectedComment]:
    comments: list[CollectedComment] = []
    for thread in driver.find_elements(By.CSS_SELECTOR, YOUTUBE_COMMENT_THREAD_SELECTOR):
        text = first_element_text(thread, ("#content-text",))
        if not text:
            continue

        top_author = first_element_text(thread, ("#author-text span", "#header-author #author-text span")) or None
        top_published = first_element_text(thread, ("#published-time-text a", "a[href*='lc=']")) or None
        top_like = parse_like_count(first_element_text(thread, ("#vote-count-middle",)))
        top_id = extract_comment_id_from_element(thread)

        comments.append(
            CollectedComment(
                text=text,
                source=url,
                source_type="youtube",
                author=top_author,
                published_at=top_published,
                like_count=top_like,
                comment_id=top_id,
                parent_id=None,
                is_reply=False,
                thread_topic=text,
            )
        )

        reply_selector = "ytd-comment-replies-renderer ytd-comment-renderer, ytd-comment-replies-renderer ytd-comment-view-model"
        for reply_element in thread.find_elements(By.CSS_SELECTOR, reply_selector):
            reply_text = first_element_text(reply_element, ("#content-text",))
            if not reply_text:
                continue
            comments.append(
                CollectedComment(
                    text=reply_text,
                    source=url,
                    source_type="youtube",
                    author=first_element_text(reply_element, ("#author-text span", "#header-author #author-text span")) or None,
                    published_at=first_element_text(reply_element, ("#published-time-text a", "a[href*='lc=']")) or None,
                    like_count=parse_like_count(first_element_text(reply_element, ("#vote-count-middle",))),
                    comment_id=extract_comment_id_from_element(reply_element),
                    parent_id=top_id,
                    is_reply=True,
                    thread_topic=text,
                )
            )
    return comments


def build_comment_key(comment: CollectedComment) -> str:
    if comment.comment_id:
        return comment.comment_id
    author = normalize_space(comment.author or "")
    parent = comment.parent_id or ""
    return f"{author}\n{parent}\n{normalize_space(comment.text)}"


def extract_comment_id_from_element(root: Any) -> str | None:
    links = root.find_elements(By.CSS_SELECTOR, "#published-time-text a, a[href*='lc=']")
    for link in links:
        href = normalize_space(link.get_attribute("href") or "")
        if "lc=" not in href:
            continue
        match = re.search(r"[?&]lc=([^&]+)", href)
        if match:
            return match.group(1)
    return None


def scroll_youtube_comment_list(driver: Any) -> None:
    threads = driver.find_elements(By.CSS_SELECTOR, YOUTUBE_COMMENT_THREAD_SELECTOR)
    if threads:
        driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", threads[-1])
        driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.6));")
        return
    driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")


def find_first_element(driver: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            return elements[0]
    return None


def first_element_text(root: Any, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        elements = root.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            text = normalize_space(element.text)
            if text:
                return text
    return ""


def parse_displayed_count(text: str) -> int | None:
    match = NUMBER_PATTERN.search(text)
    if not match:
        return None
    digits_only = re.sub(r"\D", "", match.group())
    return int(digits_only) if digits_only else None


def parse_like_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = normalize_space(str(value)).replace(",", "")
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def collect_from_generic_web(url: str, limit: int, timeout: float) -> list[CollectedComment]:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    comments = extract_json_ld_comments(soup, url)
    if len(comments) < limit:
        comments.extend(extract_dom_comments(soup, url, limit=limit - len(comments)))

    deduplicated = deduplicate_comments(comments)
    return deduplicated[:limit]


def extract_json_ld_comments(soup: BeautifulSoup, url: str) -> list[CollectedComment]:
    comments: list[CollectedComment] = []
    for tag in soup.find_all("script", attrs={"type": LD_JSON_TYPE_PATTERN}):
        if not tag.string:
            continue
        try:
            payload = json.loads(tag.string)
        except json.JSONDecodeError:
            continue

        for node in walk_json(payload):
            if not isinstance(node, dict):
                continue
            node_type = normalize_space(str(node.get("@type") or "")).lower()
            if "review" not in node_type and "comment" not in node_type:
                continue

            text = first_non_empty_string(
                node.get("reviewBody"),
                node.get("commentText"),
                node.get("text"),
                node.get("description"),
            )
            text = normalize_space(text)
            if not looks_like_comment(text):
                continue

            comments.append(
                CollectedComment(
                    text=text,
                    source=url,
                    source_type="web",
                    author=extract_author(node.get("author")),
                    published_at=first_non_empty_string(node.get("datePublished"), node.get("dateCreated")) or None,
                )
            )
    return comments


def walk_json(payload: Any) -> list[Any]:
    nodes: list[Any] = []
    stack = [payload]
    while stack:
        current = stack.pop()
        nodes.append(current)
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return nodes


def first_non_empty_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            normalized = normalize_space(value)
            if normalized:
                return normalized
    return ""


def extract_author(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = normalize_space(value)
        return normalized or None
    if isinstance(value, dict):
        name = first_non_empty_string(value.get("name"), value.get("alternateName"))
        return name or None
    return None


def extract_dom_comments(soup: BeautifulSoup, url: str, limit: int) -> list[CollectedComment]:
    comments: list[CollectedComment] = []
    for tag in soup.find_all(["div", "article", "section", "li", "p", "span"]):
        attrs_text = " ".join(
            [
                str(tag.get("id") or ""),
                " ".join(tag.get("class", [])),
                str(tag.get("data-testid") or ""),
                str(tag.get("aria-label") or ""),
            ]
        )
        if not COMMENT_HINT_PATTERN.search(attrs_text):
            continue

        text = normalize_space(tag.get_text(" ", strip=True))
        if not looks_like_comment(text):
            continue

        comments.append(CollectedComment(text=text, source=url, source_type="web"))
        if len(comments) >= limit:
            break
    return comments


def looks_like_comment(text: str) -> bool:
    return 4 <= len(text) <= 600 and len(text.split()) <= 120


def deduplicate_comments(comments: list[CollectedComment]) -> list[CollectedComment]:
    seen: set[str] = set()
    deduplicated: list[CollectedComment] = []
    for item in comments:
        key = normalize_space(item.text)
        if not key or key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)
    return deduplicated


def write_jsonl(comments: list[CollectedComment], output_path: Path) -> None:
    lines = [json.dumps(asdict(comment), ensure_ascii=False) for comment in comments]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(comments: list[CollectedComment], output_path: Path) -> None:
    output_path.write_text("\n".join(comment.text for comment in comments), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立的评论采集器，支持本地文件和网页 URL。")
    parser.add_argument("--source", required=True, help="输入来源，可为 txt/csv/jsonl 路径或网页链接")
    parser.add_argument("--output", type=Path, default=Path("fetched_comments.jsonl"), help="输出 JSONL 路径")
    parser.add_argument("--text-output", type=Path, help="额外输出纯文本评论，每行一条")
    parser.add_argument("--limit", type=int, default=100, help="最多抓取多少条评论")
    parser.add_argument("--sort", choices=["popular", "recent"], default="popular", help="YouTube 评论排序方式")
    parser.add_argument("--timeout", type=float, default=15.0, help="普通网页请求超时时间，单位秒")
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