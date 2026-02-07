import tweepy
import pandas as pd
import time
from datetime import datetime

# --- 1. 填入你的 4 个密钥 ---
API_KEY = "vHYEDBAHa6Ktry7H8JyrRqaKw"
API_SECRET = "U8evpyoXmykFr0ZHx1KK3EFkeOSCQh1WwEHNAvEJPwwKyR0dxf"
ACCESS_TOKEN = "2018625583660560384-jfUdN1E08ri0YgYUQN7ssy8IRhXOpg"
ACCESS_SECRET = "jKOzhGziu8z5Ayp2OlXUFCZYLAehR0J0B1V2xp1BBjmBG"

# --- 2. 认证连接 ---
# 使用 Tweepy Client (针对 Twitter API v2)
client = tweepy.Client(
    consumer_key=API_KEY, consumer_secret=API_SECRET,
    access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET
)

# --- [新增] 3. 测试用户配置 ---
# 请在此处填写需要接收私信的测试用户
# ID 查询地址: https://tweeterid.com/
TEST_TARGETS = [
    # 格式: {"id": "目标用户ID", "username": "目标用户名(不带@)"}
    # 示例: {"id": "44196397", "username": "elonmusk"},
    {"id": "REPLACE_WITH_ID", "username": "krystal7679091"}, 
]

def start_auto_dm():
    file_path = 'codes.xlsx'
    
    try:
        # 读取表格
        df = pd.read_excel(file_path)
        
        # 获取自己的 User ID (自动获取)
        me = client.get_me()
        my_id = me.data.id
        print(f"成功登录: @{me.data.username}")

        # --- [修改] 暂时注销自动获取粉丝逻辑 ---
        # followers = client.get_users_followers(id=my_id)
        # 
        # if not followers.data:
        #     print("目前没有新粉丝。")
        #     return
        
        # for follower in followers.data:
        #     f_id = follower.id
        #     f_name = follower.username

        # --- [修改] 改为遍历测试用户列表 ---
        print(f"正在启动测试模式，目标用户数: {len(TEST_TARGETS)}")
        
        for target in TEST_TARGETS:
            f_id = target['id']
            f_name = target['username']
            
            # 跳过未填写的默认项
            if "REPLACE" in str(f_id):
                print(f"⚠️ 跳过默认配置项，请在代码上方的 TEST_TARGETS 中填入真实用户ID")
                continue

            # 检查是否已经发过 (避免重复发送)
            if str(f_id) in df['用户ID'].astype(str).values:
                continue

            # 找一个“未使用”的码
            unused_rows = df[df['状态'] == '未使用']
            if unused_rows.empty:
                print("警报：邀请码库已空！")
                return

            row_index = unused_rows.index[0]
            code = unused_rows.loc[row_index, '邀请码']

            # 执行私信发送
            message = f"Hi @{f_name}, thank you for following alphaqx! We are thrilled to have you in our community. As a token of our appreciation, here is your exclusive early-access invitation code: {code}. Visit our website to get started. We look forward to your feedback!"
            try:
                client.create_direct_message(participant_id=f_id, text=message)
                print(f"✅ 已发放给 {f_name}: {code}")

                # 回填表格并保存
                df.loc[row_index, '状态'] = '已使用'
                df.loc[row_index, '用户ID'] = str(f_id)
                df.loc[row_index, '发放时间'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                df.to_excel(file_path, index=False)

                # 设置安全延迟，防止被封号
                time.sleep(60) 

            except Exception as e:
                print(f"❌ 发送给 {f_name} 失败: {e}")

    except Exception as e:
        print(f"程序运行出错: {e}")

if __name__ == "__main__":
    while True:
        print(f"[{datetime.now()}] 正在检查新粉丝...")
        start_auto_dm()
        time.sleep(300) # 每 5 分钟检查一次