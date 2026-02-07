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

def fetch_github_trending():
    """获取 GitHub 上近期热门的 AI 相关项目"""
    print("正在获取 GitHub 热门项目...")
    # 使用 GitHub Search API 查找最近 7 天创建的、包含 ai/llm 标签且按星数排序的项目
    date_str = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    query = f"topic:ai OR topic:llm OR topic:machine-learning created:>{date_str}"
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=10"
    
    try:
        resp = requests.get(url, timeout=10)
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
    html = generate_html(github_data, hn_data, hf_data)
    
    # 3. 发送邮件
    send_email(html)
    
    print("🎉 任务完成！")

if __name__ == "__main__":
    main()
