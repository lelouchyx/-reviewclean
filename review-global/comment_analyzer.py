from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from statistics import median

from comment_cleaner import contains_cjk, normalize_text, tokenize


POSITIVE_CN = {"好", "喜欢", "满意", "推荐", "不错", "靠谱", "赞", "值得", "解决", "快"}
NEGATIVE_CN = {"差", "垃圾", "失望", "慢", "糟", "投诉", "不好", "问题", "贵", "崩溃"}

POSITIVE_EN = {"good", "great", "love", "nice", "excellent", "amazing", "helpful", "fast", "best", "works"}
NEGATIVE_EN = {"bad", "poor", "hate", "slow", "broken", "issue", "problem", "worse", "worst", "bug"}

EN_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to", "of", "for", "in",
    "on", "at", "by", "and", "or", "but", "if", "then", "than", "this", "that", "these", "those",
    "it", "its", "as", "from", "with", "about", "into", "through", "over", "under", "again", "very",
    "can", "will", "just", "not", "no", "yes", "you", "your", "we", "our", "they", "their", "he",
    "she", "i", "me", "my", "mine", "do", "does", "did", "done", "have", "has", "had",
    "some", "much", "too", "little", "final", "also", "still", "even", "more", "most", "thi", "wa", "ha",
}

ZH_STOPWORDS = {
    "的", "了", "是", "在", "也", "和", "与", "就", "都", "而", "及", "着", "但", "并", "很",
    "还", "把", "被", "这", "那", "一个", "我们", "你", "我", "他", "她", "它", "啊", "呀", "哦",
    "吧", "呢", "嘛", "吗", "有", "没有", "可以", "还是", "就是",
}

FILLER_TERMS = {
    "dude", "guy", "guys", "bro", "man", "lol", "lmao", "omg", "actual", "actually",
    "thing", "stuff", "someone", "anyone", "everyon", "liter", "basical", "yeah", "uh",
}

FOCUS_TERMS = {
    # 价格与价值
    "price", "pric", "expens", "cheap", "cost", "value", "worth", "budget", "贵", "便宜", "价格", "性价比", "值",
    # 速度与交付
    "speed", "fast", "slow", "deliveri", "delivery", "ship", "logistic", "物流", "速度", "快", "慢", "发货",
    # 质量与效果
    "qualiti", "quality", "result", "output", "detail", "accuraci", "稳定", "质量", "效果", "输出", "细节",
    # 服务与体验
    "servic", "service", "support", "respons", "客服", "服务", "回复", "体验", "问题", "修复", "bug",
}


@dataclass
class AnalysisResult:
    summary: dict[str, object]
    top_terms: list[tuple[str, float]]
    sentiment_distribution: dict[str, int]
    tone_distribution: dict[str, int]
    language_distribution: dict[str, int]
    length_distribution: list[int]
    conclusions: list[str]


def analyze_comments(raw_comments: list[str], kept_comments: list[str]) -> AnalysisResult:
    kept_tokens = [tokenize(normalize_text(text)) for text in kept_comments]
    top_terms = build_tfidf_top_terms(kept_tokens, top_k=30)

    sentiments = classify_sentiments(kept_tokens)
    tones = classify_tones(kept_tokens)
    language_distribution = classify_languages(kept_comments)
    lengths = [len(normalize_text(text).replace(" ", "")) for text in kept_comments]

    summary = {
        "input_count": len(raw_comments),
        "kept_count": len(kept_comments),
        "removed_count": max(len(raw_comments) - len(kept_comments), 0),
        "avg_length": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "median_length": median(lengths) if lengths else 0,
    }

    conclusions = build_conclusions(summary, sentiments, tones, language_distribution, top_terms)

    return AnalysisResult(
        summary=summary,
        top_terms=top_terms,
        sentiment_distribution=sentiments,
        tone_distribution=tones,
        language_distribution=language_distribution,
        length_distribution=lengths,
        conclusions=conclusions,
    )


def classify_languages(comments: list[str]) -> dict[str, int]:
    counts = Counter({"zh": 0, "en": 0, "mixed_or_other": 0})
    for text in comments:
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


def classify_sentiments(tokenized_comments: list[list[str]]) -> dict[str, int]:
    counts = Counter({"positive": 0, "neutral": 0, "negative": 0, "skeptical": 0})
    for tokens in tokenized_comments:
        pos = 0
        neg = 0
        question_score = 0
        for token in tokens:
            base = token.lower()
            if base in POSITIVE_CN or base in POSITIVE_EN:
                pos += 1
            if base in NEGATIVE_CN or base in NEGATIVE_EN:
                neg += 1
            if base in {"<question>", "<strong_question>"}:
                question_score += 2 if base == "<strong_question>" else 1

        if question_score >= 1 and pos >= neg:
            counts["skeptical"] += 1
            continue

        if pos > neg:
            counts["positive"] += 1
        elif neg > pos:
            counts["negative"] += 1
        else:
            counts["neutral"] += 1
    return dict(counts)


def classify_tones(tokenized_comments: list[list[str]]) -> dict[str, int]:
    counts = Counter({"assertive": 0, "skeptical": 0, "neutral": 0})
    for tokens in tokenized_comments:
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

        if question_score >= 1:
            counts["skeptical"] += 1
        elif exclaim_score >= 1:
            counts["assertive"] += 1
        else:
            counts["neutral"] += 1
    return dict(counts)


def build_tfidf_top_terms(tokenized_comments: list[list[str]], top_k: int = 30) -> list[tuple[str, float]]:
    if not tokenized_comments:
        return []

    filtered_docs: list[list[str]] = []
    for tokens in tokenized_comments:
        filtered = [token for token in tokens if is_informative_term(token)]
        if filtered:
            filtered_docs.append(filtered)

    if not filtered_docs:
        return []

    doc_count = len(filtered_docs)
    document_frequency: Counter[str] = Counter()
    for tokens in filtered_docs:
        document_frequency.update(set(tokens))

    idf = {
        term: math.log(doc_count / frequency) if frequency else 0.0
        for term, frequency in document_frequency.items()
    }

    tfidf_score: Counter[str] = Counter()
    for tokens in filtered_docs:
        counts = Counter(tokens)
        total = sum(counts.values())
        if total == 0:
            continue
        for term, count in counts.items():
            tf = count / total
            tfidf_score[term] += tf * idf.get(term, 0.0)

    boosted: list[tuple[str, float]] = []
    for term, score in tfidf_score.items():
        weight = 1.0
        if term in FOCUS_TERMS:
            weight += 0.60
        if term in POSITIVE_CN or term in POSITIVE_EN or term in NEGATIVE_CN or term in NEGATIVE_EN:
            weight += 0.40
        boosted.append((term, score * weight))

    boosted.sort(key=lambda item: item[1], reverse=True)
    return [(term, round(score, 4)) for term, score in boosted[:top_k]]


def is_informative_term(token: str) -> bool:
    if token in {"<num>", "url"}:
        return False

    normalized = token.strip().lower()
    if not normalized:
        return False

    if normalized in EN_STOPWORDS or normalized in ZH_STOPWORDS:
        return False

    if normalized in FILLER_TERMS:
        return False

    if len(normalized) == 1 and not contains_cjk(normalized):
        return False

    return True


def build_conclusions(
    summary: dict[str, object],
    sentiments: dict[str, int],
    tones: dict[str, int],
    languages: dict[str, int],
    top_terms: list[tuple[str, float]],
) -> list[str]:
    conclusions: list[str] = []
    input_count = int(summary.get("input_count", 0))
    kept_count = int(summary.get("kept_count", 0))
    removed = int(summary.get("removed_count", 0))

    if input_count > 0:
        removal_ratio = removed / input_count
        conclusions.append(f"清洗后保留 {kept_count}/{input_count} 条评论，过滤比例约 {removal_ratio:.1%}。")

    dominant_sentiment = max(sentiments, key=sentiments.get) if sentiments else "neutral"
    conclusions.append(f"情绪倾向以 {dominant_sentiment} 为主（仅为词典启发式估计）。")

    skeptical_count = int(tones.get("skeptical", 0))
    if kept_count > 0:
        conclusions.append(f"语气层面反问/怀疑占比约 {skeptical_count / kept_count:.1%}。")

    dominant_language = max(languages, key=languages.get) if languages else "mixed_or_other"
    conclusions.append(f"评论语言以 {dominant_language} 为主，可据此选择后续模型或词典。")

    if top_terms:
        keyword_preview = "、".join(term for term, _ in top_terms[:5])
        conclusions.append(f"高频关键词主要集中在：{keyword_preview}。")

    return conclusions