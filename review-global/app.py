from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from matplotlib import pyplot as plt
from wordcloud import WordCloud

from comment_analyzer import (
    AnalysisResult,
    analyze_comments,
)
from comment_cleaner import CleanerConfig, clean_comments, contains_cjk, normalize_text, tokenize
from comment_fetcher import collect_from_source
from fetcher_x import has_x_saved_login_state, login_x_and_save_session

try:
    from deep_translator import GoogleTranslator  # type: ignore
except ImportError:
    GoogleTranslator = None


st.set_page_config(page_title="评论抓取与分析台", layout="wide")

TRANSLATION_LANGUAGES = {
    "中文（简体）": "zh-CN",
    "English": "en",
    "日本語": "ja",
    "Español": "es",
    "Français": "fr",
    "Deutsch": "de",
}

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r"\s+")
PUNCT_PATTERN = re.compile(r"[^\w\u4e00-\u9fff<>!? ]+", re.UNICODE)
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.x.com", "mobile.twitter.com"}


@contextmanager
def temporary_env_vars(updates: dict[str, str | None]):
    previous_values: dict[str, str | None] = {}
    try:
        for key, value in updates.items():
            previous_values[key] = os.environ.get(key)
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        yield
    finally:
        for key, previous in previous_values.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def is_x_source(source: str) -> bool:
    parsed = urlparse(source.strip())
    host = parsed.netloc.lower().split(":")[0]
    return bool(host) and (host in X_HOSTS or host.endswith("x.com") or host.endswith("twitter.com"))


def get_wordcloud_font_path() -> str | None:
    candidates = [
        r"C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei
        r"C:/Windows/Fonts/simhei.ttf",    # SimHei
        r"C:/Windows/Fonts/simsun.ttc",    # SimSun
        r"C:/Windows/Fonts/NotoSansCJK-Regular.ttc",
    ]
    for font_path in candidates:
        if Path(font_path).exists():
            return font_path
    return None


def build_wordcloud(term_counts: list[tuple[str, int]]) -> plt.Figure | None:
    if not term_counts:
        return None

    cloud_kwargs: dict[str, Any] = {
        "width": 1200,
        "height": 600,
        "background_color": "white",
        "colormap": "viridis",
        "max_words": 120,
        "collocations": False,
    }
    font_path = get_wordcloud_font_path()
    if font_path:
        cloud_kwargs["font_path"] = font_path

    cloud = WordCloud(
        **cloud_kwargs,
    ).generate_from_frequencies(dict(term_counts))

    fig, axis = plt.subplots(figsize=(12, 6))
    axis.imshow(cloud, interpolation="bilinear")
    axis.axis("off")
    return fig


def build_translated_top_terms(
    top_terms: list[tuple[str, float]],
    target_lang: str,
) -> list[tuple[str, float]]:
    if not top_terms:
        return []

    source_terms = tuple(term for term, _ in top_terms)
    translated_terms = translate_texts(source_terms, target_lang)

    score_by_term: dict[str, float] = {}
    for (original_term, score), translated_term in zip(top_terms, translated_terms):
        normalized = translated_term.strip() if isinstance(translated_term, str) else ""
        label = normalized or original_term
        score_by_term[label] = score_by_term.get(label, 0.0) + float(score)

    return sorted(score_by_term.items(), key=lambda item: item[1], reverse=True)


def render_summary_cards(result: AnalysisResult, clean_report: dict[str, object]) -> None:
    summary = result.summary
    clean_summary = clean_report.get("summary", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("输入评论", int(summary.get("input_count", 0)))
    col2.metric("保留评论", int(summary.get("kept_count", 0)))
    col3.metric("过滤评论", int(summary.get("removed_count", 0)))
    col4.metric("平均长度", float(summary.get("avg_length", 0)))

    st.caption(f"过滤原因统计: {clean_summary.get('removal_reasons', {})}")


def render_fetch_quality_stats(fetched: list[Any], comparison_df: pd.DataFrame) -> None:
    text_series = comparison_df["raw_comment"].astype(str) if not comparison_df.empty else pd.Series(dtype=str)
    duplicate_text_count = int(text_series.duplicated().sum()) if not text_series.empty else 0
    reply_count = sum(1 for item in fetched if bool(getattr(item, "is_reply", False)))
    top_level_count = max(len(fetched) - reply_count, 0)

    st.caption(
        f"抓取结构统计: 顶层评论 {top_level_count} 条，跟评 {reply_count} 条，完全相同文本重复 {duplicate_text_count} 条。"
    )


def render_charts(
    result: AnalysisResult,
    display_terms: list[tuple[str, float]] | None = None,
    term_title_suffix: str = "",
) -> None:
    terms_for_chart = display_terms if display_terms is not None else result.top_terms
    top_terms_df = pd.DataFrame(terms_for_chart[:20], columns=["term", "score"])
    sentiment_order = ["positive", "neutral", "negative", "skeptical"]
    sentiment_df = (
        pd.Series(result.sentiment_distribution, name="count")
        .reindex(sentiment_order, fill_value=0)
        .rename_axis("sentiment")
        .reset_index()
    )
    tone_order = ["assertive", "skeptical", "neutral"]
    tone_df = (
        pd.Series(result.tone_distribution, name="count")
        .reindex(tone_order, fill_value=0)
        .rename_axis("tone")
        .reset_index()
    )

    left, right = st.columns([1.2, 1.0])

    with left:
        st.subheader(f"关键词词云{term_title_suffix}")
        cloud_fig = build_wordcloud(terms_for_chart)
        if cloud_fig is not None:
            st.pyplot(cloud_fig, use_container_width=True)
        else:
            st.info("没有足够的关键词可用于词云。")

        st.subheader(f"关键词 Top 20（TF-IDF）{term_title_suffix}")
        if not top_terms_df.empty:
            st.bar_chart(top_terms_df.set_index("term"))
        else:
            st.info("没有可展示的高频词。")

    with right:
        st.subheader("情绪分布（含 skeptical）")
        if not sentiment_df.empty:
            st.bar_chart(sentiment_df.set_index("sentiment"))
        else:
            st.info("没有可展示的情绪分布。")

        st.subheader("语气分布")
        if not tone_df.empty:
            st.bar_chart(tone_df.set_index("tone"))
        else:
            st.info("没有可展示的语气分布。")


def render_comment_level_insights(comparison_df: pd.DataFrame) -> None:
    st.subheader("基于评论汇总的结论")
    if comparison_df.empty:
        st.info("暂无可汇总的评论数据。")
        return

    total = len(comparison_df)
    kept = int((comparison_df["status"] == "kept").sum())
    removed = total - kept
    reply_count = int(comparison_df["is_reply"].fillna(False).astype(bool).sum()) if "is_reply" in comparison_df else 0
    skeptical_count = int((comparison_df.get("tone_label", pd.Series(dtype=str)) == "skeptical").sum())

    removed_reasons = comparison_df.loc[comparison_df["status"] == "removed", "reason"]
    top_reason = removed_reasons.value_counts().index[0] if not removed_reasons.empty else "无"

    bullets = [
        f"保留 {kept}/{total} 条评论，过滤 {removed} 条。",
        f"跟评数量 {reply_count} 条，占比约 {reply_count / total:.1%}。",
        f"怀疑/反问语气评论 {skeptical_count} 条，占比约 {skeptical_count / total:.1%}。",
        f"最主要的过滤原因：{top_reason}。",
    ]

    for line in bullets:
        st.write(f"- {line}")


def summarize_common_conclusions(
    raw_comments: list[str],
    fetched: list[Any],
    analysis: AnalysisResult,
    comparison_df: pd.DataFrame,
) -> list[str]:
    conclusions: list[str] = []
    if not raw_comments:
        return conclusions

    conclusions.append(summarize_publication_distribution(fetched))
    conclusions.append(summarize_language_distribution(raw_comments))
    conclusions.append(summarize_topic_overlap(raw_comments, analysis))

    rating_line = summarize_rating_signal(fetched)
    if rating_line:
        conclusions.append(rating_line)

    return conclusions


def normalize_publication_label(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    text = re.sub(r"^(posted|发布于)\s*[:：]?\s*", "", text, flags=re.IGNORECASE).strip()
    lowered = text.lower()

    if "today" in lowered or "今天" in text:
        return "今天"
    if "yesterday" in lowered or "昨天" in text:
        return "昨天"
    if re.search(r"\b(hour|hours|hr|hrs|minute|minutes|min|mins)\b", lowered):
        return "今天"

    day_ago_match = re.search(r"(\d+)\s+days?\s+ago", lowered)
    if day_ago_match:
        return f"{day_ago_match.group(1)}天前"

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.month}月{parsed.day}日"
        except ValueError:
            continue

    zh_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if zh_match:
        return f"{int(zh_match.group(1))}月{int(zh_match.group(2))}日"

    return text


def build_raw_language_distribution(raw_comments: list[str]) -> dict[str, int]:
    counts = Counter({"zh": 0, "en": 0, "mixed_or_other": 0})
    for text in raw_comments:
        has_cjk = any(contains_cjk(ch) for ch in text)
        has_alpha = any(ch.isalpha() and not contains_cjk(ch) for ch in text)
        if has_cjk and has_alpha:
            counts["mixed_or_other"] += 1
        elif has_cjk:
            counts["zh"] += 1
        elif has_alpha:
            counts["en"] += 1
        else:
            counts["mixed_or_other"] += 1
    return dict(counts)


def summarize_publication_distribution(fetched: list[Any]) -> str:
    labels = [normalize_publication_label(getattr(item, "published_at", None)) for item in fetched]
    counts = Counter(label for label in labels if label)
    total = sum(counts.values())
    if total == 0:
        return "评论分布：当前样本缺少稳定的发布时间信息，暂时无法判断是否存在集中爆发。"

    top_dates = counts.most_common(2)
    distribution_text = "，".join(f"{label} {count}条" for label, count in top_dates)
    top_ratio = top_dates[0][1] / total
    top_two_ratio = sum(count for _, count in top_dates) / total

    if top_ratio >= 0.5 or top_two_ratio >= 0.7:
        judgment = "发布时间明显集中在少数时间点，说明更像一次事件驱动的集中反馈，存在短时舆情放大的风险。"
    elif top_ratio >= 0.3:
        judgment = "发布时间有一定集中，但还不是单点爆发式分布。"
    else:
        judgment = "发布时间相对分散，暂时看不出明显的单次爆发。"

    return f"评论分布：可识别发布时间的 {total} 条样本里，{distribution_text}。{judgment}"


def summarize_language_distribution(raw_comments: list[str]) -> str:
    counts = build_raw_language_distribution(raw_comments)
    total = max(sum(counts.values()), 1)
    zh_ratio = counts.get("zh", 0) / total
    en_ratio = counts.get("en", 0) / total
    mixed_ratio = counts.get("mixed_or_other", 0) / total

    summary = (
        f"原始评论语种：英文 {counts.get('en', 0)} 条（{en_ratio:.1%}），"
        f"中文 {counts.get('zh', 0)} 条（{zh_ratio:.1%}），"
        f"混合/其他 {counts.get('mixed_or_other', 0)} 条（{mixed_ratio:.1%}）。"
    )

    if en_ratio >= 0.6:
        return summary + " 这说明讨论主体主要来自英文区，更像英语社区中的共性问题。"
    if zh_ratio >= 0.6:
        return summary + " 这说明讨论主体主要来自中文区，更像中文社区中的集中反馈。"
    if en_ratio >= 0.2 and zh_ratio >= 0.2:
        return summary + " 这不是单一语种社区的孤立问题，而是跨语种都在重复出现的共性争议。"
    return summary + " 当前语种分布较混合，需要结合来源平台再判断问题主要在哪个社区发酵。"


def summarize_topic_overlap(raw_comments: list[str], analysis: AnalysisResult) -> str:
    theme_keywords: dict[str, set[str]] = {
        "AI/原创与创作争议": {"ai", "gen ai", "ai slop", "artist", "artists", "art", "contest", "plagiarism", "原创", "美术", "生成", "ai创作", "比赛"},
        "价格与价值": {"price", "pricing", "expensive", "cheap", "cost", "worth", "贵", "便宜", "价格", "值", "涨价"},
        "更新/内容与运营": {"update", "updates", "content", "campaign", "event", "运营", "更新", "内容", "活动", "维护", "宣传"},
        "体验/稳定性与玩法": {"bug", "broken", "crash", "lag", "physics", "controls", "gameplay", "体验", "稳定", "卡", "崩", "玩法", "手感", "修复"},
        "社区/尊重与品牌信任": {"community", "respect", "disrespect", "support", "reputation", "trust", "company", "社区", "尊重", "口碑", "信任", "公司", "忽视"},
    }

    counts = Counter({theme: 0 for theme in theme_keywords})
    for text in raw_comments:
        lowered = normalize_text(text).lower()
        for theme, keywords in theme_keywords.items():
            if any(keyword in lowered for keyword in keywords):
                counts[theme] += 1

    matched = [(theme, count) for theme, count in counts.most_common() if count > 0]
    if not matched:
        fallback_terms = "、".join(term for term, _ in analysis.top_terms[:5]) if analysis.top_terms else "暂无明显中心词"
        return f"讨论中心重合度：当前样本未能稳定归入预设主题，讨论相对分散；从词项上看，主要围绕 {fallback_terms} 展开。"

    total = max(len(raw_comments), 1)
    top_theme, top_count = matched[0]
    top_ratio = top_count / total
    top_two_ratio = sum(count for _, count in matched[:2]) / total
    focus_text = "，".join(f"{theme} {count / total:.1%}" for theme, count in matched[:3])

    if top_ratio >= 0.5:
        judgment = f"讨论高度重合，约 {top_ratio:.1%} 的样本都集中在“{top_theme}”这一核心问题上。"
    elif top_two_ratio >= 0.6:
        judgment = f"讨论主要集中在少数几个问题上，其中“{top_theme}”最突出，其次还有第二焦点。"
    else:
        judgment = "讨论中心存在，但还没有收敛到单一议题。"

    return f"讨论中心重合度：{judgment} 当前最主要的讨论方向依次是 {focus_text}。这说明样本并不是零散抱怨，而是在重复表达少数几个关键问题。"


def summarize_rating_signal(fetched: list[Any]) -> str | None:
    ratings = [1.0 if getattr(item, "recommended", None) else 0.0 for item in fetched if getattr(item, "recommended", None) is not None]
    if not ratings:
        return None

    average_score = sum(ratings) / len(ratings)
    recommended_count = sum(1 for score in ratings if score >= 1.0)
    not_recommended_count = len(ratings) - recommended_count
    score_100 = average_score * 100

    if average_score >= 0.6:
        tone = "整体偏正面。"
    elif average_score <= 0.4:
        tone = "整体偏负面。"
    else:
        tone = "整体呈现分化。"

    return (
        f"评分机制：当前样本可按显式推荐/不推荐折算为二元评分，平均得分约 {score_100:.1f}/100；"
        f"其中推荐 {recommended_count} 条，不推荐 {not_recommended_count} 条，{tone}"
    )


def render_raw_comment_common_conclusions(
    raw_comments: list[str],
    fetched: list[Any],
    analysis: AnalysisResult,
    comparison_df: pd.DataFrame,
) -> None:
    st.subheader("原始评论总结")
    with st.container(border=True):
        lines = summarize_common_conclusions(
            raw_comments=raw_comments,
            fetched=fetched,
            analysis=analysis,
            comparison_df=comparison_df,
        )
        if not lines:
            st.info("暂无可汇总的原始评论结论。")
            return
        for line in lines:
            st.write(line)


def render_conclusions(result: AnalysisResult) -> None:
    st.subheader("自动结论")
    for line in result.conclusions:
        st.write(f"- {line}")


def build_comparison_dataframe(
    raw_comments: list[str],
    clean_report: dict[str, object],
    fetched_items: list[Any] | None = None,
) -> pd.DataFrame:
    decisions: dict[int, dict[str, object]] = {}
    for item in clean_report.get("kept_comments", []):
        if isinstance(item, dict):
            decisions[int(item.get("original_index", 0))] = item
    for item in clean_report.get("removed_comments", []):
        if isinstance(item, dict):
            decisions[int(item.get("original_index", 0))] = item

    rows: list[dict[str, object]] = []
    for index, text in enumerate(raw_comments, start=1):
        decision = decisions.get(index, {})
        fetched_item = fetched_items[index - 1] if fetched_items is not None and index - 1 < len(fetched_items) else None
        kept = bool(decision.get("kept", False))
        normalized = str(decision.get("normalized_text", "")) or normalize_text(text)
        changed, notes = explain_normalization(text, normalized)
        tone_metrics = get_tone_metrics(text)
        rows.append(
            {
                "original_index": index,
                "raw_comment": text,
                "author": getattr(fetched_item, "author", None),
                "is_reply": bool(getattr(fetched_item, "is_reply", False)),
                "parent_id": getattr(fetched_item, "parent_id", None),
                "comment_id": getattr(fetched_item, "comment_id", None),
                "thread_topic": getattr(fetched_item, "thread_topic", None),
                "status": "kept" if kept else "removed",
                "reason": decision.get("reason", "unknown"),
                "normalized_text": normalized,
                "normalization_changed": changed,
                "normalization_notes": notes,
                "question_score": tone_metrics["question_score"],
                "exclaim_score": tone_metrics["exclaim_score"],
                "tone_label": tone_metrics["tone_label"],
                "information_score": decision.get("information_score", None),
                "similar_to": decision.get("similar_to", None),
                "similarity": decision.get("similarity", None),
            }
        )

    return pd.DataFrame(rows)


def build_contextual_comments_for_analysis(
    fetched_items: list[Any],
    clean_report: dict[str, object],
) -> list[str]:
    kept_indices = [
        int(item.get("original_index", 0))
        for item in clean_report.get("kept_comments", [])
        if isinstance(item, dict)
    ]

    contextual_comments: list[str] = []
    for index in kept_indices:
        if index <= 0 or index > len(fetched_items):
            continue
        item = fetched_items[index - 1]
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        if bool(getattr(item, "is_reply", False)):
            topic = str(getattr(item, "thread_topic", "") or "").strip()
            if topic:
                contextual_comments.append(f"{topic} {text}")
                continue
        contextual_comments.append(text)
    return contextual_comments


def add_translation_columns_to_comparison_df(
    comparison_df: pd.DataFrame,
    target_lang: str,
) -> pd.DataFrame:
    if comparison_df.empty:
        return comparison_df

    translated_df = comparison_df.copy()
    slice_count = len(translated_df)
    if slice_count <= 0:
        return translated_df

    raw_slice = tuple(str(text) for text in translated_df.loc[: slice_count - 1, "raw_comment"].tolist())
    norm_slice = tuple(str(text) for text in translated_df.loc[: slice_count - 1, "normalized_text"].tolist())

    raw_translated = translate_texts(raw_slice, target_lang)
    norm_translated = translate_texts(norm_slice, target_lang)

    translated_df["raw_comment_translated"] = ""
    translated_df["normalized_text_translated"] = ""

    translated_df.loc[: slice_count - 1, "raw_comment_translated"] = raw_translated
    translated_df.loc[: slice_count - 1, "normalized_text_translated"] = norm_translated
    return translated_df


def explain_normalization(raw_text: str, normalized_text: str) -> tuple[bool, str]:
    notes: list[str] = []

    if unicodedata.normalize("NFKC", raw_text) != raw_text:
        notes.append("NFKC字符归一化")
    if URL_PATTERN.search(raw_text):
        notes.append("URL替换为url")
    if raw_text.lower() != raw_text:
        notes.append("英文转小写")
    if PUNCT_PATTERN.sub(" ", raw_text) != raw_text:
        notes.append("标点/符号清理（保留?和!）")
    if "?" in raw_text or "!" in raw_text:
        notes.append("保留语气标记(?/!)")
    if WHITESPACE_PATTERN.sub(" ", raw_text).strip() != raw_text.strip():
        notes.append("空白压缩")

    changed = raw_text.strip() != normalized_text.strip()
    if not notes:
        notes.append("无明显正则化变化")
    return changed, "；".join(notes)


def get_tone_metrics(text: str) -> dict[str, object]:
    tokens = tokenize(normalize_text(text))
    question_score = 0
    exclaim_score = 0
    for token in tokens:
        if token == "<question>":
            question_score += 1
        elif token == "<strong_question>":
            question_score += 2
        elif token == "<exclaim>":
            exclaim_score += 1
        elif token == "<strong_exclaim>":
            exclaim_score += 2

    if question_score > 0:
        tone_label = "skeptical"
    elif exclaim_score > 0:
        tone_label = "assertive"
    else:
        tone_label = "neutral"

    return {
        "question_score": question_score,
        "exclaim_score": exclaim_score,
        "tone_label": tone_label,
    }


@st.cache_data(show_spinner=False)
def translate_texts(texts: tuple[str, ...], target_lang: str) -> list[str]:
    if not texts:
        return []
    if GoogleTranslator is None:
        raise RuntimeError("未安装 deep-translator，请先安装后再使用翻译功能。")

    translator = GoogleTranslator(source="auto", target=target_lang)
    translated: list[str] = []
    chunk_size = 30
    for start in range(0, len(texts), chunk_size):
        chunk = list(texts[start : start + chunk_size])
        try:
            translated_chunk = translator.translate_batch(chunk)
            translated.extend(preserve_terminal_punctuation(src, str(dst)) for src, dst in zip(chunk, translated_chunk))
        except Exception:
            for text in chunk:
                try:
                    translated.append(preserve_terminal_punctuation(text, str(translator.translate(text))))
                except Exception:
                    translated.append(text)
    return translated


def preserve_terminal_punctuation(source_text: str, translated_text: str) -> str:
    source_q_match = re.search(r"(\?+)\s*$", source_text.strip())
    source_e_match = re.search(r"(!+)\s*$", source_text.strip())
    source_q = len(source_q_match.group(1)) if source_q_match else 0
    source_e = len(source_e_match.group(1)) if source_e_match else 0

    result = translated_text.rstrip()
    if source_q and "?" not in result[-3:]:
        result += "?" * min(source_q, 2)
    if source_e and "!" not in result[-3:]:
        result += "!" * min(source_e, 2)
    return result


st.title("通用评论抓取与分析")
st.write("输入 X、Steam、YouTube 网址或本地文件路径，系统会自动抓取评论、清洗去重并给出可视化分析结论。")

with st.sidebar:
    st.header("抓取参数")
    source = st.text_input("网址或本地文件路径", value="https://www.youtube.com/watch?v=KK56dSyu_SM")
    st.caption("示例：YouTube 视频链接、Steam 评论页链接、X 帖子链接，或本地 txt/csv/jsonl 文件。")
    limit = st.slider("最大抓取条数", min_value=20, max_value=1000, value=200, step=20)
    sort = "recent"
    timeout = st.slider("网页请求超时（秒）", min_value=5, max_value=60, value=15, step=5)

    st.header("X 登录（抓取更完整的 X 评论时需要）")
    x_saved_session_available = has_x_saved_login_state()
    x_cookie_header = ""
    x_csrf_token = ""
    st.caption("推荐方式：直接点击“开始抓取并分析”。程序会先尝试复用本地已缓存的公开回复；如果需要更完整的线程或本地没有可复用缓存，再自动弹出本机 Microsoft Edge 登录窗口让你登录，这里不需要填写账号或密码。")
    if x_saved_session_available:
        st.caption("已检测到项目内保存的 X 登录态，会优先自动复用，以获取更完整的 X 评论线程。")
    if st.button("预先登录 X 并保存会话", use_container_width=True):
        try:
            with st.spinner("正在打开 Microsoft Edge，请在弹出的窗口中完成 X 登录..."):
                login_x_and_save_session(timeout_seconds=300.0)
            st.success("X 登录态已保存，之后抓取 X 评论会自动复用。")
            st.rerun()
        except Exception as error:
            st.error(f"X 登录失败: {error}")

    with st.expander("手动提供已登录浏览器会话（高级，通常不需要）"):
        x_cookie_header = st.text_input(
            "已登录请求里的 Cookie Header（不是账号/密码）",
            value="",
            type="password",
            help="仅在自动登录或本地缓存不可用时使用。这里要粘贴的是浏览器网络请求里的整段 Cookie，不是 X 账号、邮箱或密码。",
        )
        x_csrf_token = st.text_input(
            "X CSRF Token（可选）",
            value="",
            type="password",
            help="通常可留空；若你手填的 Cookie 里不含 ct0，再单独填写。",
        )
    st.caption("Steam / YouTube 无需填写任何会话信息。若你手填了高级会话，程序会优先使用；否则 X 会先复用项目内已保存登录态，再尝试本地已缓存的公开回复；仍不可用时才自动弹出本机 Microsoft Edge 登录窗口。")

    if is_x_source(source) and not x_cookie_header.strip() and not x_saved_session_available:
        st.info("当前尚未检测到本地 X 登录缓存。直接开始抓取时，程序会先尝试本地已缓存的公开回复；如果没有可复用缓存，再自动弹出 Microsoft Edge 登录窗口。上面的按钮只是用于提前登录。")

    st.header("清洗参数")
    min_info = st.slider("最低信息量阈值", min_value=0.0, max_value=1.0, value=0.10, step=0.01)
    sim_threshold = st.slider("近重复相似度阈值", min_value=0.5, max_value=0.98, value=0.75, step=0.01)
    repetition_ratio = st.slider("重复字符比例阈值", min_value=0.4, max_value=0.95, value=0.72, step=0.01)

    st.header("翻译功能")
    enable_translation = st.checkbox("启用结果翻译", value=False)
    target_language_label = st.selectbox("目标语言", options=list(TRANSLATION_LANGUAGES.keys()), index=1)
    translate_sample_limit = st.slider("样本区翻译条数", min_value=20, max_value=500, value=120, step=20)
    st.caption("启用后会翻译：结论、词云关键词；评论样本会尽量按所选语言显示对应翻译。")

    run_btn = st.button("开始抓取并分析", use_container_width=True)

if run_btn:
    try:
        with st.spinner("正在抓取评论..."):
            with temporary_env_vars(
                {
                    "X_COOKIE_HEADER": x_cookie_header.strip() or None,
                    "X_CSRF_TOKEN": x_csrf_token.strip() or None,
                }
            ):
                fetched = collect_from_source(source=source, limit=limit, sort=sort, timeout=float(timeout))

        raw_comments = [item.text for item in fetched]
        if not raw_comments:
            st.warning("没有抓取到评论，请检查链接是否可访问，或尝试提高 limit。")
        else:
            with st.spinner("正在清洗评论..."):
                config = CleanerConfig(
                    min_information_score=float(min_info),
                    similarity_threshold=float(sim_threshold),
                    max_repetition_ratio=float(repetition_ratio),
                )
                clean_report = clean_comments(raw_comments, config)
                kept_comments = [item["raw_text"] for item in clean_report["kept_comments"]]
                contextual_kept_comments = build_contextual_comments_for_analysis(fetched, clean_report)

            with st.spinner("正在生成分析结论..."):
                analysis = analyze_comments(raw_comments=raw_comments, kept_comments=contextual_kept_comments or kept_comments)

            comparison_df = build_comparison_dataframe(raw_comments, clean_report, fetched_items=fetched)

            render_summary_cards(analysis, clean_report)
            render_fetch_quality_stats(fetched, comparison_df)
            render_conclusions(analysis)

            target_lang = TRANSLATION_LANGUAGES[target_language_label]
            translated_conclusions: list[str] | None = None
            translated_kept_comments: list[str] | None = None
            translated_top_terms: list[tuple[str, float]] | None = None
            sample_translation_error: str | None = None
            if enable_translation:
                with st.spinner("正在翻译结果..."):
                    translated_conclusions = translate_texts(tuple(analysis.conclusions), target_lang)
                    translated_kept_comments = translate_texts(tuple(kept_comments[:translate_sample_limit]), target_lang)
                    translated_top_terms = build_translated_top_terms(analysis.top_terms, target_lang)

                st.subheader(f"自动结论翻译（{target_language_label}）")
                for line in translated_conclusions:
                    st.write(f"- {line}")
            else:
                translated_kept_comments = None
                st.warning("当前未启用翻译：词云和自动结论将保持原文。")

            chart_terms = translated_top_terms if translated_top_terms is not None else None
            chart_suffix = f"（{target_language_label}）" if translated_top_terms is not None else ""
            render_charts(analysis, display_terms=chart_terms, term_title_suffix=chart_suffix)

            with st.expander("查看清洗后评论样本", expanded=False):
                sample_comments = kept_comments[:200]
                if enable_translation and translated_kept_comments is not None:
                    sample_translations = translated_kept_comments[: len(sample_comments)]
                    if len(sample_translations) < len(sample_comments):
                        sample_translations.extend(sample_comments[len(sample_translations) :])
                    translation_column = f"对应翻译（{target_language_label}）"
                    sample_df = pd.DataFrame(
                        {
                            "原文评论": sample_comments,
                            translation_column: sample_translations,
                        }
                    )
                else:
                    sample_df = pd.DataFrame({"原文评论": sample_comments})
                st.dataframe(sample_df, use_container_width=True)

            display_df = comparison_df
            render_comment_level_insights(display_df)
            render_raw_comment_common_conclusions(
                raw_comments=raw_comments,
                fetched=fetched,
                analysis=analysis,
                comparison_df=display_df,
            )

            with st.expander("查看完整清洗报告 JSON", expanded=False):
                st.json(clean_report)
    except Exception as error:
        error_detail = str(error).strip()
        if error_detail:
            st.error(f"分析失败: {type(error).__name__}: {error_detail}")
        else:
            st.error(f"分析失败: {type(error).__name__}")
else:
    st.info("在左侧输入来源后，点击“开始抓取并分析”。")