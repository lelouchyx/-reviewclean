from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from comment_io import load_comments_from_path as load_comments_file

try:
    import jieba  # type: ignore
except ImportError:
    jieba = None

try:
    from nltk.stem import SnowballStemmer  # type: ignore
except ImportError:
    SnowballStemmer = None


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+|[!?]+")
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r"\s+")
EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")

DEMO_COMMENTS = [
    "这个产品真的很好用，物流也快。",
    "这个产品真的很好用!!! 物流也很快",
    "哈哈哈哈哈哈",
    "客服回复很慢，但是最后还是解决了问题。",
    "客服回复很慢 但最后解决了问题",
    "Great product, loved the packaging and delivery speed.",
    "Great products, loving the package and the fast delivery.",
    "一般般，没有宣传得那么好。",
    "1111111111",
    "点击链接领取优惠 http://spam.example",
]

ENGLISH_STEMMER = SnowballStemmer("english") if SnowballStemmer is not None else None


@dataclass
class CleanerConfig:
    # 这些阈值决定了评论会因信息量不足、重复噪声或近重复而被过滤。
    min_information_score: float = 0.10
    similarity_threshold: float = 0.75
    max_repetition_ratio: float = 0.72
    min_compact_length: int = 2
    keep_emoji_variants: bool = True


@dataclass
class CommentDecision:
    # 这是最终输出给报告层的单条评论判定结果。
    original_index: int
    raw_text: str
    normalized_text: str
    tokens: list[str]
    information_score: float
    kept: bool
    reason: str
    similar_to: int | None = None
    similarity: float | None = None


@dataclass
class PreparedComment:
    # 这是清洗流程内部使用的中间表示，已经包含词频和向量。
    original_index: int
    raw_text: str
    normalized_text: str
    tokens: list[str]
    counts: Counter[str]
    vector: dict[str, float]
    information_score: float


def normalize_text(text: str) -> str:
    # 统一字符形态、链接和空白，尽量消掉与语义无关的表面差异。
    text = unicodedata.normalize("NFKC", text)
    text = URL_PATTERN.sub(" url ", text)
    text = text.lower()
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for piece in TOKEN_PATTERN.findall(text):
        if set(piece) <= {"?", "!"}:
            tokens.extend(punctuation_tokens(piece))
            continue
        if contains_cjk(piece):
            tokens.extend(tokenize_cjk(piece))
        elif piece.isdigit():
            tokens.append("<num>")
        else:
            stemmed = stem_english(piece)
            if stemmed:
                tokens.append(stemmed)
    return tokens


def punctuation_tokens(piece: str) -> list[str]:
    marks = set(piece)
    tokens: list[str] = []
    if "?" in marks:
        tokens.append("<question>")
        if len(piece) >= 2:
            tokens.append("<strong_question>")
    if "!" in marks:
        tokens.append("<exclaim>")
        if len(piece) >= 2:
            tokens.append("<strong_exclaim>")
    return tokens


def contains_cjk(piece: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in piece)


def tokenize_cjk(piece: str) -> list[str]:
    if jieba is not None:
        # 有 jieba 时优先做较自然的中文分词，再补充细粒度片段提高近重复召回。
        base_tokens = [token.strip() for token in jieba.cut(piece) if token.strip()]
        return enrich_cjk_tokens(base_tokens)

    if len(piece) == 1:
        return [piece]

    # 没有 jieba 时退化为二元切分，避免整段中文只形成一个超长 token。
    tokens = [piece[index : index + 2] for index in range(len(piece) - 1)]
    if len(piece) <= 4:
        tokens.append(piece)
    return tokens


def enrich_cjk_tokens(tokens: list[str]) -> list[str]:
    enriched: list[str] = []
    for token in tokens:
        enriched.append(token)
        if len(token) == 2:
            enriched.extend(list(token))
        elif len(token) > 2:
            enriched.extend(token[index : index + 2] for index in range(len(token) - 1))
    return enriched


def stem_english(token: str) -> str:
    token = token.lower()
    if ENGLISH_STEMMER is not None:
        return ENGLISH_STEMMER.stem(token)
    return light_english_stem(token)


def light_english_stem(token: str) -> str:
    if len(token) <= 3:
        return token

    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("ing") and len(token) > 5:
        token = token[:-3]
    elif token.endswith("ed") and len(token) > 4:
        token = token[:-2]
    elif token.endswith("ly") and len(token) > 4:
        token = token[:-2]
    elif token.endswith(("ses", "xes", "zes", "ches", "shes")) and len(token) > 4:
        token = token[:-2]
    elif token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        token = token[:-1]

    if len(token) >= 3 and token[-1] == token[-2] and token[-1] not in "aeiou":
        token = token[:-1]

    return token


def prepare_comments(comments: Iterable[str]) -> list[PreparedComment]:
    comments = list(comments)
    prepared: list[PreparedComment] = []
    normalized_comments: list[tuple[int, str, list[str]]] = []

    for index, raw_text in enumerate(comments, start=1):
        normalized_text = normalize_text(raw_text)
        tokens = tokenize(normalized_text)
        normalized_comments.append((index, normalized_text, tokens))

    # 先看每个词出现于多少条评论，再统一计算整个语料的 IDF。
    doc_count = len(normalized_comments)
    document_frequency: Counter[str] = Counter()
    for _, _, tokens in normalized_comments:
        document_frequency.update(set(tokens))

    idf = build_idf(document_frequency, doc_count)

    for original_index, normalized_text, tokens in normalized_comments:
        counts = Counter(tokens)
        vector, information_score = build_tfidf_vector(counts, idf)
        prepared.append(
            PreparedComment(
                original_index=original_index,
                raw_text=comments[original_index - 1],
                normalized_text=normalized_text,
                tokens=tokens,
                counts=counts,
                vector=vector,
                information_score=information_score,
            )
        )

    return prepared


def build_idf(document_frequency: Counter[str], doc_count: int) -> dict[str, float]:
    if doc_count <= 1:
        return {term: 1.0 for term in document_frequency}
    return {
        term: math.log(doc_count / frequency) if frequency else 0.0
        for term, frequency in document_frequency.items()
    }


def build_tfidf_vector(
    counts: Counter[str],
    idf: dict[str, float],
) -> tuple[dict[str, float], float]:
    total_terms = sum(counts.values())
    if total_terms == 0:
        return {}, 0.0

    # 先做按评论长度的词频归一化，再乘上 IDF 抑制常见词。
    weighted = {
        term: (count / total_terms) * idf.get(term, 0.0)
        for term, count in counts.items()
    }
    information_score = sum(weighted.values())

    # 再做 L2 归一化，便于后续直接用余弦相似度比较评论向量。
    l2_norm = math.sqrt(sum(value * value for value in weighted.values()))
    if l2_norm == 0:
        return weighted, information_score

    normalized_vector = {
        term: value / l2_norm
        for term, value in weighted.items()
    }
    return normalized_vector, information_score


def compact_length(text: str) -> int:
    return len(text.replace(" ", ""))


def repetition_ratio(text: str) -> float:
    compact = text.replace(" ", "")
    if not compact:
        return 1.0
    char_counts = Counter(compact)
    return max(char_counts.values()) / len(compact)


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def clean_comments(comments: list[str], config: CleanerConfig) -> dict[str, object]:
    # 按输入顺序扫描，默认保留先出现的高质量评论，后续近重复评论会被压掉。
    prepared = prepare_comments(comments)
    kept_vectors: list[tuple[int, dict[str, float], str]] = []
    # token -> kept_vectors 中的位置集合，用于快速锁定近重复候选。
    kept_token_positions: dict[str, set[int]] = {}
    decisions: list[CommentDecision] = []

    for item in prepared:
        reason = find_low_quality_reason(item, config)
        similar_to = None
        similarity = None

        if not reason and item.vector:
            candidate_positions = find_duplicate_candidate_positions(item.tokens, kept_token_positions)
            similar_to, similarity = find_nearest_duplicate(item, kept_vectors, candidate_positions)
            if similarity is not None and similarity >= config.similarity_threshold:
                matched_text = ""
                for kept_index, _, kept_text in kept_vectors:
                    if kept_index == similar_to:
                        matched_text = kept_text
                        break

                if not (
                    config.keep_emoji_variants
                    and matched_text
                    and is_emoji_variant(item.raw_text, matched_text)
                ):
                    reason = "near_duplicate"

        kept = not reason
        decisions.append(
            CommentDecision(
                original_index=item.original_index,
                raw_text=item.raw_text,
                normalized_text=item.normalized_text,
                tokens=item.tokens,
                information_score=round(item.information_score, 4),
                kept=kept,
                reason=reason or "kept",
                similar_to=similar_to,
                similarity=round(similarity, 4) if similarity is not None else None,
            )
        )

        if kept:
            kept_vectors.append((item.original_index, item.vector, item.raw_text))
            kept_position = len(kept_vectors) - 1
            for token in set(item.tokens):
                if token.startswith("<") and token.endswith(">"):
                    continue
                kept_token_positions.setdefault(token, set()).add(kept_position)

    kept_comments = [asdict(decision) for decision in decisions if decision.kept]
    removed_comments = [asdict(decision) for decision in decisions if not decision.kept]

    reason_counts = Counter(decision.reason for decision in decisions if not decision.kept)
    summary = {
        "input_comments": len(comments),
        "kept_comments": len(kept_comments),
        "removed_comments": len(removed_comments),
        "removal_reasons": dict(reason_counts),
    }

    return {
        "summary": summary,
        "kept_comments": kept_comments,
        "removed_comments": removed_comments,
    }


def find_low_quality_reason(item: PreparedComment, config: CleanerConfig) -> str:
    # 先过滤明显噪声，再看信息量，避免把“哈哈哈哈”这类文本留到相似度阶段。
    if compact_length(item.normalized_text) < config.min_compact_length:
        return "too_short"
    if not item.tokens:
        return "no_tokens"
    if repetition_ratio(item.normalized_text) >= config.max_repetition_ratio and compact_length(item.normalized_text) >= 4:
        return "repetitive_noise"
    if item.information_score < config.min_information_score:
        return "low_information"
    return ""


def find_nearest_duplicate(
    item: PreparedComment,
    kept_vectors: list[tuple[int, dict[str, float], str]],
    candidate_positions: set[int] | None = None,
) -> tuple[int | None, float | None]:
    # 只与已保留评论比较，找到最相近的那一条作为重复依据。
    best_index = None
    best_similarity = None

    if candidate_positions is None:
        iterable = enumerate(kept_vectors)
    else:
        iterable = ((position, kept_vectors[position]) for position in candidate_positions if 0 <= position < len(kept_vectors))

    for _, (kept_index, kept_vector, _) in iterable:
        similarity = cosine_similarity(item.vector, kept_vector)
        if best_similarity is None or similarity > best_similarity:
            best_index = kept_index
            best_similarity = similarity

    return best_index, best_similarity


def find_duplicate_candidate_positions(
    tokens: list[str],
    kept_token_positions: dict[str, set[int]],
) -> set[int]:
    # 通过共享 token 快速缩小候选集合，减少无意义的全量向量比较。
    positions: set[int] = set()
    for token in set(tokens):
        if token.startswith("<") and token.endswith(">"):
            continue
        positions.update(kept_token_positions.get(token, set()))
    return positions


def is_emoji_variant(left: str, right: str) -> bool:
    left_no_emoji = WHITESPACE_PATTERN.sub(" ", EMOJI_PATTERN.sub("", left)).strip()
    right_no_emoji = WHITESPACE_PATTERN.sub(" ", EMOJI_PATTERN.sub("", right)).strip()
    if not left_no_emoji or not right_no_emoji:
        return False
    if left_no_emoji != right_no_emoji:
        return False
    return bool(EMOJI_PATTERN.search(left) or EMOJI_PATTERN.search(right))


def write_report(report: dict[str, object], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_cleaned_comments(report: dict[str, object], output_path: Path) -> None:
    kept_comments = report.get("kept_comments", [])
    lines = [item["raw_text"] for item in kept_comments if isinstance(item, dict) and item.get("raw_text")]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于归一化、IDF 和词干提取的评论清洗器。"
    )
    parser.add_argument("--input", type=Path, help="输入文件路径，支持 .txt、.csv、.jsonl")
    parser.add_argument("--output", type=Path, default=Path("cleaned_comments_report.json"), help="输出 JSON 报告路径")
    parser.add_argument("--clean-output", type=Path, help="只保留清洗后评论的文本输出路径")
    parser.add_argument("--demo", action="store_true", help="使用内置示例评论运行")
    parser.add_argument("--min-information-score", type=float, default=0.10, help="低信息评论的过滤阈值")
    parser.add_argument("--similarity-threshold", type=float, default=0.75, help="近重复评论的余弦相似度阈值")
    parser.add_argument("--max-repetition-ratio", type=float, default=0.72, help="重复字符占比阈值")
    parser.add_argument("--keep-emoji-variants", action="store_true", default=True, help="保留仅 emoji 差异的评论变体")
    parser.add_argument("--no-keep-emoji-variants", action="store_false", dest="keep_emoji_variants", help="将 emoji 变体也视为近重复")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.demo and args.input is None:
        raise SystemExit("请提供 --input，或使用 --demo 查看示例结果。")

    comments = DEMO_COMMENTS if args.demo else load_comments_file(args.input)
    config = CleanerConfig(
        min_information_score=args.min_information_score,
        similarity_threshold=args.similarity_threshold,
        max_repetition_ratio=args.max_repetition_ratio,
        keep_emoji_variants=args.keep_emoji_variants,
    )

    report = clean_comments(comments, config)
    write_report(report, args.output)
    if args.clean_output is not None:
        write_cleaned_comments(report, args.clean_output)

    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"已写出报告: {args.output}")
    if args.clean_output is not None:
        print(f"已写出清洗后评论: {args.clean_output}")

    kept_comments = report["kept_comments"]
    if kept_comments:
        print("保留评论预览:")
        for item in kept_comments[:5]:
            print(f"- #{item['original_index']}: {item['raw_text']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())