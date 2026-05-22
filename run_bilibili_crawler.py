#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B 站评论直接抓取脚本：绕过 main.py 的多平台导入问题
"""
import sys
import os
import asyncio
import io

# 解决编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 将 review-local 加入路径并切换工作目录
review_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review-local')
sys.path.insert(0, review_local_path)
os.chdir(review_local_path)

# 设置配置，必须在导入其他模块之前
import config
config.PLATFORM = "bili"
config.CRAWLER_TYPE = "detail"
config.HEADLESS = False  # 显示浏览器（用于扫码）
config.ENABLE_CDP_MODE = False
config.SAVE_DATA_OPTION = "jsonl"
config.SAVE_LOGIN_STATE = True
config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 200

from config import bilibili_config
bilibili_config.BILI_SPECIFIED_ID_LIST = ["BV1zUfxBQEsE"]

from var import crawler_type_var
crawler_type_var.set("detail")

# 仅导入 B 站相关模块
from media_platform.bilibili import BilibiliCrawler

async def main():
    """运行 B 站评论爬虫"""
    print("=" * 60)
    print("B 站视频评论爬虫")
    print(f"目标视频: BV1zUfxBQEsE")
    print(f"最大评论数: {config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES}")
    print("=" * 60)
    print()
    print("[INFO] 如需扫码登录，请在弹出的浏览器中完成操作")
    print("[INFO] 如已有保存的登录状态，将自动跳过扫码步骤")
    print()
    
    crawler = BilibiliCrawler()
    await crawler.start()
    
    print("\n[INFO] 爬虫运行完成")
    print(f"[INFO] 数据已保存至 data/bili/jsonl/ 目录")

if __name__ == "__main__":
    asyncio.run(main())
