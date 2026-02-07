import time
import os
import pandas as pd
import random
from playwright.sync_api import sync_playwright

# ---------------- 配置 ----------------
USER_DATA_DIR = os.path.join(os.getcwd(), "twitter_browser_data")
EXCEL_PATH = "codes.xlsx"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def start_browser_dm():
    if not os.path.exists(EXCEL_PATH):
        log(f"❌ 找不到 {EXCEL_PATH}")
        return

    # 读取 Excel
    df = pd.read_excel(EXCEL_PATH)

    # ===== 已发送用户集合 =====
    sent_users = set(
        df[df["状态"] == "已使用"]["用户ID"]
        .dropna()
        .astype(str)
        .str.replace("@", "", regex=False)
        .tolist()
    )

    log(f"📌 已发送用户数: {len(sent_users)}")

    with sync_playwright() as p:
        log("🚀 启动浏览器...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            channel="chrome",
            args=["--start-maximized"],
            no_viewport=True,
            slow_mo=500
        )

        page = context.pages[0] if context.pages else context.new_page()

        # ---------------- 登录检测 ----------------
        log("🔍 检查登录状态...")
        page.goto("https://twitter.com/home")

        try:
            page.wait_for_selector(
                '[data-testid="SideNav_NewTweet_Button"], [data-testid="AppTabBar_Home_Link"]',
                timeout=8000
            )
            log("✅ 已登录")
        except:
            log("⚠️ 请手动登录（120 秒）")
            try:
                page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=120000)
                log("✅ 登录完成")
            except:
                log("❌ 登录超时")
                context.close()
                return

        log("🔄 开始持续监听模式...")
        
        while True:
            try:
                # ---------------- 进入粉丝页 ----------------
                # 先点击个人资料，确保获取正确的 URL
                try:
                    page.click('[data-testid="AppTabBar_Profile_Link"]')
                    time.sleep(2)
                    
                    profile_url = page.url.split("?")[0].rstrip("/")
                    followers_url = profile_url + "/followers"
                    # log(f"➡️ 刷新粉丝页: {followers_url}")

                    page.goto(followers_url)
                    page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
                    time.sleep(3)
                except Exception as e:
                    log(f"⚠️ 访问粉丝页出错: {e}，等待重试...")
                    time.sleep(10)
                    continue

                # ---------------- 抓取粉丝 ----------------
                user_cells = page.query_selector_all('[data-testid="UserCell"]')

                followers = []
                for cell in user_cells:
                    try:
                        for line in cell.inner_text().split("\n"):
                            if line.startswith("@"):
                                followers.append(line.replace("@", ""))
                                break
                    except:
                        pass

                # log(f"📥 当前抓取粉丝数: {len(followers)}")

                # ======================================================
                # ⭐ 核心逻辑：只处理第一个粉丝 ⭐
                # ======================================================
                if not followers:
                    log("⚠️ 未抓取到粉丝，等待重试...")
                    time.sleep(10)
                    continue

                target_username = followers[0]
                # log(f"🎯 检查第一个粉丝: @{target_username}")

                if target_username in sent_users:
                    # 如果第一个粉丝已经发送过，说明没有新粉丝（或者新粉丝还未排到第一位）
                    # 打印日志不要太频繁
                    log(f"🟡 @{target_username} 已发送过，继续监听... (等待 20s)")
                    time.sleep(20)
                    continue
                
                log(f"🚀 发现新目标: @{target_username}，开始执行发送流程！")

                # ======================================================

                # ---------------- 准备发送 ----------------
                # 每次发送前重新检查一下 DataFrame，防止逻辑错误
                unused = df[df["状态"] == "未使用"]
                if unused.empty:
                    log("❌ 没有可用配额，程序退出")
                    break

                row_index = unused.index[0]

                # ---------------- 发送私信 ----------------
                try:
                    page.goto(f"https://twitter.com/{target_username}")
                    time.sleep(3)

                    dm_btn = page.query_selector('[data-testid="sendDMFromProfile"]')
                    if not dm_btn:
                        log(f"❌ 未找到 @{target_username} 的私信按钮（可能未开放私信），跳过")
                        # 将其加入 sent_users 以避免重复尝试（虽然没发送成功，但无法发送）
                        # 这里选择加入，避免死循环卡在这里
                        sent_users.add(target_username)
                        continue

                    dm_btn.click()

                    dm_input = None
                    for _ in range(20):
                        dm_input = page.query_selector('[data-testid="dm-composer-textarea"]')
                        if dm_input:
                            break
                        time.sleep(1)

                    if not dm_input:
                        log("❌ 未进入私信界面，跳过")
                        continue

                    message = (
                        f"Hi @{target_username}! 👋\n\n"
                        f"Thanks for following alphaqx! We're excited to have you in our community. "
                        f"Stay tuned for exclusive updates, insights, and opportunities coming your way! 🚀"
                    )

                    dm_input.click()
                    # 增加 delay 模拟真实输入
                    lines = message.split('\n')
                    for i, line in enumerate(lines):
                        if line:
                            page.keyboard.type(line, delay=50)
                        
                        if i < len(lines) - 1:
                            page.keyboard.press("Shift+Enter")
                            time.sleep(0.1)
                    
                    time.sleep(1)

                    # 等待发送按钮变为可用状态
                    send_btn = None
                    try:
                        send_btn = page.wait_for_selector('[data-testid="dm-composer-send-button"]:not([disabled])', timeout=5000)
                    except:
                        pass

                    # 如果还是不可用，尝试“激活”一下输入框
                    if not send_btn:
                        log("⚠️ 发送按钮仍不可用，尝试激活输入框...")
                        page.keyboard.press("Space")
                        time.sleep(0.5)
                        page.keyboard.press("Backspace")
                        time.sleep(1)
                        
                        try:
                            send_btn = page.wait_for_selector('[data-testid="dm-composer-send-button"]:not([disabled])', timeout=3000)
                        except:
                            pass

                    if not send_btn:
                        log("❌ 发送按钮不可用 (超时)，跳过")
                        continue

                    send_btn.click()
                    log(f"✅ 私信已发送给 @{target_username}")

                    # ---------------- 回关功能 ----------------
                    follow_back_status = "未回关"
                    try:
                        log(f"🔄 检查是否需要回关 @{target_username}...")
                        # 返回用户主页
                        page.goto(f"https://twitter.com/{target_username}")
                        time.sleep(3)
                        
                        # 检查是否有"关注"按钮（如果有，说明该用户关注了我们）
                        # 查找关注按钮，可能的状态：Following（已关注）、Follow（未关注）
                        follow_button = page.query_selector('[data-testid="placementTracking"] [role="button"]')
                        
                        if follow_button:
                            button_text = follow_button.inner_text().strip()
                            log(f"📍 按钮状态: {button_text}")
                            
                            # 如果按钮显示"关注"或"Follow"，说明我们还没关注对方
                            if button_text in ["关注", "Follow", "フォロー"]:
                                log(f"👉 开始回关 @{target_username}...")
                                follow_button.click()
                                time.sleep(2)
                                
                                # 验证是否成功
                                follow_button_after = page.query_selector('[data-testid="placementTracking"] [role="button"]')
                                if follow_button_after:
                                    new_text = follow_button_after.inner_text().strip()
                                    if new_text in ["正在关注", "Following", "フォロー中"]:
                                        follow_back_status = "已回关"
                                        log(f"✅ 成功回关 @{target_username}")
                                    else:
                                        follow_back_status = "回关失败"
                                        log(f"⚠️ 回关可能失败，按钮状态: {new_text}")
                                else:
                                    follow_back_status = "已回关"
                                    log(f"✅ 已回关 @{target_username}")
                            elif button_text in ["正在关注", "Following", "フォロー中"]:
                                follow_back_status = "已关注"
                                log(f"ℹ️ 已经关注过 @{target_username}")
                            else:
                                follow_back_status = f"未知状态({button_text})"
                                log(f"⚠️ 未知按钮状态: {button_text}")
                        else:
                            log(f"⚠️ 未找到关注按钮")
                            follow_back_status = "未找到按钮"
                            
                    except Exception as e:
                        log(f"❌ 回关过程异常: {e}")
                        follow_back_status = "回关异常"

                    # ---------------- 更新 Excel ----------------
                    df.loc[row_index, "状态"] = "已使用"
                    df.loc[row_index, "用户ID"] = f"@{target_username}"
                    df.loc[row_index, "发放时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    df.loc[row_index, "回关状态"] = follow_back_status

                    df.to_excel(EXCEL_PATH, index=False)
                    log("💾 Excel 已更新")
                    
                    # 更新内存中的已发送列表
                    sent_users.add(target_username)
                    
                    # 发送完休息一下
                    log("🎉 本次任务完成，休息 10 秒...")
                    time.sleep(10)

                except Exception as e:
                    log(f"❌ 发送过程异常: {e}")
                    time.sleep(5)

            except Exception as e:
                log(f"❌ 循环外层异常: {e}")
                time.sleep(10)

        context.close()

if __name__ == "__main__":
    start_browser_dm()
