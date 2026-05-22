#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import os
import re
from playwright.async_api import async_playwright

URL = "https://www.bilibili.com/video/BV1zUfxBQEsE?vd_source=d65c1d59e1b07770d5fd3f2d2b66a136"
USER_DATA_DIR = r"c:\Users\Zheng Yuxuan\Documents\trae_projects\reviewclean\review-local\browser_data\bili_user_data_dir"

async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=True,
            viewport={"width": 1440, "height": 1800},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.55)")
        await page.wait_for_timeout(4000)
        text = await page.evaluate("document.body.innerText")
        print("===== TITLE =====")
        print(await page.title())
        print("===== HAS LOGIN TEXT =====")
        print("登录" in text[:1000])
        print("===== COMMENT AREA SNIPPET =====")
        m = re.search(r"评论\s*\n.*?(?:没有更多评论|登录后查看.*?评论)", text, re.S)
        if m:
            snippet = m.group(0)
            print(snippet[:8000])
        else:
            print("NO_COMMENT_SNIPPET")
            print(text[:8000])
        await page.screenshot(path="inspect_bili_logged_in.png", full_page=True)
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
