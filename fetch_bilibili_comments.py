#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速脚本：从指定 B 站视频链接抓取评论（使用 B 站公开 API）
"""
import asyncio
import json
import httpx
import re

# B 站 API 相关
BILIBILI_API_HOST = "https://api.bilibili.com"
BILIBILI_VIDEO_REGEX = r"(?:BV|av)(\w+)"

async def convert_bv_to_av(bv_id: str) -> str:
    """将 BV 号转换为 AV 号"""
    # B 站 BV 到 AV 的转换算法（简化版）
    # 实际上可以通过 B 站的其他 API 获取 AV 号
    # 这里使用一个简单的方法：通过视频页面获取
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            url = f"https://www.bilibili.com/video/{bv_id}"
            resp = await client.get(url, follow_redirects=True)
            # 在页面中寻找 AV 号或直接获取视频信息
            # 从 URL 重定向中可能得到 AV 号
            if "av" in resp.url.path:
                av_match = re.search(r"av(\d+)", resp.url.path)
                if av_match:
                    return av_match.group(1)
            # 如果找不到，从页面内容中提取
            content = resp.text
            # 查找 __INITIAL_STATE__ 中的视频 ID
            match = re.search(r'"aid":(\d+)', content)
            if match:
                return match.group(1)
        except Exception as e:
            print(f"[WARNING] 转换 BV 号失败: {e}")
    
    # 如果都失败，返回 BV 号本身（API 可能支持）
    return bv_id

async def fetch_bilibili_comments(video_id: str, limit: int = 100):
    """
    从 B 站 API 获取视频评论
    
    Args:
        video_id: 视频 ID (支持 BV 号或 AV 号)
        limit: 要获取的评论数量
    
    Returns:
        评论列表
    """
    comments = []
    
    # 如果是 BV 号，需要转换为 AV 号
    if video_id.startswith("BV"):
        print(f"[INFO] 正在将 BV 号 {video_id} 转换为 AV 号...")
        av_id = await convert_bv_to_av(video_id)
        print(f"[INFO] 得到 AV/OID: {av_id}")
    else:
        av_id = video_id.replace("av", "")
    
    print(f"[INFO] 开始获取评论（OID: {av_id}）...")
    
    # B 站评论 API 端点
    api_url = f"{BILIBILI_API_HOST}/x/v2/reply/wbi/main"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }
    
    next_page = 0
    page_count = 0
    max_pages = (limit // 20) + 1  # 每页 20 条评论
    
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            while page_count < max_pages and len(comments) < limit:
                try:
                    # 构造请求参数
                    params = {
                        "oid": av_id,
                        "type": 1,  # 1 表示视频
                        "mode": 2,  # 2 表示最新评论
                        "ps": 20,   # 每页 20 条
                        "next": next_page,
                    }
                    
                    print(f"[INFO] 获取第 {page_count + 1} 页评论（from {next_page}）...")
                    
                    resp = await client.get(api_url, params=params)
                    
                    if resp.status_code != 200:
                        print(f"[WARNING] 请求失败，状态码: {resp.status_code}")
                        break
                    
                    data = resp.json()
                    
                    if data.get("code") != 0:
                        print(f"[WARNING] API 返回错误: {data.get('message')}")
                        break
                    
                    # 提取评论
                    reply_data = data.get("data", {})
                    page_replies = reply_data.get("replies", [])
                    
                    if not page_replies:
                        print(f"[INFO] 没有更多评论了")
                        break
                    
                    # 处理每条评论
                    for reply in page_replies:
                        if len(comments) >= limit:
                            break
                        
                        comment = {
                            'id': reply.get('rpid'),
                            'author': reply.get('member', {}).get('uname', '未知用户'),
                            'content': reply.get('content', {}).get('message', ''),
                            'likes': reply.get('like', 0),
                            'create_at': reply.get('ctime', 0),
                            'level': reply.get('member', {}).get('level_info', {}).get('current_level', 0),
                        }
                        comments.append(comment)
                    
                    print(f"[INFO] 已获取 {len(comments)} / {limit} 条评论")
                    
                    # 检查是否还有下一页
                    cursor = reply_data.get("cursor", {})
                    if cursor.get("is_end"):
                        print(f"[INFO] 已到达最后一页")
                        break
                    
                    next_page = cursor.get("next", 0)
                    page_count += 1
                    
                    # 避免频繁请求，加入延迟
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    print(f"[ERROR] 获取评论时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    break
    
    except Exception as e:
        print(f"[ERROR] API 请求失败: {e}")
    
    return comments

async def main():
    """主函数"""
    bv_id = "BV1zUfxBQEsE"
    
    print("=" * 60)
    print(f"B 站视频评论爬虫（直接 API 方式）")
    print(f"视频 BV ID: {bv_id}")
    print("=" * 60)
    print()
    
    # 抓取评论
    comments = await fetch_bilibili_comments(bv_id, limit=100)
    
    if comments:
        # 保存到 JSON 文件
        output_file = f"bilibili_comments_{bv_id}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(comments, f, ensure_ascii=False, indent=2)
        
        print()
        print(f"[INFO] 评论已保存到: {output_file}")
        print(f"[INFO] 共获取: {len(comments)} 条评论")
        
        # 显示前3条评论
        print("\n" + "=" * 60)
        print("前3条评论示例")
        print("=" * 60)
        for i, comment in enumerate(comments[:3], 1):
            print(f"\n{i}. {comment['author']} (点赞: {comment['likes']})")
            print(f"   {comment['content'][:80]}...")
    else:
        print("[ERROR] 没有获取到任何评论")

if __name__ == "__main__":
    asyncio.run(main())
