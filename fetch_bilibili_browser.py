#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用浏览器自动化从 B 站视频页面抓取评论（网络请求拦截方式）
"""
import asyncio
import json
import re
from playwright.async_api import async_playwright

async def fetch_bilibili_comments_browser(bv_id: str, limit: int = 100):
    """
    使用 Playwright 网络请求拦截从 B 站获取评论
    
    Args:
        bv_id: 视频 BV 号
        limit: 要获取的评论数量
    
    Returns:
        评论列表
    """
    
    all_comments = []
    captured_responses = []
    
    try:
        async with async_playwright() as p:
            print("[INFO] 启动浏览器...")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            page = await context.new_page()
            
            # 拦截 B 站评论 API 请求
            async def handle_response(response):
                url = response.url
                if "reply" in url and "bilibili.com" in url:
                    try:
                        data = await response.json()
                        if data.get("code") == 0 and data.get("data"):
                            replies = data["data"].get("replies") or data["data"].get("data", {}).get("replies", [])
                            if replies:
                                print(f"[INFO] 捕获到 API 响应，包含 {len(replies)} 条评论")
                                captured_responses.append(replies)
                    except:
                        pass
            
            page.on("response", handle_response)
            
            # 打开 B 站视频页面
            video_url = f"https://www.bilibili.com/video/{bv_id}"
            print(f"[INFO] 打开视频页面: {video_url}")
            await page.goto(video_url, wait_until="domcontentloaded")
            
            print("[INFO] 等待页面加载...")
            await asyncio.sleep(5)
            
            # 多次滚动触发评论区加载
            print("[INFO] 滚动页面触发评论加载...")
            prev_count = 0
            stall_count = 0
            for i in range(20):
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                await asyncio.sleep(2)
                current_count = sum(len(r) for r in captured_responses)
                if current_count > prev_count:
                    stall_count = 0
                    prev_count = current_count
                else:
                    stall_count += 1
                print(f"[INFO] 已滚动 {i+1}/20 次，已捕获 {current_count} 条评论...")
                if len(all_comments) >= limit:
                    break
                if stall_count >= 4:
                    print("[INFO] 评论加载停滞，尝试点击'加载更多'...")
                    try:
                        load_more = page.locator("text=加载更多, text=展开评论, text=查看更多")
                        await load_more.first.click(timeout=2000)
                        await asyncio.sleep(2)
                        stall_count = 0
                    except:
                        pass
            
            await asyncio.sleep(3)
            
            print(f"[INFO] 共捕获 {len(captured_responses)} 批评论数据")
            
            # 整合所有评论
            seen_ids = set()
            for replies in captured_responses:
                for reply in replies:
                    rid = reply.get("rpid")
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        all_comments.append({
                            'id': rid,
                            'author': reply.get('member', {}).get('uname', '未知用户'),
                            'content': reply.get('content', {}).get('message', ''),
                            'likes': reply.get('like', 0),
                            'create_at': reply.get('ctime', 0),
                        })
            
            await browser.close()
    
    except Exception as e:
        print(f"[ERROR] 浏览器自动化错误: {e}")
        import traceback
        traceback.print_exc()
    
    return all_comments[:limit]

async def main():
    """主函数"""
    bv_id = "BV1zUfxBQEsE"
    
    print("=" * 60)
    print(f"B 站视频评论爬虫（网络请求拦截方式）")
    print(f"视频 BV ID: {bv_id}")
    print("=" * 60)
    print()
    
    comments = await fetch_bilibili_comments_browser(bv_id, limit=100)
    
    if comments:
        output_file = f"bilibili_comments_{bv_id}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(comments, f, ensure_ascii=False, indent=2)
        
        print()
        print(f"[INFO] 评论已保存到: {output_file}")
        print(f"[INFO] 共获取: {len(comments)} 条评论")
        
        print("\n" + "=" * 60)
        print("前5条评论示例")
        print("=" * 60)
        for i, comment in enumerate(comments[:5], 1):
            print(f"\n{i}. {comment['author']} (点赞: {comment['likes']})")
            content = comment['content']
            print(f"   {content[:100]}{'...' if len(content) > 100 else ''}")
    else:
        print("[WARNING] 没有获取到任何评论")
        print("[HINT] 可能原因：B站需要登录、或页面加载超时")

if __name__ == "__main__":
    asyncio.run(main())
