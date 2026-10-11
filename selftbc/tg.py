"""Telegram Bot API client with TBC-style attribute access (camelCase + snake_case)."""
import json
import os
import re
import time

import requests

API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org")


class Bunch(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


def bunchify(o):
    if isinstance(o, dict):
        b = Bunch({k: bunchify(v) for k, v in o.items()})
        if "from" in o:
            b["from_user"] = b["from"]
        return b
    if isinstance(o, list):
        return [bunchify(i) for i in o]
    return o


class FileObj:
    """In-memory file, e.g. returned by getBotUsersFile; send with sendDocument."""

    def __init__(self, name, data):
        self.name = name
        self.data = data if isinstance(data, bytes) else str(data).encode()

    def __repr__(self):
        return f"<File {self.name} {len(self.data)}b>"


class TGError(Exception):
    def __init__(self, method, code, desc, params=None):
        super().__init__(f"{method}: [{code}] {desc}")
        self.method, self.code, self.description, self.parameters = method, code, desc, params or {}


# positional-argument order for convenience calls like bot.sendMessage("hi")
POSITIONAL = {
    "sendMessage": ["text"], "sendPhoto": ["photo"], "sendDocument": ["document"],
    "sendVideo": ["video"], "sendAudio": ["audio"], "sendAnimation": ["animation"],
    "sendSticker": ["sticker"], "sendVoice": ["voice"], "sendVideoNote": ["video_note"],
    "sendLocation": ["latitude", "longitude"], "sendContact": ["phone_number", "first_name"],
    "sendPoll": ["question", "options"], "sendDice": ["emoji"], "sendChatAction": ["action"],
    "editMessageText": ["text"], "editMessageCaption": ["caption"],
    "editMessageReplyMarkup": ["reply_markup"], "answerCallbackQuery": ["text"],
    "deleteMessage": ["message_id"], "pinChatMessage": ["message_id"],
    "banChatMember": ["user_id"], "unbanChatMember": ["user_id"], "getChatMember": ["user_id"],
    "forwardMessage": ["from_chat_id", "message_id"], "copyMessage": ["from_chat_id", "message_id"],
    "sendMediaGroup": ["media"], "replyText": ["text"], "replyPhoto": ["photo"],
    "setMessageReaction": ["message_id", "reaction"],
}
NEEDS_CHAT = {
    "sendMessage", "sendPhoto", "sendDocument", "sendVideo", "sendAudio", "sendAnimation", "sendSticker",
    "sendVoice", "sendVideoNote", "sendLocation", "sendContact", "sendPoll", "sendDice", "sendChatAction",
    "sendMediaGroup", "deleteMessage", "pinChatMessage", "unpinChatMessage", "banChatMember",
    "unbanChatMember", "getChatMember", "getChat", "leaveChat", "setMessageReaction", "sendInvoice",
    "sendVenue", "sendGame", "sendPaidMedia", "sendGift", "exportChatInviteLink", "createChatInviteLink",
    "getChatMemberCount", "getChatAdministrators", "restrictChatMember", "promoteChatMember",
    "approveChatJoinRequest", "declineChatJoinRequest", "sendRichMessage", "sendLivePhoto",
}
NEEDS_MSG = {"editMessageText", "editMessageCaption", "editMessageReplyMarkup", "editMessageMedia"}
ALIASES = {"replyText": "sendMessage", "replyPhoto": "sendPhoto", "kickChatMember": "banChatMember",
           "getChatMembersCount": "getChatMemberCount"}


def camel(name):
    if "_" not in name:
        return name
    p = name.split("_")
    return p[0] + "".join(x.title() if x.islower() else x for x in p[1:])


def raw_call(token, method, params=None, files=None, timeout=60, retries=3):
    params = params or {}
    data = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            data[k] = "true" if v else "false"
        elif isinstance(v, (dict, list, tuple)):
            data[k] = json.dumps(v, ensure_ascii=False)
        else:
            data[k] = v
    url = f"{API_BASE}/bot{token}/{method}"
    for attempt in range(retries):
        try:
            r = requests.post(url, data=data, files=files, timeout=timeout)
            try:
                j = r.json()
            except Exception:
                raise TGError(method, r.status_code, r.text[:200])
            if j.get("ok"):
                return j["result"]
            if j.get("error_code") == 429:
                time.sleep(min(j.get("parameters", {}).get("retry_after", 1), 30) + 0.2)
                continue
            raise TGError(method, j.get("error_code"), j.get("description"), j.get("parameters"))
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise TGError(method, 0, f"network error: {e}")
            time.sleep(1 + attempt)
    raise TGError(method, 0, "retries exhausted")


class BotAPI:
    """The low-level `bot` global: bot.sendMessage("hi"), bot.send_message(text=..., chat_id=...)."""

    def __init__(self, token, chat_id=None, message_id=None, on_error=None):
        self._token, self._chat, self._mid = token, chat_id, message_id

    def _invoke(self, method, args, kwargs):
        orig = method
        method = ALIASES.get(method, method)
        pos = POSITIONAL.get(orig) or POSITIONAL.get(method) or []
        for name, val in zip(pos, args):
            kwargs.setdefault(name, val)
        if len(args) > len(pos):
            raise TypeError(f"{orig}() got too many positional arguments")
        if method in NEEDS_CHAT and "chat_id" not in kwargs and self._chat is not None:
            kwargs["chat_id"] = self._chat
        if method in NEEDS_MSG and not kwargs.get("inline_message_id"):
            if "chat_id" not in kwargs and self._chat is not None:
                kwargs["chat_id"] = self._chat
            if "message_id" not in kwargs and self._mid is not None:
                kwargs["message_id"] = self._mid
        if method == "answerCallbackQuery" and "callback_query_id" not in kwargs:
            kwargs["callback_query_id"] = getattr(self, "_cbid", None)
        files = None
        for k, v in list(kwargs.items()):
            if isinstance(v, FileObj):
                files = files or {}
                files[k] = (v.name, v.data)
                kwargs[k] = None
        res = raw_call(self._token, method, kwargs, files=files)
        return bunchify(res)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        method = camel(name)

        def call(*args, **kwargs):
            return self._invoke(method, args, kwargs)
        return call
