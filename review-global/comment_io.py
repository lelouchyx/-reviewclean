from __future__ import annotations

import csv
import json
from pathlib import Path


COMMENT_COLUMNS = ["comment", "comments", "content", "text", "review", "message"]


def normalize_space(text: str) -> str:
    return " ".join(text.split()).strip()


def load_comments_from_path(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_from_txt(path)
    if suffix == ".csv":
        return load_from_csv(path)
    if suffix == ".jsonl":
        return load_from_jsonl(path)
    raise ValueError("输入文件只支持 .txt、.csv 和 .jsonl。")


def load_from_txt(path: Path) -> list[str]:
    return [normalize_space(line) for line in path.read_text(encoding="utf-8").splitlines() if normalize_space(line)]


def load_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV 文件缺少表头。")

        selected_column = next((column for column in COMMENT_COLUMNS if column in reader.fieldnames), None)
        if selected_column is None:
            selected_column = reader.fieldnames[0]

        comments: list[str] = []
        for row in reader:
            value = normalize_space(str(row.get(selected_column) or ""))
            if value:
                comments.append(value)
        return comments


def load_from_jsonl(path: Path) -> list[str]:
    comments: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        payload = json.loads(line)
        if isinstance(payload, str):
            value = normalize_space(payload)
            if value:
                comments.append(value)
            continue

        for key in COMMENT_COLUMNS:
            value = payload.get(key)
            if isinstance(value, str):
                normalized = normalize_space(value)
                if normalized:
                    comments.append(normalized)
                    break
    return comments