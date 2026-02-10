
import os
import smtplib
import datetime
import requests
import feedparser
import time
import calendar
import io
import json
import base64
import yfinance as yf
# Remove Matplotlib imports
# import matplotlib.pyplot as plt
# import matplotlib.dates as mdates
# from matplotlib.gridspec import GridSpec
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from jinja2 import Template
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import urllib3
import ssl
from openai import OpenAI

# 禁用不安全请求警告 (针对本地 SSL 问题)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# 全局禁用 SSL 验证 (解决 yfinance/curl 77 错误)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# 解决 CURL 证书问题 (针对 Windows 中文路径 + curl_cffi)
try:
    import curl_cffi.requests
    original_init = curl_cffi.requests.Session.__init__
    def new_init(self, *args, **kwargs):
        kwargs['verify'] = False
        original_init(self, *args, **kwargs)
    curl_cffi.requests.Session.__init__ = new_init
    print("Applied curl_cffi SSL patch")
except ImportError:
    pass

# 加载 .env 文件（如果存在）
load_dotenv()

# --- 配置部分 ---
MAIL_HOST = os.environ.get("MAIL_HOST", "smtp.qq.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", 465))
MAIL_USER = os.environ.get("MAIL_USERNAME", "")
MAIL_PASS = os.environ.get("MAIL_PASSWORD", "")
MAIL_RECEIVER = os.environ.get("MAIL_RECIPIENT", "")

# LLM 配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com"
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"

# 历史数据文件
HISTORY_FILE = "indicator_history.json"

class IndicatorMonitor:
    def __init__(self):
        self.data_summary = {} 
        self.history = self.load_history()

    def load_history(self):
        """加载本地历史数据"""
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading history: {e}")
        return {}

    def save_history(self):
        """保存历史数据"""
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def update_history(self, key, value):
        """更新特定指标的历史数据 (按日期)"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        if key not in self.history:
            self.history[key] = {}
        self.history[key][today] = value
        self.save_history()

    def fetch_market_data(self):
        """获取 DXY, US10Y, BTC, ETH, GOLD 等数据"""
        print("正在获取市场指标数据...")
        tickers = {
            "DXY": "DX-Y.NYB",
            "US10Y": "^TNX",
            "BTC": "BTC-USD",
            "ETH": "ETH-USD",
            "GOLD": "GC=F"
        }
        
        try:
            data = yf.download(list(tickers.values()), period="1mo", interval="1d", progress=False)
            
            close_data = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data
            
            summary = {}
            for name, ticker in tickers.items():
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in close_data.columns: continue
                    series = close_data[ticker].dropna()
                else:
                    series = close_data.dropna()

                if not series.empty:
                    current = series.iloc[-1]
                    prev = series.iloc[-2] if len(series) > 1 else current
                    change = ((current - prev) / prev) * 100
                    summary[name] = {
                        "price": current,
                        "change": change
                    }
            
            self.data_summary.update(summary)
            return data
        except Exception as e:
            print(f"Error fetching market data: {e}")
            return None

    def fetch_farside_flow(self):
        """获取 Farside Bitcoin ETF 净流入数据"""
        print("正在获取 ETF 流入数据 (Farside)...")
        url = "https://farside.co.uk/btc/"
        try:
            # impersonate chrome
            response = curl_cffi.requests.get(url, impersonate="chrome120", timeout=15, verify=False)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                tables = soup.find_all('table')
                target_table = None
                for table in tables:
                    text = table.get_text()
                    if "IBIT" in text and "FBTC" in text:
                        target_table = table
                        break
                
                if target_table:
                    rows = target_table.find_all('tr')
                    for row in reversed(rows):
                        cols = row.find_all(['td', 'th'])
                        data = [ele.get_text(strip=True) for ele in cols]
                        if not data: continue
                        if data[0] in ["Total", "Average", "Maximum", "Minimum"]: continue
                        
                        # Found valid row
                        date_str = data[0]
                        # Assuming last column is Total. 
                        # Based on test: ['', '', ..., 'Total'] mapped to values
                        # But sometimes headers are tricky. Usually last column is total.
                        total_flow = data[-1]
                        
                        self.data_summary['ETF_Flow'] = {
                            "date": date_str,
                            "value": total_flow
                        }
                        print(f"ETF Flow ({date_str}): {total_flow}M")
                        break
            else:
                print(f"Farside HTTP {response.status_code}")
        except Exception as e:
            print(f"Error fetching ETF data: {e}")

    def fetch_whale_count(self):
        """抓取 >1000 BTC 地址数量"""
        print("正在获取巨鲸地址数据...")
        url = "https://bitinfocharts.com/top-100-richest-bitcoin-addresses.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        try:
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table')
            found = False
            for table in tables:
                if "Addresses" in table.get_text() and "1,000" in table.get_text():
                    rows = table.find_all('tr')
                    for row in rows:
                        cols = row.find_all('td')
                        if not cols: continue
                        label = cols[0].get_text(strip=True)
                        if any(x in label for x in ["1,000 - 10,000", "10,000 - 100,000", "100,000 - 1,000,000"]):
                            count_str = cols[1].get_text(strip=True).split(' ')[0].replace(',', '')
                            if count_str.isdigit():
                                current_val = int(count_str)
                                self.data_summary['Whales'] = current_val
                                self.update_history('whales', current_val)
                                found = True
                                
                                # Check 24h change logic if we have history
                                dates = sorted(self.history.get('whales', {}).keys())
                                if len(dates) >= 2:
                                    prev_date = dates[-2]
                                    prev_val = self.history['whales'][prev_date]
                                    diff = current_val - prev_val
                                    self.data_summary['Whales_Change_24h'] = diff
                                    print(f"巨鲸地址数: {current_val} (Change: {diff})")
                                else:
                                    print(f"巨鲸地址数: {current_val}")
                    break
        except Exception as e:
            print(f"Error fetching whale data: {e}")

    def fetch_fear_greed(self):
        """获取恐慌贪婪指数"""
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            data = r.json()
            if data['data']:
                item = data['data'][0]
                self.data_summary['FearGreed'] = {
                    "value": int(item['value']),
                    "label": item['value_classification']
                }
        except Exception as e:
            print(f"Error fetching Fear & Greed: {e}")

    def fetch_derivatives_data(self):
        """获取资金费率和持仓量 (Source: Bybit)"""
        print("正在获取衍生品数据 (Bybit)...")
        try:
            import curl_cffi.requests
            # Bybit V5 Market Ticker
            url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
            
            r = curl_cffi.requests.get(
                url, 
                impersonate="chrome120", 
                timeout=10, 
                verify=False
            )
            
            if r.status_code == 200:
                data = r.json()
                if 'result' in data and 'list' in data['result'] and len(data['result']['list']) > 0:
                    item = data['result']['list'][0]
                    
                    # Funding Rate
                    fr_raw = float(item.get('fundingRate', 0))
                    fr_percent = fr_raw * 100
                    self.data_summary['FundingRate'] = fr_percent
                    
                    # Status
                    status = "🟢 健康"
                    if fr_percent > 0.03: status = "🔴 过热"
                    elif fr_percent < -0.005: status = "🔵 看空"
                    self.data_summary['FundingRate_Status'] = status
                    print(f"资金费率: {fr_percent:.4f}% ({status})")
                    
                    # Open Interest
                    oi_val = float(item.get('openInterestValue', 0))
                    oi_billion = oi_val / 1e9
                    self.data_summary['OpenInterest'] = oi_billion
                    print(f"持仓量: ${oi_billion:.2f}B")
                else:
                    print("Bybit data format unexpected")
            else:
                print(f"Bybit API Error: {r.status_code}")
                
        except Exception as e:
            print(f"Error fetching Derivatives data: {e}")

    def fetch_defi_data(self):
        """获取 DefiLlama 稳定币数据"""
        print("正在获取稳定币市值...")
        try:
            url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
            r = requests.get(url, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                total_mcap = 0
                for coin in data['peggedAssets']:
                    if isinstance(coin, dict) and 'circulating' in coin:
                         total_mcap += coin['circulating'].get('peggedUSD', 0)
                
                # 转换为 Billion
                mcap_b = total_mcap / 1e9
                self.data_summary["Stablecoin_Mcap"] = mcap_b
                self.update_history('stablecoins', mcap_b)
                print(f"稳定币市值: ${mcap_b:.2f}B")
        except Exception as e:
            print(f"DefiLlama 获取失败: {e}")

    def run(self):
        self.fetch_market_data()
        self.fetch_farside_flow()
        self.fetch_whale_count()
        self.fetch_fear_greed()
        self.fetch_derivatives_data()
        self.fetch_defi_data()
        return self.data_summary

def fetch_github_trending():
    """GitHub: topic:ai, stars:>500, weekly"""
    print("正在获取 GitHub 热门 AI 项目...")
    date_str = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    # Query updated to topic:ai and stars:>500 as requested
    query = f"topic:ai stars:>500 created:>{date_str}"
    
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": 5}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github.v3+json"
    }
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
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
    """获取 HN TopStories 中关于 AI 的讨论 (Filtered by Score)"""
    print("正在获取 Hacker News AI 话题 (Top/Best)...")
    try:
        session = requests.Session()
        # 仅扫描 TopStories 和 BestStories，确保热度
        # NewStories 会包含大量 0-2 分的新帖，故移除
        endpoints = [
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            "https://hacker-news.firebaseio.com/v0/beststories.json"
        ]
        
        candidates = []
        for ep in endpoints:
            try:
                # 获取前 50 条，确保覆盖面
                ids = session.get(ep, timeout=5).json()[:50]
                candidates.extend(ids)
            except: pass
            
        # 去重
        candidates = list(set(candidates))
            
        ai_stories = []
        keywords = ["AI", "GPT", "LLM", "Diffusion", "Model", "Neural", "Transformer", "Agent", "RAG", "DeepSeek", "OpenAI"]
        
        for hid in candidates[:80]: # 限制请求次数
            try:
                item = session.get(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", timeout=3).json()
                if not item or "title" not in item: continue
                
                # 过滤掉低热度帖子 (Score < 50)
                score = item.get("score", 0)
                if score < 50:
                    continue

                title = item["title"]
                if any(k.lower() in title.lower() for k in keywords):
                    ai_stories.append({
                        "title": title,
                        "url": item.get("url", f"https://news.ycombinator.com/item?id={hid}"),
                        "score": score,
                        "type": item.get("type", "story")
                    })
                    if len(ai_stories) >= 5: break
            except: continue
        return ai_stories
    except Exception as e:
        print(f"Hacker News 获取失败: {e}")
        return []

def fetch_huggingface_trending():
    """
    获取 Hugging Face Daily Papers (优先) 和 Trending Models
    策略：优先展示每日论文 (绝对新鲜)，辅以热门模型
    """
    print("正在获取 Hugging Face 热门内容...")
    data_summary = {"papers": [], "models": []}
    
    # 1. Fetch Daily Papers (Scraping)
    try:
        url = "https://huggingface.co/papers"
        r = curl_cffi.requests.get(url, impersonate="chrome120", verify=False, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            articles = soup.find_all('article')
            papers = []
            for art in articles[:5]: # Top 5 papers
                title_tag = art.find('h3')
                if title_tag:
                    title = title_tag.get_text(strip=True)
                    link = "https://huggingface.co" + art.find('a')['href'] if art.find('a') else ""
                    # Try to find upvotes/likes
                    likes_div = art.find('div', class_='leading-none')
                    likes = likes_div.get_text(strip=True) if likes_div else "N/A"
                    
                    papers.append({
                        "title": title,
                        "url": link,
                        "likes": likes,
                        "type": "Paper"
                    })
            data_summary["papers"] = papers
    except Exception as e:
        print(f"HF Papers error: {e}")

    # 2. Fetch Trending Models (API: sort=likes7d to ensure freshness)
    # Using curl_cffi to avoid 400/403
    try:
        # sort=likes7d gets most liked in last 7 days (fresh!)
        url = "https://huggingface.co/api/models?sort=likes7d&limit=5"
        r = curl_cffi.requests.get(url, impersonate="chrome120", verify=False, timeout=10)
        if r.status_code == 200:
            models = r.json()
            model_list = []
            for m in models:
                # Filter out very old models if possible, but likes7d usually handles this.
                # We can check 'createdAt' if the API returns it, but it might not in the summary.
                model_list.append({
                    "name": m["modelId"],
                    "likes_7d": m.get("likes", 0), # In this sort, likes might be total or 7d? API isn't always clear, but the rank is correct.
                    "url": f"https://huggingface.co/{m['modelId']}",
                    "tags": m.get("tags", [])[:3]
                })
            data_summary["models"] = model_list
    except Exception as e:
        print(f"HF Models error: {e}")

    return data_summary

def fetch_rss_data(url, limit=10):
    print(f"正在获取 RSS: {url} ...")
    try:
        feed = feedparser.parse(url)
        items = []
        now = datetime.datetime.now(datetime.timezone.utc)
        
        for entry in feed.entries:
            # Calculate Time
            published_time = None
            time_label = "最新"
            
            try:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_time = datetime.datetime.fromtimestamp(calendar.timegm(entry.published_parsed), datetime.timezone.utc)
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    published_time = datetime.datetime.fromtimestamp(calendar.timegm(entry.updated_parsed), datetime.timezone.utc)
                
                if published_time:
                    diff = now - published_time
                    seconds = diff.total_seconds()
                    
                    if seconds > 86400: # Skip older than 24h
                        continue
                        
                    hours = int(seconds / 3600)
                    if hours < 1:
                        time_label = "极新 <1小时"
                    elif hours < 12:
                        time_label = f"极新 {hours}小时前"
                    else:
                        time_label = "最新 昨日"
            except Exception as e:
                pass

            items.append({
                "title": entry.title, 
                "link": entry.link, 
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "time_label": time_label
            })
            
            if len(items) >= limit:
                break
        
        return items
    except Exception as e:
        print(f"RSS error {url}: {e}")
        return []

def fetch_macro_news():
    """获取宏观经济实时新闻 (RSS)"""
    print("正在获取宏观经济新闻...")
    # Investing.com Economy & General + CNBC Economy
    urls = [
        "https://www.investing.com/rss/news_14.rss", # Economy
        "https://www.cnbc.com/id/20910258/device/rss/rss.html", # CNBC Economy
        "https://feeds.content.dowjones.io/public/rss/mw_topstories" # MarketWatch Top Stories
    ]
    news_items = []
    for url in urls:
        items = fetch_rss_data(url, limit=10)
        news_items.extend(items)
    return news_items

class DailyReport:
    def __init__(self):
        self.monitor = IndicatorMonitor()
    
    def generate_report(self):
        print("🚀 开始执行 AI 日报生成任务...")
        
        # 1. Fetch All Data
        market_summary = self.monitor.run()
        github_data = fetch_github_trending()
        hn_data = fetch_hacker_news_ai()
        hf_data = fetch_huggingface_trending()
        macro_news = fetch_macro_news()
        
        # RSS for context
        rss_sources = [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
        ]
        rss_news = []
        for url in rss_sources:
            rss_news.extend(fetch_rss_data(url, limit=2))

        # 2. LLM Processing
        print("🤖 正在调用 LLM 生成 HTML 报告...")
        
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        
        # 使用北京时间 (UTC+8) 避免服务器时区差异
        beijing_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        today = beijing_now.strftime("%Y年%m月%d日 | %A")
        
        # Construct Prompt
        system_prompt = f"""
        你是一个专业的 AI 与金融市场分析师。请根据提供的数据，生成一份 HTML 格式的日报。
        
        **必须严格遵守以下 HTML 结构和排版要求**：
        1. **不要使用 Markdown**，直接输出纯 HTML 代码 (只包含 <body> 内的 div 结构)。
        2. **必须使用指定的 CSS 类名**，不要修改类名，因为样式表已经固定。
        3. **所有标题和项目必须包含超链接** (如果有 URL)。

        **HTML 模板结构 (请严格按此填充数据)**：

        ```html
        <div class="container">
            <h1>AI & 金融市场日报</h1>
            <div class="date">{today}</div>

            <!-- 1. 核心指标监控 (Market Pulse) -->
            <div class="section">
                <div class="section-title"><span class="section-icon">📉</span> 核心指标监控</div>
                <div class="grid-container">
                    <!-- 请为每个指标生成一个 grid-card -->
                    <!-- 示例: BTC Price -->
                    <div class="grid-card">
                        <div class="card-label">BTC Price</div>
                        <div class="card-value">$69,388</div> <!-- 数值 -->
                        <div class="card-sub trend-down">📉 -1.25%</div> <!-- trend-up(绿)/trend-down(红)/trend-neutral(灰) -->
                    </div>
                    <!-- 必须包含以下指标:
                         1. BTC Price
                         2. ETH Price
                         3. BTC ETF Flow (使用 trend-up/down 和 emoji)
                         4. 恐慌贪婪指数 (使用 bg-red/bg-green 标签)
                         5. 资金费率 (Bybit) (保留4-6位小数, 如 0.0001%, 使用 bg-green/red)
                         6. 持仓量 (OI)
                         7. 巨鲸 (>1k BTC)
                         8. 稳定币市值
                         9. 10年期美债
                         10. 美元指数 (DXY)
                         11. 黄金 (Gold)
                         12. 标普500 (SPX)
                    -->
                </div>
            </div>

            <!-- 2. 宏观经济硬核快讯 -->
            <div class="section">
                <div class="section-title"><span class="section-icon">🗞️</span> 宏观经济硬核快讯</div>
                <!-- 循环生成 macro-item -->
                <div class="macro-item">
                    <div class="macro-header">
                        <a href="[URL]" class="macro-link">📉 [标题]: [核心人物/机构] [观点]</a> 
                        <span class="macro-meta">[时间标签]</span>
                    </div>
                    <div class="macro-body">[一句话背景或影响]</div>
                </div>
            </div>

            <!-- 3. GitHub 本周黑马 -->
            <div class="section">
                <div class="section-title"><span class="section-icon">🛠</span> GitHub 本周黑马</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 70%">项目 & 核心创新</th>
                            <th style="width: 30%; text-align: right;">趋势</th>
                        </tr>
                    </thead>
                    <tbody>
                        <!-- 循环生成行 -->
                        <tr>
                            <td>
                                <a href="[URL]" class="project-name">[项目名称]</a>
                                <div class="project-desc">[描述], <b>[核心创新点/解决了什么痛点]</b>。</div>
                            </td>
                            <td style="text-align: right;"><span class="stars-badge">⭐️ [Stars数]</span></td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- 4. Hugging Face 前沿 -->
            <div class="section">
                <div class="section-title"><span class="section-icon">🤗</span> Hugging Face 前沿</div>
                <ul class="hf-list">
                    <!-- 循环生成 hf-item -->
                    <li class="hf-item">
                        <div class="hf-header">
                            <span class="hf-tag tag-paper">Paper</span> <!-- 或 tag-model -->
                            <div class="hf-title"><a href="[URL]" class="hf-link">[标题]</a></div>
                        </div>
                        <div class="hf-comment">“[一句话锐评：比之前的强在哪里？]”</div>
                    </li>
                </ul>
            </div>

            <!-- 5. AI 社区热议 -->
            <div class="section">
                <div class="section-title"><span class="section-icon">🌐</span> AI 社区热议</div>
                <ul class="hn-list">
                    <!-- 循环生成 hn-item -->
                    <li class="hn-item">
                        <span class="hn-bullet">💬</span>
                        <div class="hn-content">
                            <a href="[URL]" class="hn-link">“[标题/核心观点]”</a> — [热度/来源], [主要争论点]。
                        </div>
                    </li>
                </ul>
            </div>

            <!-- 6. 今日总结 -->
            <div class="section">
                <div class="summary-box">
                    <div class="summary-title">💡 今日总结</div>
                    <div class="summary-item">1. <b>[关键词]</b>：[总结1]</div>
                    <div class="summary-item">2. <b>[关键词]</b>：[总结2]</div>
                    <div class="summary-item">3. <b>[关键词]</b>：[总结3]</div>
                </div>
            </div>
        </div>
        ```

        **数据处理逻辑**：
        1. **Market Pulse**: 
           - 涨跌幅颜色：`trend-up` (绿色/涨), `trend-down` (红色/跌), `trend-neutral` (灰色)。
           - **注意**：对于价格(BTC/ETH/SPX)，涨是好事(trend-up 绿色)；对于恐慌指数，极度恐慌是红色(bg-red)。
           - 资金费率：如果绝对值 < 0.001%，保留 4-6 位小数，不要显示为 0.00%。
        2. **Macro News**: 筛选 3-5 条高价值新闻，必须包含具体人物或机构。时间标签格式如 "极新 3小时前" 或 "昨日"。
        3. **GitHub**: 重点关注 "本周黑马"，必须用粗体 `<b>` 标出核心创新点。
        4. **Hugging Face**: 区分 Paper 和 Model，必须有一句话锐评。
        5. **URL**: 所有 `<a href="...">` 必须填写真实 URL，如果没有则填 `#`。

        **输入数据**：
        Market Data: {json.dumps(market_summary, indent=2)}
        GitHub: {json.dumps(github_data, indent=2)}
        Hacker News: {json.dumps(hn_data, indent=2)}
        Hugging Face: {json.dumps(hf_data, indent=2)}
        Macro News (RSS): {json.dumps(macro_news, indent=2)}
        
        请直接输出生成的 HTML 代码。
        """
        
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请生成今天的日报 HTML。"}
                ],
                temperature=0.3
            )
            html_content = response.choices[0].message.content
            # Strip markdown code blocks if present
            html_content = html_content.replace("```html", "").replace("```", "")
            
            return html_content
        except Exception as e:
            print(f"LLM 生成失败: {e}")
            return "<h3>报告生成失败</h3>"

    def send_email(self, html_content):
        sender = MAIL_USER
        
        # 主收件人 (Env)
        main_receiver = MAIL_RECEIVER
        # 抄送收件人 (CC)
        cc_receivers = ["tangjx1004@163.com", "251400187@qq.com", "120750300@qq.com"]
        
        # 所有收件人 (SMTP 传输需要)
        all_receivers = [main_receiver] + cc_receivers
        
        msg = MIMEMultipart('related')
        
        # 使用北京时间
        beijing_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        msg['Subject'] = f"🤖 AI & 金融前沿日报 ({beijing_now.date()})"
        msg['From'] = sender
        msg['To'] = main_receiver
        msg['Cc'] = ",".join(cc_receivers)
        
        # CSS from preview_layout_v2.html
        css_styles = """
    /* 全局重置与字体 */
    body { 
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
        line-height: 1.6; 
        color: #37352f; 
        background-color: #f7f7f5;
        margin: 0; 
        padding: 40px 20px; 
    }

    .container { 
        max-width: 800px; 
        margin: 0 auto; 
        background: #ffffff; 
        padding: 60px; 
        min-height: 100vh;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05); 
    }

    h1 { font-size: 32px; margin: 0 0 8px 0; font-weight: 700; letter-spacing: -0.5px; }
    .date { color: #787774; font-size: 14px; margin-bottom: 48px; border-bottom: 1px solid #e0e0e0; padding-bottom: 24px; }
    
    .section { margin-bottom: 48px; }
    .section-title { 
        font-size: 20px; font-weight: 600; margin-bottom: 16px; 
        display: flex; align-items: center; color: #37352f; 
        padding-bottom: 8px; border-bottom: 2px solid #f1f1ef;
    }
    .section-icon { margin-right: 8px; font-size: 24px; }

    /* 通用链接样式 */
    a { text-decoration: none; color: inherit; transition: color 0.2s; }
    a:hover { color: #2563eb; text-decoration: underline; }

    /* --- 1. 核心指标监控 (Market Pulse) - 紧凑网格布局 --- */
    .grid-container {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 12px;
    }

    .grid-card {
        background: #f7f9fb;
        border: 1px solid #eef0f2;
        border-radius: 6px;
        padding: 12px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        transition: all 0.2s;
    }
    .grid-card:hover { border-color: #d1d5db; transform: translateY(-1px); }
    
    .card-label { 
        font-size: 12px; color: #6b7280; margin-bottom: 4px; font-weight: 500;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .card-value { 
        font-size: 18px; font-weight: 700; color: #111827; 
        font-family: -apple-system, BlinkMacSystemFont, monospace; letter-spacing: -0.5px;
    }
    .card-sub { font-size: 11px; margin-top: 4px; font-weight: 500; display: flex; align-items: center; }
    
    .trend-up { color: #059669; }
    .trend-down { color: #dc2626; }
    .trend-neutral { color: #6b7280; }
    .bg-green { background-color: #ecfdf5; padding: 2px 6px; border-radius: 4px; color: #059669; }
    .bg-red { background-color: #fef2f2; padding: 2px 6px; border-radius: 4px; color: #dc2626; }

    /* --- 2. 宏观硬核快讯 (Macro) - 垂直列表 --- */
    .macro-item { margin-bottom: 20px; padding-left: 16px; border-left: 3px solid #3b82f6; }
    .macro-header { font-size: 15px; font-weight: 600; color: #1f2937; margin-bottom: 4px; }
    .macro-link { color: #1f2937; text-decoration: none; }
    .macro-link:hover { color: #2563eb; text-decoration: underline; }
    .macro-meta { font-size: 11px; color: #d97706; background: #fffbeb; padding: 2px 6px; border-radius: 4px; margin-left: 8px; vertical-align: middle; display: inline-block;}
    .macro-body { font-size: 14px; color: #4b5563; line-height: 1.5; }

    /* --- 3. GitHub Trending - 清爽宽表格 --- */
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th { text-align: left; border-bottom: 1px solid #e5e7eb; padding: 12px 8px; color: #9ca3af; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
    td { border-bottom: 1px solid #f3f4f6; padding: 16px 8px; vertical-align: top; }
    .project-name { font-weight: 600; color: #2563eb; font-size: 15px; margin-bottom: 4px; display: block; text-decoration: none; }
    .project-desc { color: #374151; font-size: 14px; line-height: 1.5; }
    .stars-badge { font-family: monospace; color: #059669; background: #ecfdf5; padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; white-space: nowrap; }

    /* --- 4. Hugging Face - 垂直卡片流 --- */
    .hf-list { list-style: none; padding: 0; margin: 0; }
    .hf-item { margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #f3f4f6; }
    .hf-item:last-child { border-bottom: none; }
    .hf-header { display: flex; align-items: baseline; margin-bottom: 6px; }
    .hf-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; margin-right: 8px; font-weight: 700; text-transform: uppercase; }
    .tag-paper { background: #eff6ff; color: #2563eb; }
    .tag-model { background: #fff7ed; color: #c2410c; }
    .hf-title { font-weight: 600; font-size: 16px; }
    .hf-link { color: #111827; text-decoration: none; }
    .hf-link:hover { color: #2563eb; text-decoration: underline; }
    .hf-comment { font-size: 14px; color: #4b5563; background: #f9fafb; padding: 10px; border-radius: 6px; border-left: 3px solid #9ca3af; margin-top: 8px; }

    /* --- 5. AI 热议 (Hacker News) --- */
    .hn-list { list-style: none; padding: 0; margin: 0; }
    .hn-item { margin-bottom: 16px; font-size: 14px; line-height: 1.5; color: #374151; display: flex; align-items: flex-start; }
    .hn-bullet { color: #f59e0b; margin-right: 10px; font-size: 16px; line-height: 1.5; }
    .hn-link { color: #111827; font-weight: 700; text-decoration: none; }
    .hn-link:hover { color: #2563eb; text-decoration: underline; }

    /* --- 6. 总结区 --- */
    .summary-box { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 24px; }
    .summary-title { font-weight: 700; margin-bottom: 16px; color: #92400e; display: flex; align-items: center; }
    .summary-item { margin-bottom: 12px; font-size: 15px; line-height: 1.6; color: #78350f; }
    .summary-item b { color: #451a03; }
        """
        
        full_html = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
            {css_styles}
            </style>
        </head>
        <body>
            {html_content}
            <div style="margin-top: 40px; font-size: 12px; color: #888; text-align: center;">
                Generated by Trae AI Assistant • {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(full_html, 'html', 'utf-8'))
        
        try:
            server = smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT)
            server.login(MAIL_USER, MAIL_PASS)
            server.sendmail(sender, all_receivers, msg.as_string())
            server.quit()
            print("✅ 邮件发送成功 (含抄送)！")
        except Exception as e:
            print(f"❌ 邮件发送失败: {e}")

if __name__ == "__main__":
    report = DailyReport()
    html = report.generate_report()
    if html:
        report.send_email(html)
    print("🎉 任务完成！")
