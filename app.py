"""好初早餐羅八 — 純公告機（2026-07-08 大瘦身）。

唯一任務：皮皮 1:1 打「公告：內容」→ push 到四店群組。
LINE 免費額度每月 200 則、推群組按人數計（四群 61 人＝一次 61 則），
額度全留給公告，其他功能（/notify 推播、今天？、待辦/記錄/點子、AI 閒聊）已移除。

保留的維運指令（走 reply，免費）：
- 「群組ID」：群組裡打，回報 group_id（維護 STORE_GROUPS 用）
- 「我的ID」：回報 user id（維護 PIPI_USER_ID 用）
"""
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, StickerSendMessage
import json
import os

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

PIPI_USER_ID = os.environ.get("PIPI_USER_ID")

# 四店群組廣播用：{"一店": "C群組ID", "二店": "...", "中山": "...", "敦南": "..."}
STORE_GROUPS = json.loads(os.environ.get("STORE_GROUPS", "{}"))

# 廣播附的官方貼圖，格式「packageId:stickerId」，留空＝不附貼圖
BROADCAST_STICKER = os.environ.get("BROADCAST_STICKER", "")


def broadcast_messages(content):
    """組出廣播訊息列表：內文＋（可選）貼圖。同一次 push 內多個訊息只計 1 則額度。"""
    messages = [TextSendMessage(text=content)]
    if BROADCAST_STICKER and ":" in BROADCAST_STICKER:
        pkg, stk = BROADCAST_STICKER.split(":", 1)
        messages.append(StickerSendMessage(package_id=pkg.strip(), sticker_id=stk.strip()))
    return messages


@app.route("/", methods=["GET"])
def health():
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
    text = event.message.text.strip()

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

    if text.startswith(("公告：", "公告:")):
        # 冒號必帶：純聊天提到「公告」兩字不會誤觸廣播
        # 群組裡出現「公告：」是夥伴正常發話，安靜略過；1:1 非皮皮才回拒絕
        if event.source.type != "user":
            return
        if event.source.user_id != PIPI_USER_ID:
            line_bot_api.reply_message(
                event.reply_token, TextSendMessage(text="這個指令只有皮皮在 1:1 私訊可以用喔")
            )
            return
        content = text[len("公告："):].strip()
        if not content:
            reply = "請帶上內容，例如：公告：明天 iCHEF 菜單會更新"
        elif not STORE_GROUPS:
            reply = "還沒設定四店群組（STORE_GROUPS 環境變數是空的），先把群組 ID 收齊喔"
        else:
            sent, failed = [], []
            for store, gid in STORE_GROUPS.items():
                try:
                    line_bot_api.push_message(gid, broadcast_messages(content))
                    sent.append(store)
                except Exception as e:
                    print(f"[broadcast error] {store}: {e}", flush=True)
                    failed.append(store)
            reply = f"📣 已發送到：{'、'.join(sent) if sent else '（無）'}"
            if failed:
                reply += f"\n❌ 發送失敗：{'、'.join(failed)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 其他訊息：群組一律安靜；皮皮 1:1 給用法提示；其他人 1:1 給固定說明
    if event.source.type != "user":
        return
    if event.source.user_id == PIPI_USER_ID:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="羅八現在只負責發公告喔：\n公告：要傳給四間店的內容"),
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="我是好初早餐的公告小幫手，這裡沒有提供對話功能喔 🙇"),
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
