from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-1.5-flash")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]  # 後母的家（All Tasks Archive）
# 艾庭的點子暫存庫（在「艾庭的工作面板」底下）
NOTION_IDEAS_DB_ID = os.environ.get("NOTION_IDEAS_DB_ID", "c946327f-b5bc-4373-aa5f-b78a7ca4e47b")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2025-09-03",
}

# /notify 推播用：兩個都設在 Render 環境變數，缺任一個端點就拒絕服務
NOTIFY_SECRET = os.environ.get("NOTIFY_SECRET")
PIPI_USER_ID = os.environ.get("PIPI_USER_ID")

# 四店群組廣播用：{"一店": "C群組ID", "二店": "...", "中山": "...", "敦南": "..."}
STORE_GROUPS = json.loads(os.environ.get("STORE_GROUPS", "{}"))


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def parse_command(text):
    """辨識關鍵字並回傳 (種類, 內容)。
    待辦／記錄 -> task；點子 -> idea；其餘 -> (None, None) 走 Gemini 聊天。
    分隔符可以是空白（半/全形）或冒號（半/全形）。
    """
    text = text.strip()
    routes = [("點子", "idea"), ("待辦", "task"), ("記錄", "task")]
    for keyword, kind in routes:
        if text.startswith(keyword):
            rest = text[len(keyword):].lstrip(" 　:：\t")
            return kind, rest.strip()
    return None, None


def format_task_name(raw):
    """用 Gemini 把隨手打的話改寫成符合好初命名規則的待辦標題。"""
    prompt = (
        "你是好初早餐的待辦命名助理。把下面這句話改寫成一個乾淨的待辦標題，規則：\n"
        "1. 動詞開頭，一眼看出要做什麼動作（不要只有名詞堆疊）\n"
        "2. 如果句中提到分店（敦南／中山／一店／二店），標題最前面加「店名｜」前綴，例如「敦南｜」\n"
        "3. 15 個字以內，精簡\n"
        "4. 只回傳標題本身一行，不要句號、不要引號、不要任何解釋\n"
        f"原句：{raw}"
    )
    try:
        resp = model.generate_content(prompt)
        name = resp.text.strip().splitlines()[0].strip()
        name = name.strip('「」"\'')
        return name or raw
    except Exception as e:
        print(f"[Gemini naming error] {e}", flush=True)
        return raw


def save_to_notion(raw_content, sender_name):
    """寫進後母的家，符合好初 task 規格：填齊 Task／Date／類型／權重／狀態。
    類型／權重固定預設為「雜務 ⭐️」（從 LINE 隨手丟的多半是雜務），少數其實是
    專案的，皮皮整理時再改。標題套命名規則，原話留在備註。
    """
    task_name = format_task_name(raw_content)
    data = {
        "parent": {"type": "data_source_id", "data_source_id": NOTION_DB_ID},
        "properties": {
            "Task": {"title": [{"text": {"content": task_name}}]},
            "Date": {"date": {"start": today_str()}},
            "類型": {"select": {"name": "雜務"}},
            "權重": {"select": {"name": "⭐️"}},
            "狀態": {"status": {"name": "🔲 待處理"}},
            "備註": {"rich_text": [{"text": {"content": f"來自 LINE：{sender_name}（原話：{raw_content}）"}}]},
        },
    }
    response = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=data)
    if response.status_code != 200:
        print(f"[Notion task error] {response.status_code}: {response.text}", flush=True)
        return False, task_name
    return True, task_name


def save_to_ideas(content, sender_name):
    """寫進艾庭的點子暫存庫，狀態預設為待孵化。"""
    data = {
        "parent": {"type": "data_source_id", "data_source_id": NOTION_IDEAS_DB_ID},
        "properties": {
            "點子": {"title": [{"text": {"content": content}}]},
            "日期": {"date": {"start": today_str()}},
            "狀態": {"select": {"name": "💡 待孵化"}},
            "備註": {"rich_text": [{"text": {"content": f"來自 LINE：{sender_name}"}}]},
        },
    }
    response = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=data)
    if response.status_code != 200:
        print(f"[Notion idea error] {response.status_code}: {response.text}", flush=True)
        return False
    return True


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/notify", methods=["POST"])
def notify():
    """給雲端排程 curl 用的推播端點：把提醒直接 push 到皮皮的 LINE。
    需帶 X-Notify-Secret header，body 為 {"text": "..."}。
    """
    if not NOTIFY_SECRET or request.headers.get("X-Notify-Secret") != NOTIFY_SECRET:
        abort(403)
    if not PIPI_USER_ID:
        return "PIPI_USER_ID not set", 503
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return "text required", 400
    line_bot_api.push_message(PIPI_USER_ID, TextSendMessage(text=text[:4900]))
    return "OK", 200


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
    try:
        profile = line_bot_api.get_profile(event.source.user_id)
        sender_name = profile.display_name
    except Exception:
        sender_name = "群組成員"

    text = user_message.strip()

    if text == "我的ID":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"你的 LINE user ID：\n{event.source.user_id}"),
        )
        return

    if text == "群組ID":
        if event.source.type == "group":
            reply = f"這個群組的 ID：\n{event.source.group_id}"
        else:
            reply = "請在群組裡對我說「群組ID」，我才能回報那個群組的 ID 喔"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if text.startswith("發給四間店"):
        # 只有皮皮本人在 1:1 私訊才能廣播，群組裡或其他人打一律擋下
        if event.source.type != "user" or event.source.user_id != PIPI_USER_ID:
            line_bot_api.reply_message(
                event.reply_token, TextSendMessage(text="這個指令只有皮皮在 1:1 私訊可以用喔")
            )
            return
        content = text[len("發給四間店"):].lstrip(" 　:：\t").strip()
        if not content:
            reply = "請帶上內容，例如：發給四間店：明天 iCHEF 菜單會更新"
        elif not STORE_GROUPS:
            reply = "還沒設定四店群組（STORE_GROUPS 環境變數是空的），先把群組 ID 收齊喔"
        else:
            sent, failed = [], []
            for store, gid in STORE_GROUPS.items():
                try:
                    line_bot_api.push_message(gid, TextSendMessage(text=content))
                    sent.append(store)
                except Exception as e:
                    print(f"[broadcast error] {store}: {e}", flush=True)
                    failed.append(store)
            reply = f"📣 已發送到：{'、'.join(sent) if sent else '（無）'}"
            if failed:
                reply += f"\n❌ 發送失敗：{'、'.join(failed)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    kind, content = parse_command(user_message)

    if kind == "task":
        if content:
            success, task_name = save_to_notion(content, sender_name)
            reply = f"✅ 已記成待辦：{task_name}" if success else "❌ 待辦記錄失敗，請稍後再試"
        else:
            reply = "請輸入待辦內容，例如：待辦：敦南補外帶吸管"
    elif kind == "idea":
        if content:
            success = save_to_ideas(content, sender_name)
            reply = f"💡 已收進點子庫：{content}" if success else "❌ 點子記錄失敗，請稍後再試"
        else:
            reply = "請輸入點子內容，例如：點子：做一款好初醬油菜單"
    else:
        response = model.generate_content(user_message)
        reply = response.text

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
