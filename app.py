from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from matplotlib import pyplot as plt
from wordcloud import WordCloud

from comment_analyzer import (
    AnalysisResult,
    NEGATIVE_CN,
    NEGATIVE_EN,
    POSITIVE_CN,
    POSITIVE_EN,
    analyze_comments,
)
from comment_cleaner import CleanerConfig, clean_comments, normalize_text, tokenize
from comment_fetcher import collect_from_source

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

    theme_keywords: dict[str, set[str]] = {
        "速度与效率": {"fast", "speed", "quick", "slow", "lag", "卡", "快", "慢", "速度", "延迟"},
        "效果与稳定性": {"quality", "result", "output", "bug", "broken", "稳定", "不稳定", "效果", "崩", "修复"},
        "价格与价值": {"price", "pricing", "expensive", "cheap", "cost", "worth", "贵", "便宜", "价格", "值", "订阅"},
        "易用性与流程整合": {"easy", "simple", "workflow", "tool", "入口", "方便", "一站", "整合", "上手", "省事"},
        "推送与营销打扰": {"ads", "ad", "promo", "discount", "coupon", "推送", "广告", "营销", "优惠", "链接"},
    }

    positive_themes = {name: 0 for name in theme_keywords}
    negative_themes = {name: 0 for name in theme_keywords}

    for text in raw_comments:
        normalized = normalize_text(text)
        tokens = tokenize(normalized)
        token_set = set(tokens)
        pos_hits = sum(1 for token in token_set if token in POSITIVE_CN or token in POSITIVE_EN)
        neg_hits = sum(1 for token in token_set if token in NEGATIVE_CN or token in NEGATIVE_EN)

        matched_themes: list[str] = []
        lowered = normalized.lower()
        for theme, keywords in theme_keywords.items():
            if any(keyword in lowered for keyword in keywords):
                matched_themes.append(theme)

        if not matched_themes:
            continue

        if pos_hits >= neg_hits:
            for theme in matched_themes:
                positive_themes[theme] += 1
        else:
            for theme in matched_themes:
                negative_themes[theme] += 1

    top_positive = [name for name, count in sorted(positive_themes.items(), key=lambda item: item[1], reverse=True) if count > 0][:3]
    top_negative = [name for name, count in sorted(negative_themes.items(), key=lambda item: item[1], reverse=True) if count > 0][:3]

    dominant_sentiment = max(analysis.sentiment_distribution, key=analysis.sentiment_distribution.get)
    skeptical_count = int((comparison_df.get("tone_label", pd.Series(dtype=str)) == "skeptical").sum())
    skeptical_ratio = skeptical_count / len(raw_comments) if raw_comments else 0.0

    if top_positive and top_negative:
        conclusions.append(
            "从原始评论看，正面反馈主要集中在"
            + "、".join(top_positive)
            + "；负面反馈更集中在"
            + "、".join(top_negative)
            + "。"
        )
    elif top_positive:
        conclusions.append("从原始评论看，正面讨论主要集中在" + "、".join(top_positive) + "，负面焦点相对分散。")
    elif top_negative:
        conclusions.append("从原始评论看，负面讨论主要集中在" + "、".join(top_negative) + "，正面焦点相对分散。")
    else:
        conclusions.append("从原始评论看，讨论焦点较分散，尚未形成清晰的单一主题。")

    if dominant_sentiment == "positive":
        conclusions.append("整体情绪偏正向，但用户评价并非单纯叫好，更强调实际体验是否持续稳定。")
    elif dominant_sentiment == "negative":
        conclusions.append("整体情绪偏负向，核心矛盾集中在结果预期与实际体验之间的落差。")
    else:
        conclusions.append("整体情绪偏中性，用户更像在做功能与成本之间的理性权衡。")

    total_sentiments = sum(analysis.sentiment_distribution.values())
    if total_sentiments > 0:
        positive_ratio = analysis.sentiment_distribution.get("positive", 0) / total_sentiments
        neutral_ratio = analysis.sentiment_distribution.get("neutral", 0) / total_sentiments
        negative_ratio = analysis.sentiment_distribution.get("negative", 0) / total_sentiments
        conclusions.append(
            f"情绪占比上，正向约 {positive_ratio:.1%}、中立约 {neutral_ratio:.1%}、负向约 {negative_ratio:.1%}；"
            "负向占比走高通常意味着反馈偏差，中立占比较高通常说明反馈偏观察和保留态度。"
        )

    if skeptical_ratio >= 0.25:
        conclusions.append("反问与怀疑语气占比较高，说明评论区对“值不值”和“稳不稳”这两类问题仍有明显争论。")
    else:
        conclusions.append("反问与怀疑语气处于可控区间，讨论更多围绕具体使用体验而不是情绪化对立。")

    conclusions.append("从消费级 AI 产品的评价结构看，这批评论呈现出典型特征：功能可用性已被认可，但价值感和稳定性仍是决定口碑是否能持续的关键。")
    return conclusions


def render_raw_comment_common_conclusions(
    raw_comments: list[str],
    fetched: list[Any],
    analysis: AnalysisResult,
    comparison_df: pd.DataFrame,
) -> None:
    st.subheader("原始评论共性结论（独立栏目）")
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
st.write("输入网址或本地文件路径，系统会自动抓取评论、清洗去重并给出可视化分析结论。")

with st.sidebar:
    st.header("抓取参数")
    source = st.text_input("网址或本地文件路径", value="https://www.youtube.com/watch?v=KK56dSyu_SM")
    limit = st.slider("最大抓取条数", min_value=20, max_value=1000, value=200, step=20)
    sort = st.selectbox("YouTube 排序", options=["popular", "recent"], index=0)
    timeout = st.slider("网页请求超时（秒）", min_value=5, max_value=60, value=15, step=5)

    st.header("清洗参数")
    min_info = st.slider("最低信息量阈值", min_value=0.0, max_value=1.0, value=0.10, step=0.01)
    sim_threshold = st.slider("近重复相似度阈值", min_value=0.5, max_value=0.98, value=0.75, step=0.01)
    repetition_ratio = st.slider("重复字符比例阈值", min_value=0.4, max_value=0.95, value=0.72, step=0.01)

    st.header("翻译功能")
    enable_translation = st.checkbox("启用结果翻译", value=False)
    target_language_label = st.selectbox("目标语言", options=list(TRANSLATION_LANGUAGES.keys()), index=1)
    translate_sample_limit = st.slider("样本区翻译条数", min_value=20, max_value=500, value=120, step=20)
    st.caption("启用后会翻译：结论、词云关键词、评论样本和对照表（含 CSV 导出）。")

    run_btn = st.button("开始抓取并分析", use_container_width=True)

if run_btn:
    try:
        with st.spinner("正在抓取评论..."):
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

            translated_conclusions: list[str] | None = None
            translated_kept_comments: list[str] | None = None
            translated_top_terms: list[tuple[str, float]] | None = None
            translated_comparison_df: pd.DataFrame | None = None
            if enable_translation:
                target_lang = TRANSLATION_LANGUAGES[target_language_label]
                with st.spinner("正在翻译结果..."):
                    translated_conclusions = translate_texts(tuple(analysis.conclusions), target_lang)
                    translated_kept_comments = translate_texts(tuple(kept_comments[:translate_sample_limit]), target_lang)
                    translated_top_terms = build_translated_top_terms(analysis.top_terms, target_lang)
                    translated_comparison_df = add_translation_columns_to_comparison_df(
                        comparison_df,
                        target_lang=target_lang,
                    )

                st.subheader(f"自动结论翻译（{target_language_label}）")
                for line in translated_conclusions:
                    st.write(f"- {line}")
            else:
                st.warning("当前未启用翻译：词云、评论表格和 CSV 将保持原文。")

            chart_terms = translated_top_terms if translated_top_terms is not None else None
            chart_suffix = f"（{target_language_label}）" if translated_top_terms is not None else ""
            render_charts(analysis, display_terms=chart_terms, term_title_suffix=chart_suffix)

            with st.expander("查看清洗后评论样本", expanded=False):
                sample_comments = kept_comments[:200]
                sample_df = pd.DataFrame({"comment": sample_comments})
                if translated_kept_comments is not None:
                    translated_series = translated_kept_comments[: len(sample_comments)]
                    if len(translated_series) < len(sample_comments):
                        translated_series.extend(sample_comments[len(translated_series) :])
                    sample_df[f"translated_{target_language_label}"] = translated_series
                st.dataframe(sample_df, use_container_width=True)

            with st.expander("查看原始评论与清洗结果对照", expanded=True):
                display_df = translated_comparison_df if translated_comparison_df is not None else comparison_df
                st.dataframe(display_df, use_container_width=True)
                csv_data = display_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    label="下载对照结果 CSV",
                    data=csv_data,
                    file_name="comment_comparison.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

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
        st.error(f"分析失败: {error}")
else:
    st.info("在左侧输入来源后，点击“开始抓取并分析”。")