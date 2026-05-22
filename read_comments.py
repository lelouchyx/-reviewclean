#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from collections import Counter
import datetime

comments_file = 'review-local/data/bili/jsonl/detail_comments_2026-05-21.jsonl'
target_video_id = "116130530270497"  # BV1zUfxBQEsE 的 AV ID（字符串格式）

all_comments = []
with open(comments_file, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        if c.get('video_id') == target_video_id:
            all_comments.append(c)

print(f"===== 视频 BV1zUfxBQEsE《棱镜2033》评论分析 =====")
print(f"共抓取：{len(all_comments)} 条评论")
print()

# 按点赞排序
sorted_comments = sorted(all_comments, key=lambda x: x.get('like_count', 0), reverse=True)

print("--- 完整评论列表（按点赞降序）---")
for i, c in enumerate(sorted_comments, 1):
    nick = c.get('nickname', '?')
    likes = c.get('like_count', 0)
    content = c.get('content', '')
    ctime = c.get('create_time', 0)
    date_str = datetime.datetime.fromtimestamp(ctime).strftime('%Y-%m-%d') if ctime else '?'
    print(f"\n{i}. [{date_str}] {nick}（赞:{likes}）")
    print(f"   {content}")

# 简单分析
print()
print("===== 基础统计 =====")
total_likes = sum(c.get('like_count', 0) for c in all_comments)
print(f"总点赞数: {total_likes}")
top = sorted_comments[0]
print(f"最高赞评论: {top.get('nickname')} ({top.get('like_count')}赞): {top.get('content', '')[:60]}")

# 关键词分析（简单）
keywords = {
    '画饼/期待': ['饼', '落地', '希望', '期待', '加油'],
    '游戏玩法': ['游戏', '玩法', '类型', 'npc', 'NPC', '实机', '演示'],
    '技术/质疑': ['元宇宙', '投资', '抄', '缝', '像'],
    '视觉/画面': ['建模', '好看', '漂亮', '城市', '科幻'],
    '时间线': ['2033', '年', '时代'],
}

print()
print("--- 关键词情感分布 ---")
for category, words in keywords.items():
    matches = [c for c in all_comments if any(w in c.get('content', '') for w in words)]
    print(f"  {category}: {len(matches)} 条评论涉及")

