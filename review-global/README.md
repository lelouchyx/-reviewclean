# ReviewClean

评论抓取、清洗与分析工具（Streamlit 前端），支持：

- 本地文件与网页评论采集
- YouTube 评论抓取（浏览器滚动 + API 回退）
- X/Twitter 评论抓取（登录态 API + 楼中楼递归）
- 文本归一化、TF-IDF、近重复去重
- 情绪/语气分析、词云与可视化
- 翻译、对照表与 CSV 导出

## 目录说明

- app.py: Streamlit 前端入口
- fetcher_youtube.py: YouTube 抓取模块（comment_fetcher.py 兼容入口）
- fetcher_steam.py: Steam 抓取模块（含追评/编辑标记）
- fetcher_x.py: X/Twitter 评论抓取模块
- comment_cleaner.py: 评论清洗与去重模块
- comment_analyzer.py: 评论分析模块
- comment_io.py: 输入读取工具

## 环境准备（Windows）

1. 创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. 安装依赖

```powershell
pip install streamlit pandas matplotlib wordcloud requests beautifulsoup4
pip install deep-translator selenium youtube-comment-downloader jieba nltk
```

说明：
- 第二行依赖为可选增强（翻译、YouTube 抓取、中文分词、英文词干）。
- 若不需要对应功能，可不安装。

## 运行

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\app.py --server.port 8501
```

浏览器打开：

- http://localhost:8501

## 命令行使用（可选）

抓取评论：

```powershell
.\.venv\Scripts\python.exe .\fetcher-youtube.py --source "https://www.youtube.com/watch?v=VIDEO_ID" --limit 200 --output fetched_comments.jsonl --text-output fetched_comments.txt
```

抓取 Steam 评论（含追评检测）：

```powershell
.\.venv\Scripts\python.exe .\fetcher-steam.py --source "https://steamcommunity.com/app/1260320/reviews/?browsefilter=trendweek&p=1&filterLanguage=default" --limit 200 --output steam_reviews.jsonl --text-output steam_reviews.txt
```

抓取 X/Twitter 评论：

程序会优先尝试自动读取项目内已保存的 X 登录态；如果当前帖子已有仓库内公开回复快照，则也可以直接复用快照完成分析。

也支持像 review-local 一样，先在项目自己的浏览器上下文里登录一次，再复用保存下来的登录态：

```powershell
python .\fetcher-x.py --login
```

执行后会打开一个浏览器窗口，在其中完成 X 登录；登录成功后，会话会保存到 `review-global/browser_data/x_user_data_dir`，后续抓取会自动复用。

如果自动读取失败，再在本地终端设置登录态环境变量：

```powershell
$env:X_COOKIE_HEADER = '从已登录 X Web 会话复制的 Cookie 请求头'
$env:X_CSRF_TOKEN = '可选；如果 X_COOKIE_HEADER 里已经带 ct0，可不单独设置'
```

```powershell
.\.venv\Scripts\python.exe .\fetcher-x.py --source "https://x.com/NTE_GL/status/2052327582277009536?s=20" --limit 50 --output x_comments.jsonl --text-output x_comments.txt
```

说明：
- 最完整的 X 评论抓取仍依赖已登录的 X Web 会话。
- 对于仓库已收录公开回复快照的帖子，程序会先复用本地快照；当前已内置 NTE 示例帖子 `2052327582277009536` 的公开回复快照。
- 也可以先执行 `python .\fetcher-x.py --login`，在项目自己的浏览器上下文里完成一次登录，后续无需再手填 Cookie。
- 程序会先尝试项目内保存的登录态，再尝试本地公开回复快照；若仍不可用，最后才回退到手动提供 X_COOKIE_HEADER / X_CSRF_TOKEN。
- 默认会抓取主帖下的顶层评论，并递归展开 1 层楼中楼。
- 可用 --max-reply-depth 控制递归深度，用 --no-expand-replies 只抓顶层评论。

清洗评论：

```powershell
.\.venv\Scripts\python.exe .\comment_cleaner.py --input fetched_comments.txt --output cleaned_report.json --clean-output cleaned_comments.txt
```

## 备注

- 当前仓库默认不提交本地产物（jsonl/txt、缓存、虚拟环境）。
- 文件 2 - 信息检索（中文）.md 已被排除，不会上传到仓库。
