"""SelfTBC runtime: update dispatch, TPY globals (bot/Bot/User/libs), pollers, scheduler, broadcasts."""
import base64
import binascii
import csv
import datetime
import hashlib
import io
import json
import math
import random
import re
import string
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import requests

from . import db, tg, tpy
from .tg import Bunch, BotAPI, FileObj, TGError, bunchify

ReturnCommand = tpy.ReturnCommand
MAX_CHAIN = 3
MAX_BOTDATA = 2 * 1024 * 1024
ALLOWED_UPDATES = [
    "message", "edited_message", "channel_post", "edited_channel_post", "business_connection",
    "business_message", "edited_business_message", "deleted_business_messages", "message_reaction",
    "message_reaction_count", "inline_query", "chosen_inline_result", "callback_query",
    "shipping_query", "pre_checkout_query", "purchased_paid_media", "poll", "poll_answer",
    "my_chat_member", "chat_member", "chat_join_request", "chat_boost", "removed_chat_boost",
]
POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="upd")


# --------------------------------------------------------------------------- helpers
def record_error(bot_id, command, err):
    db.x("INSERT INTO errors(bot_id,ts,command,error) VALUES(?,?,?,?)", (bot_id, db.now(), command, str(err)[:4000]))
    db.x("DELETE FROM errors WHERE bot_id=? AND id NOT IN (SELECT id FROM errors WHERE bot_id=? ORDER BY id DESC LIMIT 500)", (bot_id, bot_id))


def get_command(bot_id, name):
    if name is None:
        return None
    for cand in {name, name.lstrip("/"), "/" + name.lstrip("/")}:
        r = db.q1("SELECT * FROM commands WHERE bot_id=? AND name=?", (bot_id, cand))
        if r:
            return r
    r = db.q1("SELECT * FROM commands WHERE bot_id=? AND lower(name) IN (?,?)", (bot_id, name.lower(), "/" + name.lower().lstrip("/")))
    return r


def to_file(name, rows_or_text):
    return FileObj(name, rows_or_text)


def is_numeric(v):
    try:
        float(str(v))
        return True
    except Exception:
        return False


def cap_sleep(s):
    time.sleep(min(float(s), 10))


# --------------------------------------------------------------------------- context
class Ctx:
    def __init__(self, bot, user_id=None, chat_id=None, update=None, update_type="message", message=None, message_id=None):
        self.bot = bot  # db row
        self.bot_id = bot["id"]
        self.token = bot["token"]
        self.user_id = str(user_id) if user_id is not None else None
        self.chat_id = chat_id if chat_id is not None else user_id
        self.update = update
        self.update_type = update_type
        self.message = message if message is not None else Bunch()
        self.chain = 0
        self.in_error = False
        self.api = BotAPI(self.token, self.chat_id, message_id)
        if update_type == "callback_query" and message is not None:
            self.api._cbid = message.get("id")


# --------------------------------------------------------------------------- Bot / User / libs objects
class BotHL:
    """High-level `Bot` global."""

    def __init__(self, ctx):
        self.c = ctx

    # --- global data
    def saveData(self, name, data, bot_id=None, api_key=None):
        s = db.dumps(data)
        if len(s.encode()) > MAX_BOTDATA:
            raise ValueError("Bot data value exceeds 2 MB")
        db.x("INSERT OR REPLACE INTO bdata(bot_id,name,value) VALUES(?,?,?)", (self.c.bot_id, str(name), s))
        return {"ok": True}

    def getData(self, name=None, bot_id=None, api_key=None):
        r = db.q1("SELECT value FROM bdata WHERE bot_id=? AND name=?", (self.c.bot_id, str(name)))
        return db.loads(r["value"]) if r else None

    def deleteData(self, name=None, bot_id=None):
        db.x("DELETE FROM bdata WHERE bot_id=? AND name=?", (self.c.bot_id, str(name)))
        return {"ok": True}

    def deleteAllData(self, except_data=None):
        keep = list(except_data or [])
        rows = db.q("SELECT name FROM bdata WHERE bot_id=?", (self.c.bot_id,))
        for r in rows:
            if r["name"] not in keep:
                db.x("DELETE FROM bdata WHERE bot_id=? AND name=?", (self.c.bot_id, r["name"]))

    def getDataFile(self, name, output_format="txt"):
        v = self.getData(name)
        return FileObj(f"{name}.{output_format}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2))

    def getAllData(self, name=None, output_format="json"):
        rows = db.q("SELECT name,value FROM bdata WHERE bot_id=?", (self.c.bot_id,))
        d = {r["name"]: db.loads(r["value"]) for r in rows if name is None or name in r["name"]}
        return FileObj("bot_data.json", json.dumps(d, ensure_ascii=False, indent=2))

    # --- users
    def getBotUsersFile(self, output_format="json", include_creation_date=False, include_last_active_date=False):
        return users_file(self.c.bot_id, output_format, include_creation_date, include_last_active_date)

    def blockUser(self, user_id):
        db.x("UPDATE users SET blocked=1 WHERE bot_id=? AND user_id=?", (self.c.bot_id, str(user_id)))
        return {"ok": True}

    def unblockUser(self, user_id):
        db.x("UPDATE users SET blocked=0 WHERE bot_id=? AND user_id=?", (self.c.bot_id, str(user_id)))
        return {"ok": True}

    def getBlockedUsers(self):
        return [r["user_id"] for r in db.q("SELECT user_id FROM users WHERE bot_id=? AND blocked=1", (self.c.bot_id,))]

    def getBlockedUsersCount(self):
        return len(self.getBlockedUsers())

    def getBlockedUsersFile(self):
        return FileObj("blocked_users.json", json.dumps(self.getBlockedUsers()))

    def getStats(self, time_frames=None, bot_id=None, api_key=None):
        return bot_stats(self.c.bot_id)

    def info(self, bot_id=None, api_key=None):
        b = db.q1("SELECT id,name,username,running,version FROM bots WHERE id=?", (self.c.bot_id,))
        b["stats"] = bot_stats(self.c.bot_id)
        return Bunch(b)

    def start(self, bot_id=None, api_key=None):
        MANAGER.start(bot_id or self.c.bot_id)

    def stop(self, bot_id=None, api_key=None):
        MANAGER.stop(bot_id or self.c.bot_id)

    def status(self, bot_id=None, api_key=None):
        return "working" if MANAGER.is_running(bot_id or self.c.bot_id) else "stopped"

    # --- ids
    def genId(self):
        return int(uuid.uuid4().int % 10**12)

    def genRandomId(self):
        return uuid.uuid4().hex[:12]

    gen_random_id = genRandomId

    def genRandomErrorId(self):
        return "ERR-" + uuid.uuid4().hex[:8].upper()

    # --- flow control
    def handleNextCommand(self, command, options=None, cancel_at_command=False):
        if not self.c.user_id:
            raise ValueError("handleNextCommand needs a user context")
        db.x("INSERT OR REPLACE INTO waits(bot_id,user_id,command,options,cancel_at_command) VALUES(?,?,?,?,?)",
             (self.c.bot_id, self.c.user_id, command, db.dumps(options), 1 if cancel_at_command else 0))
        return {"ok": True}

    def runCommand(self, command, options=None):
        if self.c.in_error:
            return {"ok": False, "error": "runCommand disabled inside !"}
        if self.c.chain >= MAX_CHAIN:
            return {"ok": False, "error": "chain limit (3) reached"}
        self.c.chain += 1
        run_named(self.c, command, options=options)
        return {"ok": True}

    def runCommandAfter(self, timeout, command, options=None, id=None):
        if isinstance(timeout, datetime.datetime):
            timeout = (timeout - datetime.datetime.now(timeout.tzinfo)).total_seconds()
        timeout = float(timeout)
        if timeout < 1 or timeout > 60 * 60 * 24 * 366:
            raise ValueError("timeout must be between 1 second and 366 days")
        n = db.q1("SELECT COUNT(*) c FROM jobs WHERE bot_id=? AND user_id=?", (self.c.bot_id, self.c.user_id))["c"]
        if n >= 50000:
            raise ValueError("too many scheduled tasks (50,000)")
        jid = str(id) if id else uuid.uuid4().hex
        db.x("INSERT OR REPLACE INTO jobs(id,bot_id,user_id,chat_id,command,options,run_at) VALUES(?,?,?,?,?,?,?)",
             (jid, self.c.bot_id, self.c.user_id, str(self.c.chat_id), command, db.dumps(options), db.now() + timeout))
        return {"id": jid, "command": command, "timeout": timeout}

    def cancelScheduledTask(self, job_id):
        db.x("DELETE FROM jobs WHERE id=?", (str(job_id),))
        return {"ok": True}

    # --- broadcast
    def broadcast(self, code=None, command=None, callback_url=None, bot_id=None, api_key=None, function=None,
                  warnings=None, mode=None, bot_ids=None, speed=None, **kwargs):
        if not (code or command):
            raise ValueError("broadcast needs command= or code=")
        return start_broadcast(self.c.bot, command=command, code=code, speed=int(speed or 8), options=kwargs or None)

    def getBroadcastStatus(self, broadcast_id):
        r = db.q1("SELECT * FROM broadcasts WHERE id=? AND bot_id=?", (broadcast_id, self.c.bot_id))
        return Bunch(r) if r else None

    def listBroadcasts(self):
        return [Bunch(r) for r in db.q("SELECT * FROM broadcasts WHERE bot_id=? ORDER BY started DESC", (self.c.bot_id,))]

    def stopBroadcast(self, broadcast_id):
        db.x("UPDATE broadcasts SET status='stopped' WHERE id=? AND status='running'", (broadcast_id,))
        return {"ok": True}

    def setBroadcastSpeed(self, broadcast_id, speed):
        db.x("UPDATE broadcasts SET speed=? WHERE id=?", (max(1, min(int(speed), 25)), broadcast_id))
        return {"ok": True}

    def clearBroadcast(self, broadcast_id=None):
        if broadcast_id:
            db.x("DELETE FROM broadcasts WHERE id=? AND bot_id=?", (broadcast_id, self.c.bot_id))
        else:
            db.x("DELETE FROM broadcasts WHERE bot_id=? AND status!='running'", (self.c.bot_id,))
        return "ok"

    def Transfer(self, *a, **k):
        raise NotImplementedError("Transfer is not available on a self-hosted instance")


class UserHL:
    def __init__(self, ctx):
        self.c = ctx

    def _uid(self, user):
        return str(user if user is not None else self.c.user_id)

    def saveData(self, name, data, user=None, data_type=None, bot_id=None, api_key=None):
        db.x("INSERT OR REPLACE INTO udata(bot_id,user_id,name,value) VALUES(?,?,?,?)",
             (self.c.bot_id, self._uid(user), str(name), db.dumps(data)))
        return {"ok": True}

    def getData(self, name, user=None, bot_id=None, api_key=None):
        r = db.q1("SELECT value FROM udata WHERE bot_id=? AND user_id=? AND name=?", (self.c.bot_id, self._uid(user), str(name)))
        return db.loads(r["value"]) if r else None

    def deleteData(self, name, user=None, data_type=None, bot_id=None, api_key=None):
        db.x("DELETE FROM udata WHERE bot_id=? AND user_id=? AND name=?", (self.c.bot_id, self._uid(user), str(name)))
        return {"ok": True}

    def deleteAllData(self, name=None, old_data=False, except_data=None):
        keep = list(except_data or [])
        for r in db.q("SELECT user_id,name FROM udata WHERE bot_id=?", (self.c.bot_id,)):
            if (name is None or r["name"] == name) and r["name"] not in keep:
                db.x("DELETE FROM udata WHERE bot_id=? AND user_id=? AND name=?", (self.c.bot_id, r["user_id"], r["name"]))

    def deleteAllDataOfUser(self, user):
        db.x("DELETE FROM udata WHERE bot_id=? AND user_id=?", (self.c.bot_id, str(user)))

    def getDataFile(self, name, user=None, output_format="txt"):
        v = self.getData(name, user)
        return FileObj(f"{name}.{output_format}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2))

    def getAllData(self, name=None, output_format="json", old_data=False):
        d = {}
        for r in db.q("SELECT user_id,name,value FROM udata WHERE bot_id=?", (self.c.bot_id,)):
            if name is None or r["name"] == name:
                d.setdefault(r["user_id"], {})[r["name"]] = db.loads(r["value"])
        return FileObj("user_data.json", json.dumps(d, ensure_ascii=False, indent=2))

    def getAllDataOfUser(self, user, output_format="json"):
        d = {r["name"]: db.loads(r["value"]) for r in db.q("SELECT name,value FROM udata WHERE bot_id=? AND user_id=?", (self.c.bot_id, str(user)))}
        return FileObj(f"user_{user}.json", json.dumps(d, ensure_ascii=False, indent=2))


class HTTPLib:
    def request(self, method, url, params=None, data=None, json=None, headers=None, timeout=20, **kw):
        timeout = min(float(timeout), 60)
        return requests.request(method.upper(), url, params=params, data=data, json=json, headers=headers, timeout=timeout)

    def get(self, url, **k):
        return self.request("GET", url, **k)

    def post(self, url, **k):
        return self.request("POST", url, **k)

    def put(self, url, **k):
        return self.request("PUT", url, **k)

    def delete(self, url, **k):
        return self.request("DELETE", url, **k)


class RandomLib:
    def randomInt(self, a, b):
        return random.randint(int(a), int(b))

    def randomFloat(self, a, b):
        return random.uniform(float(a), float(b))

    def randomStr(self, length=8, char_set=None):
        cs = char_set or (string.ascii_letters + string.digits)
        return "".join(random.choice(cs) for _ in range(int(length)))

    def randomAscii(self, length=8):
        return "".join(random.choice(string.ascii_letters) for _ in range(int(length)))


class CSVHandler:
    """In-memory CSV helper (libs.CSV.CSVHandler). Data is kept in Bot data under csv:<name>."""

    def __init__(self, ctx, name):
        self.c, self.name = ctx, name

    def _load(self):
        return BotHL(self.c).getData("csv:" + self.name) or {"headers": [], "rows": []}

    def create_csv(self, headers):
        BotHL(self.c).saveData("csv:" + self.name, {"headers": list(headers), "rows": []})

    def add_row(self, row):
        d = self._load()
        d["rows"].append(row)
        BotHL(self.c).saveData("csv:" + self.name, d)

    def edit_row(self, row_index, row):
        d = self._load()
        d["rows"][int(row_index)] = row
        BotHL(self.c).saveData("csv:" + self.name, d)

    def get(self):
        d = self._load()
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=d["headers"])
        w.writeheader()
        w.writerows(d["rows"])
        return FileObj(self.name, out.getvalue())

    def delete(self):
        BotHL(self.c).deleteData("csv:" + self.name)


def make_libs(ctx):
    csv_ns = SimpleNamespace(CSVHandler=lambda name: CSVHandler(ctx, name))

    def set_lang(lang):
        db.x("UPDATE users SET lang=? WHERE bot_id=? AND user_id=?", (lang, ctx.bot_id, ctx.user_id))

    translate = SimpleNamespace(setLang=set_lang, getLang=lambda: (db.q1("SELECT lang FROM users WHERE bot_id=? AND user_id=?", (ctx.bot_id, ctx.user_id)) or {}).get("lang"))
    return SimpleNamespace(Random=RandomLib(), CSV=csv_ns, translate=translate)


# --------------------------------------------------------------------------- stats / users files
def bot_stats(bot_id):
    t = db.now()
    day, week, month = t - 86400, t - 7 * 86400, t - 30 * 86400

    def c(where, *a):
        return db.q1(f"SELECT COUNT(*) c FROM users WHERE bot_id=? {where}", (bot_id,) + a)["c"]
    return {
        "total": c(""), "blocked": c("AND blocked=1"),
        "active_24h": c("AND last_active>=?", day), "active_7d": c("AND last_active>=?", week), "active_30d": c("AND last_active>=?", month),
        "new_24h": c("AND created>=?", day), "new_7d": c("AND created>=?", week), "new_30d": c("AND created>=?", month),
    }


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat() if ts else None


def users_file(bot_id, fmt="json", creation=True, last_active=True):
    rows = db.q("SELECT * FROM users WHERE bot_id=? ORDER BY created", (bot_id,))
    items = []
    for r in rows:
        it = {"id": r["user_id"], "first_name": r["first_name"], "username": r["username"], "blocked": bool(r["blocked"])}
        if creation:
            it["created"] = iso(r["created"])
        if last_active:
            it["last_active"] = iso(r["last_active"])
        items.append(it)
    if fmt == "csv":
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(items[0].keys()) if items else ["id"])
        w.writeheader()
        w.writerows(items)
        return FileObj("users.csv", out.getvalue())
    return FileObj("users.json", json.dumps(items, ensure_ascii=False, indent=2))


def parse_user_ids(text, fmt=None):
    """Accepts JSON (list of ids / list of dicts / dict), CSV with header, or TXT one-per-line."""
    text = text.strip().lstrip("﻿")
    ids = []
    keys = ("id", "user_id", "userid", "chat_id", "telegram_id", "tg_id", "uid")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for k in ("users", "data", "items", "ids"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                data = list(data.keys()) if all(re.fullmatch(r"-?\d+", str(k)) for k in data) else [data]
        for it in data:
            if isinstance(it, dict):
                for k in keys:
                    if k in it and re.fullmatch(r"-?\d+", str(it[k])):
                        ids.append({"id": str(it[k]), "first_name": it.get("first_name"), "username": it.get("username"),
                                    "created": it.get("created") or it.get("creation_date")})
                        break
            elif re.fullmatch(r"-?\d+", str(it)):
                ids.append({"id": str(it)})
        return ids
    except ValueError:
        pass
    lines = text.splitlines()
    if lines and "," in lines[0] and not re.fullmatch(r"\s*-?\d+\s*,?.*", lines[0].split(",")[0]):
        for row in csv.DictReader(io.StringIO(text)):
            low = {k.strip().lower(): v for k, v in row.items() if k}
            for k in keys:
                if k in low and re.fullmatch(r"-?\d+", str(low[k]).strip()):
                    ids.append({"id": low[k].strip(), "first_name": low.get("first_name"), "username": low.get("username")})
                    break
        return ids
    for ln in lines:
        m = re.match(r"\s*(-?\d+)", ln)
        if m:
            ids.append({"id": m.group(1)})
    return ids


# --------------------------------------------------------------------------- namespace / execution
def build_namespace(ctx, command_name, msg="", params="", options=None):
    api = ctx.api
    hl = BotHL(ctx)
    ns = {
        "msg": msg, "message": ctx.message, "bot": api, "Bot": hl, "User": UserHL(ctx), "libs": make_libs(ctx),
        "HTTP": HTTPLib(), "update_type": ctx.update_type, "bot_token": ctx.token, "bot_id": ctx.bot_id,
        "params": params, "u": ctx.user_id if ctx.user_id is None or not str(ctx.user_id).lstrip("-").isdigit() else int(ctx.user_id),
        "options": options, "left_points": 10**9, "isNumeric": is_numeric,
        "MembershipCheck": lambda chats, user_id=None: membership_check(ctx, chats, user_id),
        "encodejson": lambda o: json.dumps(o, ensure_ascii=False), "jsondumps": lambda o, **k: json.dumps(o, ensure_ascii=False, **k),
        "bf_json": lambda o: json.loads(o) if isinstance(o, (str, bytes)) else json.dumps(o, ensure_ascii=False),
        "ReturnCommand": ReturnCommand, "returncommand": ReturnCommand, "returnCommand": ReturnCommand,
        "parse_qs": urllib.parse.parse_qs, "rawurlencode": urllib.parse.quote, "encodeURIComponent": lambda s: urllib.parse.quote(str(s), safe=""),
        "decodeURIComponent": urllib.parse.unquote, "md5": lambda s: hashlib.md5(str(s).encode()).hexdigest(),
        "bunchify": bunchify, "base64": base64, "binascii": binascii, "hashlib": hashlib, "re": re, "regex": re,
        "json": json, "math": math, "random": random, "datetime": datetime,
        "time": SimpleNamespace(time=time.time, sleep=cap_sleep, strftime=time.strftime, gmtime=time.gmtime,
                                localtime=time.localtime, mktime=time.mktime, monotonic=time.monotonic, ctime=time.ctime),
        "handleNextCommand": hl.handleNextCommand, "runCommand": hl.runCommand, "runCommandAfter": hl.runCommandAfter,
        "command": command_name, "call": ctx.message if ctx.update_type == "callback_query" else None,
        "Exception": Exception,
    }
    ns.update(env_vars(ctx))
    return ns


_env_cache = {}


def env_vars(ctx):
    row = get_command(ctx.bot_id, ".env")
    if not row or not row["code"].strip():
        return {}
    key = (ctx.bot_id, row["code"])
    if key not in _env_cache:
        try:
            ns = {}
            tpy.execute(row["code"], ns, ".env")
            _env_cache[key] = {k: v for k, v in ns.items() if not k.startswith("__")}
        except Exception as e:
            record_error(ctx.bot_id, ".env", e)
            _env_cache[key] = {}
    return dict(_env_cache[key])


def membership_check(ctx, chats, user_id=None):
    if isinstance(chats, (str, int)):
        chats = [chats]
    uid = user_id or ctx.user_id
    for ch in chats:
        try:
            m = ctx.api.getChatMember(chat_id=ch, user_id=uid)
        except TGError:
            return False
        if m.status not in ("creator", "owner", "administrator", "member") and not (m.status == "restricted" and m.is_member):
            return False
    return True


def run_code(ctx, name, code, msg="", params="", options=None, use_preface=True, count=True):
    """Run one command's code (with ~ preface). Errors go to the `!` handler."""
    preface = ""
    if use_preface:
        p = get_command(ctx.bot_id, "~")
        if p:
            preface = p["code"] + "\n"
    ns = build_namespace(ctx, name, msg, params, options)
    try:
        tpy.execute(preface + code, ns, name)
        if count:
            db.x("UPDATE commands SET runs=runs+1 WHERE bot_id=? AND name=?", (ctx.bot_id, name))
        return True
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc(limit=3)
        record_error(ctx.bot_id, name, err + "\n" + tb)
        h = get_command(ctx.bot_id, "!")
        if h and not ctx.in_error and name != "!":
            ctx.in_error = True
            try:
                hns = build_namespace(ctx, "!", msg, params, err)
                tpy.execute(h["code"], hns, "!")
            except Exception as e2:
                record_error(ctx.bot_id, "!", f"{type(e2).__name__}: {e2}")
            finally:
                ctx.in_error = False
        elif not h and ctx.chat_id is not None and ctx.update_type in ("message", "callback_query"):
            try:
                ctx.api.sendMessage(text="⚠️ Something went wrong. Please try again later.")
            except Exception:
                pass
        return False


def run_named(ctx, name, msg="", params="", options=None):
    row = get_command(ctx.bot_id, name)
    if not row:
        return False
    return run_code(ctx, row["name"], row["code"], msg or ctx.message.get("text") or "", params, options)


def run_gate(ctx, msg, params):
    """Run `@`. Returns False if it raised ReturnCommand (stop)."""
    g = get_command(ctx.bot_id, "@")
    if not g:
        return True
    ns = build_namespace(ctx, "@", msg, params, None)
    try:
        co = tpy.check(g["code"], "@")
        ns["__builtins__"] = tpy.SAFE_BUILTINS
        exec(co, ns)
        return True
    except ReturnCommand:
        return False
    except Exception as e:
        record_error(ctx.bot_id, "@", f"{type(e).__name__}: {e}")
        return True  # a broken gate must not take the bot down


# --------------------------------------------------------------------------- update dispatch
def extract(update):
    ut = next((k for k in update if k != "update_id"), None)
    obj = update.get(ut) or {}
    frm = obj.get("from") or obj.get("user") or {}
    chat = obj.get("chat") or (obj.get("message") or {}).get("chat") or {}
    if ut == "callback_query":
        chat = (obj.get("message") or {}).get("chat") or {}
    return ut, obj, frm, chat


def touch_user(bot_id, frm):
    if not frm or frm.get("is_bot") or not frm.get("id"):
        return None
    uid = str(frm["id"])
    t = db.now()
    r = db.q1("SELECT blocked FROM users WHERE bot_id=? AND user_id=?", (bot_id, uid))
    if r is None:
        db.x("INSERT INTO users(bot_id,user_id,first_name,username,lang,created,last_active) VALUES(?,?,?,?,?,?,?)",
             (bot_id, uid, frm.get("first_name"), frm.get("username"), frm.get("language_code"), t, t))
        return {"blocked": 0, "new": True}
    db.x("UPDATE users SET first_name=?,username=?,last_active=? WHERE bot_id=? AND user_id=?",
         (frm.get("first_name"), frm.get("username"), t, bot_id, uid))
    return {"blocked": r["blocked"], "new": False}


def dispatch(bot, update):
    bot_id = bot["id"]
    ut, obj, frm, chat = extract(update)
    if ut is None:
        return
    st = touch_user(bot_id, frm)
    if st and st["blocked"]:
        return
    user_id = frm.get("id") or chat.get("id")
    chat_id = chat.get("id", user_id)
    message = bunchify(obj)
    if ut == "callback_query":
        mid = (obj.get("message") or {}).get("message_id")
    else:
        mid = obj.get("message_id")
    ctx = Ctx(bot, user_id, chat_id, update, ut, message, mid)

    if ut in ("message", "callback_query"):
        text = (obj.get("data") if ut == "callback_query" else (obj.get("text") or obj.get("caption"))) or ""
        # wait (handleNextCommand)
        if user_id is not None:
            w = db.q1("SELECT * FROM waits WHERE bot_id=? AND user_id=?", (bot_id, str(user_id)))
            if w and ut == "message":
                is_cmd = text.startswith("/")
                if is_cmd and w["cancel_at_command"]:
                    db.x("DELETE FROM waits WHERE bot_id=? AND user_id=?", (bot_id, str(user_id)))
                else:
                    db.x("DELETE FROM waits WHERE bot_id=? AND user_id=?", (bot_id, str(user_id)))
                    if not run_gate(ctx, text, ""):
                        return
                    run_named(ctx, w["command"], msg=text, options=db.loads(w["options"]))
                    return
        name, params = text, ""
        if text.startswith("/") or ut == "callback_query":
            parts = text.split(None, 1)
            tok = parts[0] if parts else ""
            tok = re.sub(r"@\w+$", "", tok) if tok.startswith("/") else tok
            params = parts[1] if len(parts) > 1 else ""
            name = tok
            if ut == "callback_query" and not get_command(bot_id, text):
                name = tok
            elif ut == "callback_query":
                name, params = text, ""
        if not run_gate(ctx, text, params):
            return
        row = get_command(bot_id, name) if name else None
        if row is None and text and not text.startswith("/"):
            row = get_command(bot_id, text)  # reply-keyboard button text as command
        if row is None:
            row = get_command(bot_id, "*")
        if row:
            run_code(ctx, row["name"], row["code"], text, params, None)
        return

    # special updates
    row = get_command(bot_id, f"/handler_{ut}") or get_command(bot_id, f"handler_{ut}") \
        or get_command(bot_id, "/handler_special_updates") or get_command(bot_id, "handler_special_updates")
    if row:
        run_code(ctx, row["name"], row["code"], "", "", None)


# --------------------------------------------------------------------------- broadcast
def start_broadcast(bot, command=None, code=None, speed=8, options=None):
    users = [r["user_id"] for r in db.q("SELECT user_id FROM users WHERE bot_id=? AND blocked=0", (bot["id"],))]
    bid = uuid.uuid4().hex[:10]
    speed = max(1, min(int(speed), 25))
    db.x("INSERT INTO broadcasts(id,bot_id,command,status,total,speed,started) VALUES(?,?,?,?,?,?,?)",
         (bid, bot["id"], command or "<code>", "running", len(users), speed, db.now()))

    def work():
        sent = failed = 0
        for uid in users:
            st = db.q1("SELECT status,speed FROM broadcasts WHERE id=?", (bid,))
            if not st or st["status"] != "running":
                break
            ctx = Ctx(bot, uid, uid, None, "broadcast", Bunch())
            try:
                if command:
                    row = get_command(bot["id"], command)
                    ok = run_code(ctx, command, row["code"], "", "", options, count=False) if row else False
                else:
                    ok = run_code(ctx, "<broadcast>", code, "", "", options, count=False)
                sent += 1 if ok else 0
                failed += 0 if ok else 1
            except TGError as e:
                failed += 1
                if e.code == 403:
                    db.x("UPDATE users SET blocked=1 WHERE bot_id=? AND user_id=?", (bot["id"], uid))
            except Exception:
                failed += 1
            db.x("UPDATE broadcasts SET sent=?,failed=? WHERE id=?", (sent, failed, bid))
            time.sleep(1.0 / max(1, st["speed"]))
        db.x("UPDATE broadcasts SET status=CASE WHEN status='running' THEN 'done' ELSE status END, sent=?, failed=?, finished=? WHERE id=?",
             (sent, failed, db.now(), bid))

    threading.Thread(target=work, daemon=True, name=f"bc-{bid}").start()
    return bid


# --------------------------------------------------------------------------- runners
class BotRunner(threading.Thread):
    def __init__(self, bot_id):
        super().__init__(daemon=True, name=f"bot-{bot_id}")
        self.bot_id = bot_id
        self.stop_flag = threading.Event()

    def run(self):
        bot = db.q1("SELECT * FROM bots WHERE id=?", (self.bot_id,))
        if not bot:
            return
        try:
            tg.raw_call(bot["token"], "deleteWebhook", {"drop_pending_updates": False})
        except Exception as e:
            record_error(self.bot_id, "(startup)", e)
        offset = bot["tg_offset"] or 0
        backoff = 1
        while not self.stop_flag.is_set():
            try:
                ups = tg.raw_call(bot["token"], "getUpdates",
                                  {"offset": offset, "timeout": 25, "allowed_updates": ALLOWED_UPDATES}, timeout=40, retries=1)
                backoff = 1
                for up in ups:
                    offset = max(offset, up["update_id"] + 1)
                    cur = db.q1("SELECT * FROM bots WHERE id=?", (self.bot_id,)) or bot
                    POOL.submit(self._safe, cur, up)
                if ups:
                    db.x("UPDATE bots SET tg_offset=? WHERE id=?", (offset, self.bot_id))
            except TGError as e:
                if e.code in (401, 404):
                    record_error(self.bot_id, "(poller)", f"Token rejected by Telegram: {e}. Bot stopped.")
                    db.x("UPDATE bots SET running=0 WHERE id=?", (self.bot_id,))
                    return
                if self.stop_flag.wait(backoff):
                    return
                backoff = min(backoff * 2, 30)
            except Exception as e:
                record_error(self.bot_id, "(poller)", e)
                if self.stop_flag.wait(backoff):
                    return
                backoff = min(backoff * 2, 30)

    def _safe(self, bot, up):
        try:
            dispatch(bot, up)
        except Exception as e:
            record_error(self.bot_id, "(dispatch)", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")


class Manager:
    def __init__(self):
        self.runners = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()

    def is_running(self, bot_id):
        r = self.runners.get(str(bot_id))
        return bool(r and r.is_alive())

    def start(self, bot_id):
        bot_id = str(bot_id)
        with self.lock:
            if self.is_running(bot_id):
                return
            db.x("UPDATE bots SET running=1, started_at=? WHERE id=?", (db.now(), bot_id))
            r = BotRunner(bot_id)
            self.runners[bot_id] = r
            r.start()

    def stop(self, bot_id):
        bot_id = str(bot_id)
        with self.lock:
            r = self.runners.pop(bot_id, None)
            if r:
                r.stop_flag.set()
            db.x("UPDATE bots SET running=0 WHERE id=?", (bot_id,))

    def start_saved(self):
        for b in db.q("SELECT id FROM bots WHERE running=1"):
            self.start(b["id"])

    def _scheduler(self):
        while True:
            try:
                for j in db.q("SELECT * FROM jobs WHERE run_at<=? ORDER BY run_at LIMIT 200", (db.now(),)):
                    if db.x("DELETE FROM jobs WHERE id=?", (j["id"],)) == 0:
                        continue
                    bot = db.q1("SELECT * FROM bots WHERE id=?", (j["bot_id"],))
                    if bot and bot["running"]:
                        POOL.submit(self._job, bot, j)
            except Exception:
                pass
            time.sleep(1)

    def _job(self, bot, j):
        try:
            uid = j["user_id"] if j["user_id"] not in (None, "None") else None
            ctx = Ctx(bot, uid, j["chat_id"] if j["chat_id"] != "None" else uid, None, "scheduled", Bunch())
            row = get_command(bot["id"], j["command"])
            if row:
                run_code(ctx, row["name"], row["code"], "", "", db.loads(j["options"]))
        except Exception as e:
            record_error(bot["id"], j["command"], e)


MANAGER = Manager()


def simulate(bot_id, user_id, text=None, callback_data=None, first_name="Tester"):
    """Run a synthetic update synchronously (used by the dashboard Test button)."""
    bot = db.q1("SELECT * FROM bots WHERE id=?", (bot_id,))
    frm = {"id": int(user_id), "is_bot": False, "first_name": first_name}
    chat = {"id": int(user_id), "type": "private", "first_name": first_name}
    if callback_data is not None:
        up = {"update_id": 0, "callback_query": {"id": "sim", "from": frm, "data": callback_data,
                                                   "message": {"message_id": 1, "chat": chat, "date": int(time.time())}}}
    else:
        up = {"update_id": 0, "message": {"message_id": 1, "from": frm, "chat": chat, "date": int(time.time()), "text": text or "/start"}}
    before = db.q1("SELECT COALESCE(MAX(id),0) m FROM errors WHERE bot_id=?", (bot_id,))["m"]
    dispatch(bot, up)
    errs = db.q("SELECT command,error FROM errors WHERE bot_id=? AND id>?", (bot_id, before))
    return {"ran": True, "errors": errs}
