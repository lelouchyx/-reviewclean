# ReviewClean Workspace

一个面向评论抓取、清洗和分析的工作区仓库，当前主要封装了两个方向的成果：

- review-global：当前主应用。基于 Streamlit，支持 X、Steam、YouTube 和本地文件的评论抓取、清洗、分析与可视化。
- 根目录 B 站辅助脚本：用于登录态抓取、导出 CSV、生成摘要等一次性工具。

## 当前推荐入口

主应用入口位于 review-global：

```powershell
cd review-global
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install streamlit pandas matplotlib wordcloud requests beautifulsoup4
pip install deep-translator selenium youtube-comment-downloader jieba nltk playwright browser-cookie3
.\.venv\Scripts\python.exe -m streamlit run .\app.py --server.port 8501
```

浏览器打开：

- http://127.0.0.1:8501

## 仓库结构

- review-global/: 主项目代码与 README
- fetch_bilibili_comments.py: B 站评论抓取辅助脚本
- fetch_bilibili_browser.py: B 站浏览器态抓取辅助脚本
- export_bili_comments_csv.py: B 站评论导出 CSV
- run_bilibili_crawler.py: B 站抓取入口脚本

## X/Twitter 说明

review-global 当前的 X 抓取策略按优先级如下：

1. 复用项目内已保存的 X 登录态
2. 复用仓库内已收录的公开回复快照
3. 必要时再弹出本机 Microsoft Edge 完成登录

仓库当前已收录一份公开回复快照，用于稳定复现 NTE 这条帖子分析流程：

- review-global/browser_data/x_public_reply_snapshots/2052327582277009536.jsonl

## GitHub 打包约定

- 不提交本机登录态、浏览器用户目录、虚拟环境、缓存目录
- 保留一份经过验证的 X 公开回复快照，便于在无登录态时复现分析流程
- 不提交一次性抓取产物和本地测试输出