# ReviewClean

评论抓取、清洗与分析工具（Streamlit 前端），支持：

- 本地文件与网页评论采集
- YouTube 评论抓取（浏览器滚动 + API 回退）
- 文本归一化、TF-IDF、近重复去重
- 情绪/语气分析、词云与可视化
- 翻译、对照表与 CSV 导出

## 目录说明

- app.py: Streamlit 前端入口
- comment_fetcher.py: 评论抓取模块
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
.\.venv\Scripts\python.exe .\comment_fetcher.py --source "https://www.youtube.com/watch?v=VIDEO_ID" --limit 200 --output fetched_comments.jsonl --text-output fetched_comments.txt
```

清洗评论：

```powershell
.\.venv\Scripts\python.exe .\comment_cleaner.py --input fetched_comments.txt --output cleaned_report.json --clean-output cleaned_comments.txt
```

## 备注

- 当前仓库默认不提交本地产物（jsonl/txt、缓存、虚拟环境）。
- 文件 2 - 信息检索（中文）.md 已被排除，不会上传到仓库。
