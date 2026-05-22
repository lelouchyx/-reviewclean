from __future__ import annotations

import asyncio
import argparse
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import requests

from fetcher_youtube import CollectedComment, deduplicate_comments, normalize_space, write_jsonl, write_txt

try:
    import browser_cookie3  # type: ignore
except ImportError:
    browser_cookie3 = None

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except ImportError:
    sync_playwright = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
PUBLIC_BEARER_TOKEN = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TWEET_DETAIL_QUERY_ID = "ju7f1DGV1TxWM2fCuD1Qmg"
STATUS_ID_PATTERN = re.compile(r"/status/(\d+)")
COOKIE_VALUE_PATTERN = re.compile(r"(?:^|;\s*)([^=;]+)=([^;]*)")
X_COOKIE_DOMAINS = ("x.com", ".x.com", "twitter.com", ".twitter.com")
X_HOME_URL = "https://x.com/home"
X_LOGIN_URL = "https://x.com/i/flow/login"
X_BROWSER_DATA_DIRNAME = "x_user_data_dir"
X_STORAGE_STATE_FILENAME = "x_storage_state.json"
X_BROWSER_CHANNEL = "msedge"
X_BROWSER_NAME = "Microsoft Edge"
NITTER_BASE_URL = "https://nitter.net"
X_PUBLIC_SNAPSHOT_DIRNAME = "x_public_reply_snapshots"

TWEET_DETAIL_FEATURES = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}
TWEET_DETAIL_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withArticleSummaryText": True,
    "withArticleVoiceOver": True,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}


@dataclass
class XSessionAuth:
    cookie_header: str
    csrf_token: str


@dataclass
class XCollectedThreadComment:
    comment: CollectedComment
    reply_count: int | None


def get_x_browser_data_dir() -> Path:
    return get_x_browser_data_root() / X_BROWSER_DATA_DIRNAME


def get_x_browser_data_root() -> Path:
    return Path(__file__).resolve().parent / "browser_data"


def get_x_storage_state_path() -> Path:
    return get_x_browser_data_root() / X_STORAGE_STATE_FILENAME


def get_x_public_snapshot_dir() -> Path:
    return get_x_browser_data_root() / X_PUBLIC_SNAPSHOT_DIRNAME


def get_x_public_snapshot_path(status_id: str) -> Path:
    return get_x_public_snapshot_dir() / f"{status_id}.jsonl"


def has_x_saved_login_state() -> bool:
    storage_state_path = get_x_storage_state_path()
    return storage_state_path.exists()


def ensure_playwright_event_loop_policy() -> None:
    if os.name != "nt":
        return

    windows_proactor_policy = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if windows_proactor_policy is None:
        return

    current_policy = asyncio.get_event_loop_policy()
    if isinstance(current_policy, windows_proactor_policy):
        return

    asyncio.set_event_loop_policy(windows_proactor_policy())


def launch_x_persistent_context(playwright: Any, user_data_dir: Path, headless: bool) -> Any:
    launch_kwargs = {
        "user_data_dir": str(user_data_dir),
        "headless": headless,
        "channel": X_BROWSER_CHANNEL,
        "viewport": {"width": 1440, "height": 960} if not headless else {"width": 1280, "height": 720},
    }

    try:
        return playwright.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as first_error:
        error_message = str(first_error)
        if X_BROWSER_CHANNEL in error_message.lower() and ("not found" in error_message.lower() or "channel" in error_message.lower()):
            raise RuntimeError(f"未检测到可用的 {X_BROWSER_NAME}。请先安装桌面版 {X_BROWSER_NAME} 后再重试 X 登录。") from first_error
        if not user_data_dir.exists() or not any(user_data_dir.iterdir()):
            raise

        broken_dir = user_data_dir.with_name(f"{user_data_dir.name}.broken-{int(time.time())}")
        try:
            if broken_dir.exists():
                shutil.rmtree(broken_dir, ignore_errors=True)
            shutil.move(str(user_data_dir), str(broken_dir))
            user_data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as move_error:
            raise RuntimeError(
                "本地 X 登录缓存已损坏，且自动重建失败。请关闭可能占用该目录的浏览器进程后重试。"
            ) from move_error

        try:
            return playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as second_error:
            raise RuntimeError(
                f"本地 X 登录缓存已损坏。程序已尝试重建浏览器目录，但 {X_BROWSER_NAME} 登录窗口仍未能启动。"
            ) from second_error


def login_x_and_save_session(timeout_seconds: float = 300.0) -> XSessionAuth:
    if sync_playwright is None:
        raise RuntimeError("当前环境未安装 playwright，无法打开 X 登录浏览器。")

    ensure_playwright_event_loop_policy()

    user_data_dir = get_x_browser_data_dir()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser_context = launch_x_persistent_context(playwright, user_data_dir=user_data_dir, headless=False)
        try:
            page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()
            page.goto(X_HOME_URL, wait_until="domcontentloaded", timeout=60000)
            auth = extract_x_session_auth_from_playwright_context(browser_context)
            if auth is not None:
                persist_x_session_auth(auth)
                return auth

            page.goto(X_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            auth = wait_for_x_session_auth(browser_context, timeout_seconds=timeout_seconds)
            persist_x_session_auth(auth)
            return auth
        finally:
            browser_context.close()


def collect_from_x_url(
    url: str,
    limit: int,
    timeout: float = 15.0,
    expand_reply_threads: bool = True,
    max_reply_depth: int = 1,
    auto_login: bool = True,
) -> list[CollectedComment]:
    root_comment_id = extract_status_id(url)
    if root_comment_id is None:
        raise RuntimeError("无法从 X/Twitter 链接中解析帖子 ID。")

    auth: XSessionAuth | None = None
    auth_error: Exception | None = None
    collected_from_authenticated_x: list[CollectedComment] | None = None

    try:
        auth = resolve_x_session_auth(auto_login=False)
    except Exception as error:
        auth_error = error

    if auth is not None:
        try:
            session = build_x_session(auth=auth, source_url=url)
            collected = collect_x_thread_comments(
                session=session,
                source_url=url,
                root_post_id=root_comment_id,
                focal_tweet_id=root_comment_id,
                parent_comment_id=None,
                thread_topic=None,
                is_reply=False,
                limit=limit,
                timeout=timeout,
                expand_reply_threads=expand_reply_threads,
                remaining_depth=max_reply_depth,
                visited_comment_ids={root_comment_id},
            )
            collected_from_authenticated_x = deduplicate_comments(collected)[:limit]
            if collected_from_authenticated_x:
                return collected_from_authenticated_x
        except Exception as error:
            auth_error = error

    public_comments = load_cached_public_x_comments(root_post_id=root_comment_id, limit=limit)
    if not public_comments:
        public_comments = collect_visible_nitter_replies(
            url=url,
            root_post_id=root_comment_id,
            limit=limit,
            timeout=timeout,
        )
    if public_comments:
        return public_comments

    if collected_from_authenticated_x is not None:
        return collected_from_authenticated_x

    if auth is None and auto_login:
        auth = resolve_x_session_auth(auto_login=True)
        session = build_x_session(auth=auth, source_url=url)
        collected = collect_x_thread_comments(
            session=session,
            source_url=url,
            root_post_id=root_comment_id,
            focal_tweet_id=root_comment_id,
            parent_comment_id=None,
            thread_topic=None,
            is_reply=False,
            limit=limit,
            timeout=timeout,
            expand_reply_threads=expand_reply_threads,
            remaining_depth=max_reply_depth,
            visited_comment_ids={root_comment_id},
        )
        return deduplicate_comments(collected)[:limit]

    if auth_error is not None:
        raise auth_error

    return []


def load_cached_public_x_comments(root_post_id: str, limit: int) -> list[CollectedComment]:
    snapshot_path = get_x_public_snapshot_path(root_post_id)
    if limit <= 0 or not snapshot_path.exists():
        return []

    comments: list[CollectedComment] = []
    try:
        for raw_line in snapshot_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue

            text = normalize_space(str(payload.get("text") or ""))
            if not text:
                continue

            comments.append(
                CollectedComment(
                    text=text,
                    source=normalize_space(str(payload.get("source") or "")) or f"https://x.com/i/web/status/{root_post_id}",
                    source_type=normalize_space(str(payload.get("source_type") or "x")) or "x",
                    author=normalize_space(str(payload.get("author") or "")) or None,
                    published_at=normalize_space(str(payload.get("published_at") or "")) or None,
                    like_count=parse_int(payload.get("like_count")),
                    comment_id=normalize_space(str(payload.get("comment_id") or "")) or None,
                    parent_id=normalize_space(str(payload.get("parent_id") or root_post_id)) or root_post_id,
                    is_reply=bool(payload.get("is_reply", False)),
                    thread_topic=normalize_space(str(payload.get("thread_topic") or "")) or None,
                    recommended=None,
                )
            )
            if len(comments) >= limit:
                break
    except Exception:
        return []

    return deduplicate_comments(comments)[:limit]


def collect_visible_nitter_replies(
    url: str,
    root_post_id: str,
    limit: int,
    timeout: float,
) -> list[CollectedComment]:
    if limit <= 0 or sync_playwright is None:
        return []

    try:
        ensure_playwright_event_loop_policy()
        with sync_playwright() as playwright:
            for headless in (True, False):
                comments = collect_visible_nitter_replies_with_browser(
                    playwright=playwright,
                    url=url,
                    root_post_id=root_post_id,
                    limit=limit,
                    timeout=timeout,
                    headless=headless,
                )
                if comments:
                    return comments
    except Exception:
        return []

    return []


def collect_visible_nitter_replies_with_browser(
    playwright: Any,
    url: str,
    root_post_id: str,
    limit: int,
    timeout: float,
    headless: bool,
) -> list[CollectedComment]:
    current_url = build_nitter_status_url(url)
    seen_urls: set[str] = set()
    seen_comments: set[str] = set()
    comments: list[CollectedComment] = []
    per_page_timeout_ms = max(15000, int(timeout * 1000))
    max_pages = max(2, min(60, (limit + 19) // 20 + 2))

    try:
        try:
            browser = playwright.chromium.launch(channel=X_BROWSER_CHANNEL, headless=headless)
        except Exception:
            browser = playwright.chromium.launch(headless=headless)

        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 2000},
        )
        page = context.new_page()

        try:
            for _ in range(max_pages):
                if not current_url or current_url in seen_urls or len(comments) >= limit:
                    break
                seen_urls.add(current_url)

                try:
                    page.goto(current_url, wait_until="domcontentloaded", timeout=per_page_timeout_ms)
                    page.wait_for_timeout(1200)
                    payload = page.evaluate(
                        """
                        () => ({
                          items: Array.from(document.querySelectorAll('.reply')).map((item) => {
                            const text = item.querySelector('.tweet-content')?.textContent?.replace(/\\s+/g, ' ').trim() || '';
                            const author = item.querySelector('.fullname')?.textContent?.replace(/\\s+/g, ' ').trim() || '';
                            const username = item.querySelector('.username')?.textContent?.replace(/\\s+/g, ' ').trim() || '';
                            const dateAnchor = item.querySelector('.tweet-date a');
                            const date = dateAnchor?.textContent?.replace(/\\s+/g, ' ').trim() || '';
                            const href = dateAnchor?.getAttribute('href') || '';
                            return { text, author, username, date, href };
                          }),
                          nextHref: (() => {
                            const next = Array.from(document.querySelectorAll('.show-more a')).find((node) => {
                              const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                              return /load more|show more/i.test(text);
                            });
                            return next?.getAttribute('href') || '';
                          })(),
                        })
                        """
                    )
                except Exception:
                    break

                if not isinstance(payload, dict):
                    break

                raw_items = payload.get("items")
                if isinstance(raw_items, list):
                    for raw_item in raw_items:
                        if not isinstance(raw_item, dict):
                            continue

                        text = normalize_space(str(raw_item.get("text") or ""))
                        if not text:
                            continue

                        author = normalize_space(str(raw_item.get("author") or "")) or None
                        username = normalize_space(str(raw_item.get("username") or "")) or None
                        published_at = normalize_space(str(raw_item.get("date") or "")) or None
                        href = normalize_space(str(raw_item.get("href") or ""))
                        comment_id = extract_status_id(href)
                        comment_key = comment_id or f"{author or ''}|{username or ''}|{published_at or ''}|{text}"
                        if comment_key in seen_comments:
                            continue

                        seen_comments.add(comment_key)
                        comments.append(
                            CollectedComment(
                                text=text,
                                source=url,
                                source_type="x",
                                author=author,
                                published_at=published_at,
                                like_count=None,
                                comment_id=comment_id,
                                parent_id=root_post_id,
                                is_reply=False,
                                thread_topic=None,
                            )
                        )
                        if len(comments) >= limit:
                            break

                if len(comments) >= limit:
                    break

                next_href = normalize_space(str(payload.get("nextHref") or ""))
                if not next_href:
                    break
                current_url = resolve_nitter_next_url(current_url, next_href)
        finally:
            context.close()
            browser.close()
    except Exception:
        return []

    return deduplicate_comments(comments)[:limit]


def build_nitter_status_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    path = parsed.path or ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{NITTER_BASE_URL}{path}"


def resolve_nitter_next_url(current_url: str, href: str) -> str:
    if href.startswith("?"):
        base = current_url.split("#", 1)[0].split("?", 1)[0]
        return f"{base}{href}"
    if href.startswith("/"):
        return f"{NITTER_BASE_URL}{href}"
    return urljoin(current_url, href)


def collect_x_thread_comments(
    session: requests.Session,
    source_url: str,
    root_post_id: str,
    focal_tweet_id: str,
    parent_comment_id: str | None,
    thread_topic: str | None,
    is_reply: bool,
    limit: int,
    timeout: float,
    expand_reply_threads: bool,
    remaining_depth: int,
    visited_comment_ids: set[str],
) -> list[CollectedComment]:
    if limit <= 0:
        return []

    direct_comments = collect_direct_x_comments(
        session=session,
        source_url=source_url,
        root_post_id=root_post_id,
        focal_tweet_id=focal_tweet_id,
        parent_comment_id=parent_comment_id,
        thread_topic=thread_topic,
        is_reply=is_reply,
        limit=limit,
        timeout=timeout,
        visited_comment_ids=visited_comment_ids,
    )

    ordered_comments: list[CollectedComment] = []
    for item in direct_comments:
        ordered_comments.append(item.comment)
        if len(ordered_comments) >= limit:
            return ordered_comments[:limit]

        if not expand_reply_threads or remaining_depth <= 0:
            continue
        if not item.reply_count or item.reply_count <= 0:
            continue

        child_limit = limit - len(ordered_comments)
        if child_limit <= 0:
            break

        child_comments = collect_x_thread_comments(
            session=session,
            source_url=source_url,
            root_post_id=root_post_id,
            focal_tweet_id=item.comment.comment_id or "",
            parent_comment_id=item.comment.comment_id,
            thread_topic=item.comment.text,
            is_reply=True,
            limit=child_limit,
            timeout=timeout,
            expand_reply_threads=expand_reply_threads,
            remaining_depth=remaining_depth - 1,
            visited_comment_ids=visited_comment_ids,
        )
        ordered_comments.extend(child_comments)
        if len(ordered_comments) >= limit:
            return ordered_comments[:limit]

    return ordered_comments[:limit]


def resolve_x_session_auth(auto_login: bool = False) -> XSessionAuth:
    cookie_header = normalize_space(os.environ.get("X_COOKIE_HEADER") or "")
    csrf_token = normalize_space(os.environ.get("X_CSRF_TOKEN") or "")

    if cookie_header:
        if not extract_cookie_value(cookie_header, "auth_token"):
            raise RuntimeError("X_COOKIE_HEADER 中缺少 auth_token，当前会话无法读取评论线程。")

        if not csrf_token:
            csrf_token = extract_cookie_value(cookie_header, "ct0") or ""
        if not csrf_token:
            raise RuntimeError(
                "未能从 X_COOKIE_HEADER 中解析 ct0。请额外设置环境变量 X_CSRF_TOKEN。"
            )

        auth = XSessionAuth(cookie_header=cookie_header, csrf_token=csrf_token)
        persist_x_session_auth(auth)
        return auth

    saved_auth = try_resolve_x_session_auth_from_saved_state()
    if saved_auth is not None:
        return saved_auth

    saved_auth = try_resolve_x_session_auth_from_saved_profile()
    if saved_auth is not None:
        return saved_auth

    auto_auth = try_resolve_x_session_auth_from_browser()
    if auto_auth is not None:
        persist_x_session_auth(auto_auth)
        return auto_auth

    if auto_login:
        return login_x_and_save_session(timeout_seconds=300.0)

    raise RuntimeError(
        "X 评论抓取需要已登录的 Web 会话。当前未检测到可用的项目内 X 登录缓存，"
        "也无法从本机 Edge/Chrome 自动读取 x.com 登录态。请先运行 python .\\fetcher-x.py --login "
        "完成一次登录，或手动提供 X_COOKIE_HEADER。"
    )


def try_resolve_x_session_auth_from_saved_profile() -> XSessionAuth | None:
    if sync_playwright is None or not has_x_saved_login_state():
        return None

    try:
        ensure_playwright_event_loop_policy()
        with sync_playwright() as playwright:
            browser_context = launch_x_persistent_context(
                playwright,
                user_data_dir=get_x_browser_data_dir(),
                headless=True,
            )
            try:
                return extract_x_session_auth_from_playwright_context(browser_context)
            finally:
                browser_context.close()
    except Exception:
        return None


def try_resolve_x_session_auth_from_saved_state() -> XSessionAuth | None:
    storage_state_path = get_x_storage_state_path()
    if not storage_state_path.exists():
        return None

    try:
        payload = json.loads(storage_state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        return None

    cookie_header = build_cookie_header_from_playwright_cookies(cookies)
    if not cookie_header:
        return None

    auth_token = extract_cookie_value(cookie_header, "auth_token")
    csrf_token = extract_cookie_value(cookie_header, "ct0")
    if not auth_token or not csrf_token:
        return None

    return XSessionAuth(cookie_header=cookie_header, csrf_token=csrf_token)


def try_resolve_x_session_auth_from_browser() -> XSessionAuth | None:
    if browser_cookie3 is None:
        return None

    loaders: list[tuple[str, Any]] = []
    for name in ("edge", "chrome"):
        loader = getattr(browser_cookie3, name, None)
        if callable(loader):
            loaders.append((name, loader))

    for _, loader in loaders:
        for domain_name in X_COOKIE_DOMAINS:
            try:
                cookie_jar = loader(domain_name=domain_name)
            except Exception:
                continue

            cookie_header = build_cookie_header_from_jar(cookie_jar)
            if not cookie_header:
                continue

            auth_token = extract_cookie_value(cookie_header, "auth_token")
            csrf_token = extract_cookie_value(cookie_header, "ct0")
            if auth_token and csrf_token:
                return XSessionAuth(cookie_header=cookie_header, csrf_token=csrf_token)

    return None


def persist_x_session_auth(auth: XSessionAuth) -> None:
    persist_x_session_auth_to_storage_state(auth)

    if sync_playwright is None:
        return

    ensure_playwright_event_loop_policy()

    cookies = build_playwright_cookies_from_header(auth.cookie_header)
    if not cookies:
        return

    user_data_dir = get_x_browser_data_dir()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser_context = launch_x_persistent_context(playwright, user_data_dir=user_data_dir, headless=True)
            try:
                browser_context.add_cookies(cookies)
            finally:
                browser_context.close()
    except Exception:
        return


def persist_x_session_auth_to_storage_state(auth: XSessionAuth) -> None:
    cookies = build_storage_state_cookies_from_header(auth.cookie_header)
    if not cookies:
        return

    browser_data_root = get_x_browser_data_root()
    browser_data_root.mkdir(parents=True, exist_ok=True)
    storage_state_path = get_x_storage_state_path()
    payload = {
        "cookies": cookies,
        "origins": [],
    }
    storage_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_for_x_session_auth(browser_context: Any, timeout_seconds: float) -> XSessionAuth:
    deadline = time.monotonic() + max(timeout_seconds, 30.0)
    while time.monotonic() < deadline:
        auth = extract_x_session_auth_from_playwright_context(browser_context)
        if auth is not None:
            return auth
        time.sleep(2.0)

    raise RuntimeError("等待 X 登录超时。请在弹出的浏览器中完成登录后重试。")


def extract_x_session_auth_from_playwright_context(browser_context: Any) -> XSessionAuth | None:
    try:
        cookies = browser_context.cookies([X_HOME_URL, "https://twitter.com"])
    except Exception:
        return None

    cookie_header = build_cookie_header_from_playwright_cookies(cookies)
    if not cookie_header:
        return None

    auth_token = extract_cookie_value(cookie_header, "auth_token")
    csrf_token = extract_cookie_value(cookie_header, "ct0")
    if not auth_token or not csrf_token:
        return None

    return XSessionAuth(cookie_header=cookie_header, csrf_token=csrf_token)


def build_x_session(auth: XSessionAuth, source_url: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "accept": "*/*",
            "authorization": PUBLIC_BEARER_TOKEN,
            "content-type": "application/json",
            "cookie": auth.cookie_header,
            "referer": source_url,
            "user-agent": USER_AGENT,
            "x-csrf-token": auth.csrf_token,
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
        }
    )
    return session


def collect_direct_x_comments(
    session: requests.Session,
    source_url: str,
    root_post_id: str,
    focal_tweet_id: str,
    parent_comment_id: str | None,
    thread_topic: str | None,
    is_reply: bool,
    limit: int,
    timeout: float,
    visited_comment_ids: set[str],
) -> list[XCollectedThreadComment]:
    collected: list[XCollectedThreadComment] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while len(collected) < limit:
        payload = fetch_tweet_detail(
            session=session,
            focal_tweet_id=focal_tweet_id,
            source_url=source_url,
            timeout=timeout,
            cursor=cursor,
        )
        before_count = len(collected)

        for tweet_result in iter_tweet_results_from_payload(payload):
            item = build_x_thread_comment(
                tweet_result=tweet_result,
                source_url=source_url,
                focal_tweet_id=focal_tweet_id,
                parent_comment_id=parent_comment_id,
                thread_topic=thread_topic,
                is_reply=is_reply,
            )
            if item is None:
                continue
            comment_id = item.comment.comment_id
            if not comment_id or comment_id in visited_comment_ids:
                continue
            visited_comment_ids.add(comment_id)
            collected.append(item)
            if len(collected) >= limit:
                return collected[:limit]

        next_cursor = extract_bottom_cursor(payload)
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            break
        if len(collected) == before_count:
            break

        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return collected[:limit]


def fetch_tweet_detail(
    session: requests.Session,
    focal_tweet_id: str,
    source_url: str,
    timeout: float,
    cursor: str | None = None,
) -> dict[str, Any]:
    response = session.get(build_tweet_detail_url(focal_tweet_id=focal_tweet_id, cursor=cursor), timeout=timeout)
    if response.status_code in {401, 403, 404}:
        raise RuntimeError(
            "X 评论接口拒绝了当前会话。请确认 X_COOKIE_HEADER / X_CSRF_TOKEN 来自已登录的 X Web 会话，"
            "并在本地终端重新设置后再运行。"
        )
    response.raise_for_status()
    payload = response.json()
    conversation = payload.get("data", {}).get("threaded_conversation_with_injections_v2")
    if not isinstance(conversation, dict):
        raise RuntimeError("X 评论接口返回中缺少 threaded_conversation_with_injections_v2。")
    session.headers["referer"] = source_url
    return conversation


def build_tweet_detail_url(focal_tweet_id: str, cursor: str | None = None) -> str:
    variables: dict[str, Any] = {
        "focalTweetId": focal_tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
    }
    if cursor:
        variables["cursor"] = cursor

    params = urlencode(
        {
            "variables": json.dumps(variables, ensure_ascii=False, separators=(",", ":")),
            "features": json.dumps(TWEET_DETAIL_FEATURES, ensure_ascii=False, separators=(",", ":")),
            "fieldToggles": json.dumps(TWEET_DETAIL_FIELD_TOGGLES, ensure_ascii=False, separators=(",", ":")),
        }
    )
    return f"https://x.com/i/api/graphql/{TWEET_DETAIL_QUERY_ID}/TweetDetail?{params}"


def iter_tweet_results_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in flatten_timeline_entries(payload):
        content = entry.get("content", {})
        typename = content.get("__typename")
        if typename == "TimelineTimelineModule":
            for item in content.get("items") or []:
                result = unwrap_tweet_result(item.get("item", {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
                if result is not None:
                    results.append(result)
            continue
        result = unwrap_tweet_result(content.get("itemContent", {}).get("tweet_results", {}).get("result"))
        if result is not None:
            results.append(result)
    return results


def flatten_timeline_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    instructions = payload.get("instructions") or []
    entries: list[dict[str, Any]] = []
    for instruction in instructions:
        entries.extend(instruction.get("entries") or [])
    return entries


def unwrap_tweet_result(result: Any) -> dict[str, Any] | None:
    current = result if isinstance(result, dict) else None
    while isinstance(current, dict):
        typename = current.get("__typename")
        if typename == "Tweet":
            return current
        if typename == "TweetWithVisibilityResults":
            current = current.get("tweet")
            continue
        if typename == "TweetTombstone":
            return None
        if "tweet" in current and isinstance(current.get("tweet"), dict):
            current = current.get("tweet")
            continue
        return current if current.get("legacy") else None
    return None


def build_x_thread_comment(
    tweet_result: dict[str, Any],
    source_url: str,
    focal_tweet_id: str,
    parent_comment_id: str | None,
    thread_topic: str | None,
    is_reply: bool,
) -> XCollectedThreadComment | None:
    legacy = tweet_result.get("legacy") or {}
    in_reply_to = str(legacy.get("in_reply_to_status_id_str") or "")
    if in_reply_to != focal_tweet_id:
        return None

    comment_id = str(tweet_result.get("rest_id") or legacy.get("id_str") or "")
    if not comment_id or comment_id == focal_tweet_id:
        return None

    text = extract_tweet_text(tweet_result, legacy)
    if not text:
        return None

    user_result = unwrap_user_result(tweet_result.get("core", {}).get("user_results", {}).get("result"))
    user_legacy = user_result.get("legacy") if user_result else {}
    user_core = user_result.get("core") if user_result else {}
    screen_name = normalize_space(str((user_core or {}).get("screen_name") or user_legacy.get("screen_name") or ""))
    user_name = normalize_space(str((user_core or {}).get("name") or user_legacy.get("name") or ""))
    if user_name and screen_name:
        author = f"{user_name} (@{screen_name})"
    elif user_name:
        author = user_name
    elif screen_name:
        author = f"@{screen_name}"
    else:
        author = None

    like_count = parse_int(legacy.get("favorite_count"))
    reply_count = parse_int(legacy.get("reply_count"))
    published_at = normalize_space(str(legacy.get("created_at") or "")) or None

    comment = CollectedComment(
        text=text,
        source=source_url,
        source_type="x",
        author=author,
        published_at=published_at,
        like_count=like_count,
        comment_id=comment_id,
        parent_id=parent_comment_id,
        is_reply=is_reply,
        thread_topic=thread_topic,
    )
    return XCollectedThreadComment(comment=comment, reply_count=reply_count)


def unwrap_user_result(result: Any) -> dict[str, Any] | None:
    current = result if isinstance(result, dict) else None
    while isinstance(current, dict):
        typename = current.get("__typename")
        if typename == "User":
            return current
        if typename == "UserUnavailable":
            return None
        if "user" in current and isinstance(current.get("user"), dict):
            current = current.get("user")
            continue
        return current if current.get("legacy") else None
    return None


def extract_tweet_text(tweet_result: dict[str, Any], legacy: dict[str, Any]) -> str:
    note_text = (
        tweet_result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
        .get("text")
    )
    if note_text:
        return normalize_space(str(note_text))
    full_text = legacy.get("full_text") or ""
    return normalize_space(str(full_text))


def extract_bottom_cursor(payload: dict[str, Any]) -> str | None:
    for entry in flatten_timeline_entries(payload):
        content = entry.get("content", {})
        if content.get("__typename") != "TimelineTimelineCursor":
            continue
        if content.get("cursorType") != "Bottom":
            continue
        value = normalize_space(str(content.get("value") or ""))
        if value:
            return value
    return None


def extract_cookie_value(cookie_header: str, name: str) -> str | None:
    for match in COOKIE_VALUE_PATTERN.finditer(cookie_header):
        if match.group(1) == name:
            return match.group(2)
    return None


def build_cookie_header_from_jar(cookie_jar: Any) -> str:
    pairs: list[str] = []
    seen_names: set[str] = set()
    for cookie in cookie_jar:
        name = normalize_space(str(getattr(cookie, "name", "") or ""))
        value = str(getattr(cookie, "value", "") or "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def build_cookie_header_from_playwright_cookies(cookies: list[dict[str, Any]]) -> str:
    pairs: list[str] = []
    seen_names: set[str] = set()
    for cookie in cookies:
        name = normalize_space(str(cookie.get("name") or ""))
        value = str(cookie.get("value") or "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def build_storage_state_cookies_from_header(cookie_header: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    http_only_names = {"auth_token", "kdt", "twid", "att"}

    for match in COOKIE_VALUE_PATTERN.finditer(cookie_header):
        name = normalize_space(match.group(1))
        value = match.group(2)
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".x.com",
                "path": "/",
                "httpOnly": name in http_only_names,
                "secure": True,
                "sameSite": "Lax",
            }
        )

    return cookies


def build_playwright_cookies_from_header(cookie_header: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for match in COOKIE_VALUE_PATTERN.finditer(cookie_header):
        name = match.group(1)
        value = match.group(2)
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        cookies.append(
            {
                "name": name,
                "value": value,
                "url": X_HOME_URL,
            }
        )
    return cookies


def parse_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def extract_status_id(url: str) -> str | None:
    path = urlparse(url).path
    match = STATUS_ID_PATTERN.search(path)
    return match.group(1) if match else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="X/Twitter 评论抓取器（优先登录态 API，失败时回退 Nitter 公共可见回复）")
    parser.add_argument("--source", help="X/Twitter 帖子链接")
    parser.add_argument("--limit", type=int, default=50, help="最大抓取评论数（含楼中楼）")
    parser.add_argument("--output", type=Path, default=Path("x_comments.jsonl"), help="输出 JSONL 路径")
    parser.add_argument("--text-output", type=Path, help="额外输出纯文本内容")
    parser.add_argument("--timeout", type=float, default=15.0, help="接口请求超时时间，单位秒")
    parser.add_argument("--max-reply-depth", type=int, default=1, help="递归展开楼中楼深度，默认 1")
    parser.add_argument("--no-expand-replies", action="store_true", help="只抓顶层评论，不递归展开楼中楼")
    parser.add_argument("--login", action="store_true", help="打开浏览器登录 X 并保存会话到本地 browser_data")
    parser.add_argument("--login-timeout", type=float, default=300.0, help="等待手动登录超时时间，单位秒")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.login:
        auth = login_x_and_save_session(timeout_seconds=args.login_timeout)
        summary = {
            "login_saved": True,
            "has_auth_token": bool(extract_cookie_value(auth.cookie_header, "auth_token")),
            "has_ct0": bool(auth.csrf_token),
            "browser_data_dir": str(get_x_browser_data_dir()),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if not args.source:
        raise RuntimeError("未提供 X/Twitter 帖子链接。请传入 --source，或使用 --login 保存登录态。")

    comments = collect_from_x_url(
        url=args.source,
        limit=args.limit,
        timeout=args.timeout,
        expand_reply_threads=not args.no_expand_replies,
        max_reply_depth=max(0, args.max_reply_depth),
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
            print(f"- 作者: {item.author or '未知'}")
            print(f"  时间: {item.published_at or '未知'}")
            print(f"  回复标记: {'是' if item.is_reply else '否'}")
            print(f"  内容: {item.text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())