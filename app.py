from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
import requests
import os
from datetime import datetime

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-1.5-flash")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]

def save_to_notion(task_content, sender_name):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    data = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Task": {"title": [{"text": {"content": task_content}}]},
            "Date": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "Inbox (To Do Dump)": {"checkbox": True},
            "備註": {"rich_text": [{"text": {"content": f"來自 LINE：{sender_name}"}}]}
        }
    }
    response = requests.post(url, headers=headers, json=data)
    return response.status_code == 200

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    sender_name = ""

    try:
        profile = line_bot_api.get_profile(event.source.user_id)
        sender_name = profile.display_name
    except Exception:
        sender_name = "群組成員"

    if user_message.startswith("記錄 ") or user_message.startswith("記錄　"):
        task_content = user_message[3:].strip()
        if task_content:
            success = save_to_notion(task_content, sender_name)
            if success:
                reply = f"✅ 已記錄：{task_content}"
            else:
                reply = "❌ 記錄失敗，請稍後再試"
        else:
            reply = "請輸入要記錄的內容，例如：記錄 明天要備雞蛋"
    else:
        response = model.generate_content(user_message)
        reply = response.text

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
