import os
import smtplib
import datetime
import requests
import feedparser
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Template

# --- 配置部分 ---
# 你可以在本地直接修改这里进行测试，或者设置环境变量
# 实际在 GitHub Actions 运行时，我们会使用环境变量
MAIL_HOST = os.environ.get("MAIL_HOST", "smtp.qq.com")  # 默认 QQ 邮箱
MAIL_PORT = int(os.environ.get("MAIL_PORT", 465))
MAIL_USER = os.environ.get("MAIL_USERNAME", "")       # 你的邮箱地址
MAIL_PASS = os.environ.get("MAIL_PASSWORD", "")       # 你的邮箱授权码
MAIL_RECEIVER = os.environ.get("MAIL_RECIPIENT", "")  # 接收报告的邮箱

# LLM 配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
# 使用 or 运算符处理空字符串的情况（GitHub Actions 可能会将未定义的 secret 设为空字符串）
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com"
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"

def fetch_github_trending():
    """获取 GitHub 上近期热门的 AI 相关项目"""
    print("正在获取 GitHub 热门项目...")
    # 使用 GitHub Search API 查找最近 7 天创建的、包含 ai/llm 标签且按星数排序的项目
    date_str = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    # 尝试最简单的 Query，先确保能通
    # query = f"topic:ai OR topic:llm OR topic:machine-learning created:>{date_str}"
    query = f"ai language:python created:>{date_str}"
    
    url = "https://api.github.com/search/repositories"
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": 10
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/vnd.github.v3+json"
    }
    # 如果有 GITHUB_TOKEN，添加到请求头中以提高速率限制
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        # 调试输出
        print(f"Debug - Request URL: {resp.url}")
        
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            return [
                {
                    "name": item["full_name"],
                    "desc": item["description"] or "暂无描述",
                    "stars": item["stargazers_count"],
                    "url": item["html_url"],
                    "language": item["language"] or "Unknown"
                }
                for item in items
            ]
        else:
            print(f"GitHub API 请求失败: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"GitHub 获取失败: {e}")
    return []

def fetch_hacker_news_ai():
    """获取 Hacker News 上热门的 AI 讨论"""
    print("正在获取 Hacker News AI 话题...")
    try:
        # 使用 Session 复用连接
        session = requests.Session()
        # 获取 Top Stories ID
        top_ids = session.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10).json()[:30] # 减少检查数量以加快速度
        ai_stories = []
        keywords = ["AI", "GPT", "LLM", "Diffusion", "Model", "Neural", "Transformer"]
        
        for hid in top_ids:
            try:
                item = session.get(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", timeout=5).json()
                if not item or "title" not in item:
                    continue
                
                title = item["title"]
                # 简单的关键词过滤
                if any(k.lower() in title.lower() for k in keywords):
                    ai_stories.append({
                        "title": title,
                        "url": item.get("url", f"https://news.ycombinator.com/item?id={hid}"),
                        "score": item.get("score", 0),
                        "comments": item.get("descendants", 0)
                    })
                    if len(ai_stories) >= 5: # 限制数量
                        break
            except Exception as e:
                print(f"Skipping HN item {hid}: {e}")
                continue
                
        return ai_stories
    except Exception as e:
        print(f"Hacker News 获取失败: {e}")
        return []

def fetch_huggingface_trending():
    """获取 Hugging Face 热门模型"""
    print("正在获取 Hugging Face 热门模型...")
    try:
        # Hugging Face API
        url = "https://huggingface.co/api/models?sort=likes&direction=-1&limit=15"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            models = resp.json()
            return [
                {
                    "name": m["modelId"],
                    "likes": m.get("likes", 0),
                    "url": f"https://huggingface.co/{m['modelId']}",
                    "tags": m.get("tags", [])[:3] # 只取前3个标签
                }
                for m in models
            ]
    except Exception as e:
        print(f"Hugging Face 获取失败: {e}")
        return []

import calendar

def fetch_rss_data(url, limit=5, hours=24):
    """通用 RSS 获取函数，支持时间筛选，使用 User-Agent 避免被拦截"""
    print(f"正在获取 RSS: {url} ...")
    try:
        # 1. 使用 requests 获取内容，设置 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        
        if resp.status_code != 200:
            print(f"RSS 请求失败: {resp.status_code}")
            return []
            
        # 2. 解析内容
        feed = feedparser.parse(resp.content)
        items = []
        current_time = time.time() # UTC 时间戳 (seconds since epoch)
        
        for entry in feed.entries:
            # 尝试获取发布时间
            published_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if not published_time:
                continue
                
            # 转换为 UTC 时间戳
            # feedparser 的 published_parsed 通常是 UTC 时间的 struct_time
            # 使用 calendar.timegm 转换为时间戳
            entry_time = calendar.timegm(published_time)
            
            # 筛选最近 N 小时
            if current_time - entry_time < hours * 3600:
                items.append({
                    "title": entry.title,
                    "link": entry.link,
                    "published": time.strftime("%Y-%m-%d %H:%M", published_time),
                    "summary": entry.get("summary", "")[:200] + "..." # 截断摘要
                })
                
            if len(items) >= limit:
                break
        
        print(f"成功获取 {len(items)} 条 RSS 数据 ({url})")        
        return items
    except Exception as e:
        print(f"RSS 获取失败 ({url}): {e}")
        return []

def fetch_crypto_news():
    """获取币圈新闻 (CoinDesk)"""
    return fetch_rss_data("https://www.coindesk.com/arc/outboundfeeds/rss/", limit=10)

def fetch_macro_news():
    """获取宏观经济新闻 (CNBC)"""
    # CNBC Finance
    return fetch_rss_data("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664", limit=10)

from openai import OpenAI

def generate_smart_report(github_data, hn_data, hf_data, crypto_data, macro_data):
    """使用 LLM 生成智能总结报告"""
    if not LLM_API_KEY:
        print("⚠️ 未配置 LLM_API_KEY，回退到普通模板模式")
        # 暂时只传递前三个参数给普通模板，避免报错
        return generate_html(github_data, hn_data, hf_data)
        
    print("🤖 正在调用 LLM 进行智能总结与分析...")
    # 打印调试信息（脱敏）
    safe_key = LLM_API_KEY[:6] + "*" * 4 + LLM_API_KEY[-4:] if len(LLM_API_KEY) > 10 else "******"
    print(f"Debug Info: BaseURL={LLM_BASE_URL}, Model={LLM_MODEL}, Key={safe_key}")
    
    # 构造 Prompt
    data_summary = f"""
    GitHub Trending:
    {str(github_data)}
    
    Hacker News AI Topics:
    {str(hn_data)}
    
    Hugging Face Trending:
    {str(hf_data)}

    Crypto News (Latest 24h):
    {str(crypto_data)}

    Macroeconomic News (Latest 24h):
    {str(macro_data)}
    """
    
    system_prompt = """
    你是一名资深的 **全栈科技与金融分析师**。请基于最近 24–48 小时内的公开信息，整理一份高质量的 **《AI & 金融前沿日报》**。
    受众为：关注前沿 AI 工具、加密货币市场及宏观经济的开发者、创业者和投资者。

    **信息来源与板块结构**
    请将提供的数据按以下五大板块重组（如果某板块无数据，可跳过）：
    1.  **GitHub 热门 AI 项目**（筛选最具创新性的开源项目）
    2.  **Hacker News AI 热议**（筛选最有深度的技术讨论）
    3.  **Hugging Face 热门模型**（筛选最实用的新模型）
    4.  **币圈动态 (Crypto Watch)**（筛选对比特币、以太坊或 Web3 行业有重大影响的新闻）
    5.  **宏观经济 (Macro Insights)**（筛选可能影响科技股或风险资产的全球经济/政策新闻）

    **输出要求**
    1.  **HTML 表格格式：** 每个板块输出一个紧凑的 HTML 表格（`<table>`）。
    2.  **Top 5：** 每个表格仅保留 **Top 3-5** 最具价值的条目。
    3.  **表格列名固定为：**
        *   **名称 / 链接** (Name/Link)
        *   **分类** (Category: 工具/新闻/政策/行情)
        *   **核心解读** (Analysis: 用大白话解释它为什么重要，对未来的潜在影响)
        *   **关注指数** (Impact: ⭐⭐⭐ - ⭐⭐⭐⭐⭐)
    4.  **写作风格：** 专业、犀利、简练。拒绝通稿式废话，直击要害。

    **总结要求**
    在所有表格之后，增加 **「今日风向标」** 栏目：
    *   **一句话总结：** 用一句话概括今天科技与金融市场的整体情绪（如：AI 应用层爆发，但宏观政策收紧导致币圈承压）。
    *   **最不容错过：** 挑选 **唯一一项** 今日最重要的信息，并给出深度推荐理由。

    **输出格式**
    *   只输出 HTML 的 `<body>` 内部的核心内容。
    *   使用简单的 CSS class 以便渲染（如 `table`, `th`, `td`）。
    """
    
    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"这是今天的原始数据，请开始生成：\n{data_summary}"}
            ],
            temperature=0.7,
            max_tokens=3000
        )
        content = response.choices[0].message.content
        
        # 包装成完整的 HTML
        full_html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 900px; margin: 0 auto; padding: 20px; }}
                h1 {{ text-align: center; color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 20px; }}
                h2 {{ margin-top: 30px; color: #0366d6; border-left: 5px solid #0366d6; padding-left: 10px; font-size: 1.4em; }}
                table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 0.9em; }}
                th, td {{ padding: 12px; border: 1px solid #e1e4e8; text-align: left; vertical-align: top; }}
                th {{ background-color: #f6f8fa; font-weight: 600; color: #24292e; }}
                tr:nth-child(even) {{ background-color: #f8f9fa; }}
                a {{ color: #0366d6; text-decoration: none; font-weight: bold; }}
                .tag {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; font-weight: normal; background-color: #e1ecf4; color: #0366d6; }}
                .highlight-box {{ background-color: #fff8c5; border: 1px solid #d3c875; padding: 20px; border-radius: 6px; margin-top: 40px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
                .highlight-title {{ font-weight: bold; color: #735c0f; margin-bottom: 10px; font-size: 1.2em; border-bottom: 1px solid #eadd86; padding-bottom: 5px; }}
                .footer {{ text-align: center; font-size: 0.8em; color: #999; margin-top: 40px; border-top: 1px solid #eee; padding-top: 20px; }}
            </style>
        </head>
        <body>
            <h1>🚀 AI & 金融前沿日报 ({datetime.date.today()})</h1>
            {content}
            <div class="footer">
                由 AI 自动生成 • 数据来源：GitHub, Hacker News, Hugging Face, CoinDesk, CNBC
            </div>
        </body>
        </html>
        """
        return full_html
        
    except Exception as e:
        print(f"❌ LLM 生成失败: {e}")
        print("🔄 回退到普通模板模式...")
        
        # 将错误信息注入到普通模板中，方便用户在邮件中直接看到原因
        error_html = f"""
        <div style="background-color: #fee; border: 1px solid #f00; padding: 15px; margin-bottom: 20px; border-radius: 5px; color: #c00;">
            <h3>⚠️ 智能日报生成失败</h3>
            <p><strong>错误信息：</strong> {str(e)}</p>
            <p><strong>Debug Info:</strong> BaseURL={LLM_BASE_URL}, Model={LLM_MODEL}, Key={safe_key}</p>
            <p>请检查 GitHub Secrets 中的 LLM_API_KEY 配置。</p>
        </div>
        """
        
        # 生成普通报告
        normal_html = generate_html(github_data, hn_data, hf_data, crypto_data, macro_data)
        
        # 将错误信息插入到 body 开始处
        return normal_html.replace("<body>", f"<body>{error_html}")

def generate_html(github_data, hn_data, hf_data, crypto_data=None, macro_data=None):
    """生成 HTML 邮件内容"""
    template_str = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
            .container { max-width: 600px; margin: 0 auto; padding: 20px; }
            h2 { color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 30px; }
            .item { margin-bottom: 15px; }
            .item a { color: #0366d6; text-decoration: none; font-weight: bold; }
            .meta { font-size: 0.85em; color: #666; }
            .footer { margin-top: 40px; font-size: 0.8em; color: #999; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 AI & 金融每日简报 ({{ date }})</h1>
            
            <h2>🔥 GitHub 本周热门 AI 项目</h2>
            {% if github_data %}
                {% for item in github_data %}
                <div class="item">
                    <a href="{{ item.url }}">{{ item.name }}</a> 
                    <span class="meta">⭐ {{ item.stars }} | {{ item.language }}</span>
                    <div style="font-size: 0.9em;">{{ item.desc }}</div>
                </div>
                {% endfor %}
            {% else %}
                <p>获取失败或无数据。</p>
            {% endif %}

            <h2>📰 Hacker News 热议</h2>
            {% if hn_data %}
                {% for item in hn_data %}
                <div class="item">
                    <a href="{{ item.url }}">{{ item.title }}</a>
                    <div class="meta">⬆️ {{ item.score }} | 💬 {{ item.comments }} comments</div>
                </div>
                {% endfor %}
            {% else %}
                <p>暂无相关 AI 热门讨论。</p>
            {% endif %}

            <h2>🤗 Hugging Face 热门模型</h2>
            {% if hf_data %}
                {% for item in hf_data %}
                <div class="item">
                    <a href="{{ item.url }}">{{ item.name }}</a>
                    <span class="meta">❤️ {{ item.likes }}</span>
                    <div class="meta">Tags: {{ item.tags | join(', ') }}</div>
                </div>
                {% endfor %}
            {% else %}
                <p>获取失败或无数据。</p>
            {% endif %}

            <h2>💰 币圈动态 (Crypto Watch)</h2>
            {% if crypto_data %}
                {% for item in crypto_data %}
                <div class="item">
                    <a href="{{ item.link }}">{{ item.title }}</a>
                    <div class="meta">🕒 {{ item.published }}</div>
                    <div style="font-size: 0.9em; color: #555;">{{ item.summary }}</div>
                </div>
                {% endfor %}
            {% else %}
                <p>暂无数据。</p>
            {% endif %}

            <h2>🌍 宏观经济 (Macro Insights)</h2>
            {% if macro_data %}
                {% for item in macro_data %}
                <div class="item">
                    <a href="{{ item.link }}">{{ item.title }}</a>
                    <div class="meta">🕒 {{ item.published }}</div>
                    <div style="font-size: 0.9em; color: #555;">{{ item.summary }}</div>
                </div>
                {% endfor %}
            {% else %}
                <p>暂无数据。</p>
            {% endif %}

            <div class="footer">
                此报告由 GitHub Actions 自动生成。<br>
                {{ date }}
            </div>
        </div>
    </body>
    </html>
    """
    template = Template(template_str)
    return template.render(
        date=datetime.date.today().strftime("%Y-%m-%d"),
        github_data=github_data,
        hn_data=hn_data,
        hf_data=hf_data,
        crypto_data=crypto_data,
        macro_data=macro_data
    )

def send_email(html_content):
    """发送邮件"""
    if not MAIL_USER or not MAIL_PASS or not MAIL_RECEIVER:
        print("❌ 邮件配置不完整，跳过发送步骤。请检查环境变量。")
        # 将 HTML 保存到本地文件以便预览
        with open("report_preview.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("✅ 已生成预览文件: report_preview.html")
        return

    msg = MIMEMultipart()
    msg['From'] = MAIL_USER
    msg['To'] = MAIL_RECEIVER
    msg['Subject'] = f"AI Daily Report - {datetime.date.today()}"
    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT)
        server.login(MAIL_USER, MAIL_PASS)
        server.sendmail(MAIL_USER, [MAIL_RECEIVER], msg.as_string())
        server.quit()
        print("✅ 邮件发送成功！")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

def main():
    print("🚀 开始执行 AI 日报生成任务...")
    
    # 1. 获取数据
    github_data = fetch_github_trending()
    hn_data = fetch_hacker_news_ai()
    hf_data = fetch_huggingface_trending()
    crypto_data = fetch_crypto_news()
    macro_data = fetch_macro_news()
    
    # 2. 生成报告
    # 尝试使用 LLM 生成智能报告，如果失败或未配置 Key 会自动回退
    html = generate_smart_report(github_data, hn_data, hf_data, crypto_data, macro_data)
    
    # 3. 发送邮件
    send_email(html)
    
    print("🎉 任务完成！")

if __name__ == "__main__":
    main()
