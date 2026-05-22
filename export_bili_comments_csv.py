#!/usr/bin/env python
# -*- coding: utf-8 -*-

import csv
import json
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
JSONL_PATH = ROOT / "review-local" / "data" / "bili" / "jsonl" / "detail_comments_2026-05-21.jsonl"
CSV_PATH = ROOT / "bilibili_comments_BV1zUfxBQEsE_200.csv"
SUMMARY_PATH = ROOT / "bilibili_comments_BV1zUfxBQEsE_summary.txt"

TARGET_VIDEO_ID = "116130530270497"
TARGET_BVID = "BV1zUfxBQEsE"
TARGET_TITLE = "《棱镜2033》实机展示&首曝PV&概念视频——未来，正在载入"
MAX_COMMENTS = 200


THEMES = {
    "质疑买量与机器人": ["人机", "机器人", "ai评论", "ai弹幕", "买评论", "买弹幕", "买量", "真人", "伪人"],
    "质疑骗投资与画饼": ["骗投资", "拉投资", "画饼", "饼", "元宇宙", "demo", "大饼"],
    "批评玩法不清": ["玩法", "核心玩法", "什么类型", "啥类型", "手游", "网游", "买断", "抽卡", "公测"],
    "批评完成度与质感": ["建模", "动作", "僵硬", "塑料", "质感", "配音", "演示", "实机", "ui", "画风"],
    "对 AI NPC 与设定感兴趣": ["ai", "AI", "npc", "NPC", "平行世界", "沉浸感", "售货机", "交互"],
    "联想到其他作品": ["2077", "GTA", "头号玩家", "塞尔达", "原子之心", "浩劫前夕", "心之眼"],
    "调侃 2033 上线时间": ["2033", "2030", "十年前出", "几年之后", "上线", "开服时间"],
    "正面期待": ["期待", "观望", "不错", "有意思", "看好", "顺利落地", "创新", "可以", "挺好"],
}


def load_comments() -> list[dict]:
    deduped: "OrderedDict[str, dict]" = OrderedDict()
    with JSONL_PATH.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("video_id") != TARGET_VIDEO_ID:
                continue
            deduped[item["comment_id"]] = item
    comments = list(deduped.values())[:MAX_COMMENTS]
    comments.sort(key=lambda item: int(item.get("like_count", 0)), reverse=True)
    return comments


def format_time(timestamp: int | str | None) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def export_csv(comments: list[dict]) -> None:
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "bvid",
                "video_id",
                "video_title",
                "comment_id",
                "parent_comment_id",
                "nickname",
                "user_id",
                "like_count",
                "create_time",
                "content",
            ],
        )
        writer.writeheader()
        for item in comments:
            writer.writerow(
                {
                    "bvid": TARGET_BVID,
                    "video_id": TARGET_VIDEO_ID,
                    "video_title": TARGET_TITLE,
                    "comment_id": item.get("comment_id", ""),
                    "parent_comment_id": item.get("parent_comment_id", ""),
                    "nickname": item.get("nickname", ""),
                    "user_id": item.get("user_id", ""),
                    "like_count": item.get("like_count", 0),
                    "create_time": format_time(item.get("create_time")),
                    "content": item.get("content", "").replace("\r", " ").replace("\n", " "),
                }
            )


def build_theme_counts(comments: list[dict]) -> Counter:
    counts: Counter = Counter()
    for item in comments:
        text = item.get("content", "")
        for theme, keywords in THEMES.items():
            if any(keyword in text for keyword in keywords):
                counts[theme] += 1
    return counts


def build_summary(comments: list[dict]) -> str:
    theme_counts = build_theme_counts(comments)
    top_liked = comments[:10]
    total = len(comments)
    total_likes = sum(int(item.get("like_count", 0)) for item in comments)

    lines: list[str] = []
    lines.append(f"视频：{TARGET_TITLE}")
    lines.append(f"BVID：{TARGET_BVID}")
    lines.append(f"去重评论数：{total}")
    lines.append(f"总点赞数：{total_likes}")
    lines.append("")
    lines.append("主题统计：")
    for theme, count in theme_counts.most_common():
        lines.append(f"- {theme}: {count} 条，占比 {count / total:.1%}")

    lines.append("")
    lines.append("高赞评论（前 10）：")
    for item in top_liked:
        lines.append(
            f"- {item.get('nickname', '')} | {item.get('like_count', 0)}赞 | {item.get('content', '')}"
        )

    return "\n".join(lines)


def main() -> None:
    comments = load_comments()
    if len(comments) < MAX_COMMENTS:
        raise RuntimeError(f"目标评论不足 {MAX_COMMENTS} 条，当前仅 {len(comments)} 条")

    export_csv(comments)
    summary = build_summary(comments)
    SUMMARY_PATH.write_text(summary, encoding="utf-8")

    print(f"CSV_WRITTEN={CSV_PATH}")
    print(f"SUMMARY_WRITTEN={SUMMARY_PATH}")
    print(f"COMMENT_COUNT={len(comments)}")
    print(summary)


if __name__ == "__main__":
    main()