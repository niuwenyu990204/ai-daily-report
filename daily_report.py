import os
import smtplib
import datetime
import requests
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
        url = "https://huggingface.co/api/models?sort=likes&direction=-1&limit=5"
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

from openai import OpenAI

def generate_smart_report(github_data, hn_data, hf_data):
    """使用 LLM 生成智能总结报告"""
    if not LLM_API_KEY:
        print("⚠️ 未配置 LLM_API_KEY，回退到普通模板模式")
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
    """
    
    system_prompt = """
    你是一个专业的 AI 科技媒体编辑。请根据提供的原始数据，写一份高质量的《AI 每日简报》。
    
    要求如下：
    1.  **语言风格：** 采用全中文口语化翻译，避免生硬的机器翻译痕迹，力求自然流畅。
    2.  **内容筛选：** 从提供的列表中筛选出最值得关注的 5-8 项，并按照其热度或重要性进行降序排列。
    3.  **项目分类：** 必须为每个项目明确标注其类型：
        *   **[开源程序]（需部署）：** 指需要用户自行下载代码、配置环境并部署才能使用的项目。
        *   **[在线工具]（开箱即用）：** 指可以直接通过网页访问或下载客户端即可使用的项目。
        *   **[行业新闻]：** 指与 AI 领域相关的最新动态、研究成果、政策发布等信息。
    4.  **结构统一：** 每个项目或新闻条目都应遵循以下格式（直接输出 HTML 格式）：
        
        <div class="item">
            <h3><a href="URL">项目名称</a> <span class="tag">[类型]</span></h3>
            <p><strong>一句话简介：</strong>...</p>
            <p><strong>核心价值：</strong>...</p>
            <p><strong>使用门槛：</strong>...</p>
        </div>

    5.  **输出格式：** 
        *   只输出 HTML 的 `<body>` 内部的核心内容（不需要 `<html>`, `<head>` 标签）。
        *   使用简单的 CSS class (如 .item, .tag) 以便渲染。
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
            max_tokens=2000
        )
        content = response.choices[0].message.content
        
        # 包装成完整的 HTML
        full_html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 650px; margin: 0 auto; padding: 20px; }}
                h1 {{ text-align: center; color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 20px; }}
                .item {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #e9ecef; }}
                .item h3 {{ margin-top: 0; color: #0366d6; }}
                .item a {{ color: #0366d6; text-decoration: none; }}
                .tag {{ background: #e1ecf4; color: #0366d6; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; margin-left: 10px; font-weight: normal; }}
                p {{ margin: 8px 0; }}
                strong {{ color: #495057; }}
                .footer {{ text-align: center; font-size: 0.8em; color: #999; margin-top: 40px; border-top: 1px solid #eee; padding-top: 20px; }}
            </style>
        </head>
        <body>
            <h1>🤖 AI 每日简报 ({datetime.date.today()})</h1>
            {content}
            <div class="footer">
                由 AI 自动生成 • {datetime.date.today()}
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
        normal_html = generate_html(github_data, hn_data, hf_data)
        
        # 将错误信息插入到 body 开始处
        return normal_html.replace("<body>", f"<body>{error_html}")

def generate_html(github_data, hn_data, hf_data):
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
            <h1>🤖 AI 每日简报 ({{ date }})</h1>
            
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
        hf_data=hf_data
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
    
    # 2. 生成报告
    # 尝试使用 LLM 生成智能报告，如果失败或未配置 Key 会自动回退
    html = generate_smart_report(github_data, hn_data, hf_data)
    
    # 3. 发送邮件
    send_email(html)
    
    print("🎉 任务完成！")

if __name__ == "__main__":
    main()
