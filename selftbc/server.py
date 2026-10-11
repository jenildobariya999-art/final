"""Dashboard + REST API (stdlib only, no third-party web framework)."""
import hmac
import json
import os
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, runtime, tg, tpy
from .runtime import MANAGER

ADMIN_KEY = os.environ.get("SELFTBC_KEY", "")
STATIC = os.path.join(os.path.dirname(__file__), "static")
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern).replace("{*", "") + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class HTTPErr(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def need_bot(bid):
    b = db.q1("SELECT * FROM bots WHERE id=?", (bid,))
    if not b:
        raise HTTPErr(404, "bot not found")
    return b


def pub_bot(b, with_token=False):
    d = {k: b[k] for k in ("id", "name", "username", "version", "created", "started_at")}
    d["running"] = MANAGER.is_running(b["id"])
    d["users"] = db.q1("SELECT COUNT(*) c FROM users WHERE bot_id=?", (b["id"],))["c"]
    d["commands"] = db.q1("SELECT COUNT(*) c FROM commands WHERE bot_id=?", (b["id"],))["c"]
    d["uptime"] = int(time.time() - b["started_at"]) if d["running"] and b["started_at"] else 0
    d["last_active"] = (db.q1("SELECT MAX(last_active) m FROM users WHERE bot_id=?", (b["id"],)) or {}).get("m")
    d["errors"] = db.q1("SELECT COUNT(*) c FROM errors WHERE bot_id=?", (b["id"],))["c"]
    if with_token:
        d["token"] = b["token"]
    return d


# ------------------------------------------------------------------ bots
@route("GET", "/api/bots")
def list_bots(h, **_):
    return [pub_bot(b) for b in db.q("SELECT * FROM bots ORDER BY created DESC")]


@route("POST", "/api/bots")
def create_bot(h, **_):
    token = (h.json().get("token") or "").strip()
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        raise HTTPErr(400, "invalid bot token format")
    try:
        me = tg.raw_call(token, "getMe", retries=1)
    except tg.TGError as e:
        raise HTTPErr(400, f"Telegram rejected the token: {e.description}")
    bid = str(me["id"])
    if db.q1("SELECT id FROM bots WHERE id=?", (bid,)):
        db.x("UPDATE bots SET token=?,name=?,username=? WHERE id=?", (token, me.get("first_name"), me.get("username"), bid))
    else:
        db.x("INSERT INTO bots(id,token,name,username,created) VALUES(?,?,?,?,?)", (bid, token, me.get("first_name"), me.get("username"), db.now()))
    return pub_bot(need_bot(bid))


@route("GET", "/api/bots/{bid}")
def get_bot(h, bid, **_):
    return pub_bot(need_bot(bid), with_token=True)


@route("DELETE", "/api/bots/{bid}")
def delete_bot(h, bid, **_):
    need_bot(bid)
    MANAGER.stop(bid)
    for t in ("commands", "users", "udata", "bdata", "waits", "jobs", "errors", "broadcasts"):
        db.x(f"DELETE FROM {t} WHERE bot_id=?", (bid,))
    db.x("DELETE FROM bots WHERE id=?", (bid,))
    return {"ok": True}


@route("POST", "/api/bots/{bid}/start")
def start_bot(h, bid, **_):
    need_bot(bid)
    MANAGER.start(bid)
    return {"ok": True}


@route("POST", "/api/bots/{bid}/stop")
def stop_bot(h, bid, **_):
    need_bot(bid)
    MANAGER.stop(bid)
    return {"ok": True}


@route("POST", "/api/bots/{bid}/token")
def update_token(h, bid, **_):
    need_bot(bid)
    token = (h.json().get("token") or "").strip()
    me = tg.raw_call(token, "getMe", retries=1)
    if str(me["id"]) != bid:
        raise HTTPErr(400, "that token belongs to a different bot")
    db.x("UPDATE bots SET token=? WHERE id=?", (token, bid))
    if MANAGER.is_running(bid):
        MANAGER.stop(bid)
        MANAGER.start(bid)
    return {"ok": True}


@route("POST", "/api/bots/{bid}/version")
def set_version(h, bid, **_):
    need_bot(bid)
    db.x("UPDATE bots SET version=? WHERE id=?", (str(h.json().get("version", "1.0.0"))[:20], bid))
    return {"ok": True}


# ------------------------------------------------------------------ commands
@route("GET", "/api/bots/{bid}/commands")
def list_commands(h, bid, **_):
    need_bot(bid)
    return db.q("SELECT name, length(code) size, (length(code)-length(replace(code,char(10),''))+1) lines, runs, updated FROM commands WHERE bot_id=? ORDER BY name", (bid,))


@route("GET", "/api/bots/{bid}/command")
def get_cmd(h, bid, **_):
    name = h.qs("name")
    r = db.q1("SELECT name,code,updated,runs FROM commands WHERE bot_id=? AND name=?", (bid, name))
    if not r:
        raise HTTPErr(404, "command not found")
    return r


@route("PUT", "/api/bots/{bid}/command")
def save_cmd(h, bid, **_):
    need_bot(bid)
    body = h.json()
    name, code = (body.get("name") or "").strip(), body.get("code", "")
    if not name:
        raise HTTPErr(400, "command name required")
    if len(code.encode()) > 256 * 1024:
        raise HTTPErr(400, "code exceeds 256 KB")
    if name != ".env":
        try:
            tpy.check(code, name)
        except tpy.TPYSyntaxError as e:
            raise HTTPErr(400, str(e))
    if not db.q1("SELECT 1 x FROM commands WHERE bot_id=? AND name=?", (bid, name)) and \
            db.q1("SELECT COUNT(*) c FROM commands WHERE bot_id=?", (bid,))["c"] >= 400:
        raise HTTPErr(400, "400 commands per bot limit reached")
    db.x("INSERT INTO commands(bot_id,name,code,updated) VALUES(?,?,?,?) ON CONFLICT(bot_id,name) DO UPDATE SET code=excluded.code, updated=excluded.updated",
         (bid, name, code, db.now()))
    return {"ok": True}


@route("DELETE", "/api/bots/{bid}/command")
def del_cmd(h, bid, **_):
    db.x("DELETE FROM commands WHERE bot_id=? AND name=?", (bid, h.qs("name")))
    return {"ok": True}


def parse_export(text):
    """'=== name ===' blocks -> list of (name, code). Same format as TBC's export_commands."""
    out, cur, buf = [], None, []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.fullmatch(r"=== (.+?) ===", line)
        if m:
            if cur is not None:
                out.append((cur, "\n".join(buf).strip("\n")))
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, "\n".join(buf).strip("\n")))
    return out


@route("POST", "/api/bots/{bid}/commands/import")
def import_cmds(h, bid, **_):
    need_bot(bid)
    text = h.text()
    # accept JSON too: {"commands":[{"name","code"}]} or {name: code}
    items = None
    if text.lstrip().startswith(("{", "[")):
        try:
            d = json.loads(text)
            if isinstance(d, dict) and "commands" in d:
                d = d["commands"]
            if isinstance(d, dict):
                items = list(d.items())
            elif isinstance(d, list):
                items = [(c.get("name") or c.get("command"), c.get("code", "")) for c in d]
        except ValueError:
            pass
    if items is None:
        items = parse_export(text)
    added = updated = 0
    bad = []
    for name, code in items:
        if not name:
            continue
        if name != ".env":
            try:
                tpy.check(code, name)
            except tpy.TPYSyntaxError as e:
                bad.append({"name": name, "error": str(e)})
                continue
        exists = db.q1("SELECT 1 x FROM commands WHERE bot_id=? AND name=?", (bid, name))
        db.x("INSERT INTO commands(bot_id,name,code,updated) VALUES(?,?,?,?) ON CONFLICT(bot_id,name) DO UPDATE SET code=excluded.code, updated=excluded.updated",
             (bid, name, code, db.now()))
        updated += 1 if exists else 0
        added += 0 if exists else 1
    return {"added": added, "updated": updated, "rejected": bad}


@route("GET", "/api/bots/{bid}/commands/export")
def export_cmds(h, bid, **_):
    rows = db.q("SELECT name,code FROM commands WHERE bot_id=? ORDER BY name", (bid,))
    return Raw("\n\n".join(f"=== {r['name']} ===\n{r['code']}" for r in rows) + "\n", "text/plain; charset=utf-8",
               f'attachment; filename="commands_{bid}.txt"')


@route("POST", "/api/bots/{bid}/search")
def search_cmds(h, bid, **_):
    b = h.json()
    pat, rep = b.get("q", ""), b.get("replace")
    if not pat:
        return {"matches": []}
    flags = 0 if b.get("case") else re.I
    rx_src = pat if b.get("regex") else re.escape(pat)
    if b.get("word"):
        rx_src = r"\b" + rx_src + r"\b"
    try:
        rx = re.compile(rx_src, flags)
    except re.error as e:
        raise HTTPErr(400, f"bad regex: {e}")
    matches, changed = [], 0
    for r in db.q("SELECT name,code FROM commands WHERE bot_id=?", (bid,)):
        lines = [(i + 1, ln) for i, ln in enumerate(r["code"].split("\n")) if rx.search(ln)]
        if lines:
            matches.append({"name": r["name"], "lines": [{"n": n, "text": t[:300]} for n, t in lines[:50]], "count": len(lines)})
            if rep is not None:
                new = rx.sub(rep.replace("\\", "\\\\") if not b.get("regex") else rep, r["code"])
                if r["name"] != ".env":
                    try:
                        tpy.check(new, r["name"])
                    except tpy.TPYSyntaxError:
                        continue
                db.x("UPDATE commands SET code=?, updated=? WHERE bot_id=? AND name=?", (new, db.now(), bid, r["name"]))
                changed += 1
    return {"matches": matches, "changed": changed}


@route("POST", "/api/bots/{bid}/test")
def test_cmd(h, bid, **_):
    b = h.json()
    bot = need_bot(bid)
    uid = b.get("user_id")
    if not uid:
        raise HTTPErr(400, "user_id required (a user who has started the bot)")
    return runtime.simulate(bid, uid, text=b.get("text"), callback_data=b.get("callback_data"))


@route("POST", "/api/validate")
def validate(h, **_):
    try:
        tpy.check(h.json().get("code", ""))
        return {"ok": True}
    except tpy.TPYSyntaxError as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ errors
@route("GET", "/api/bots/{bid}/errors")
def errors(h, bid, **_):
    return db.q("SELECT id,ts,command,error FROM errors WHERE bot_id=? ORDER BY id DESC LIMIT 200", (bid,))


@route("DELETE", "/api/bots/{bid}/errors")
def clear_errors(h, bid, **_):
    db.x("DELETE FROM errors WHERE bot_id=?", (bid,))
    return {"ok": True}


# ------------------------------------------------------------------ users
@route("GET", "/api/bots/{bid}/users")
def users(h, bid, **_):
    s = h.qs("search", "")
    lim = min(int(h.qs("limit", "100")), 1000)
    off = int(h.qs("offset", "0"))
    like = f"%{s}%"
    rows = db.q("SELECT user_id,first_name,username,created,last_active,blocked FROM users WHERE bot_id=? AND (user_id LIKE ? OR first_name LIKE ? OR username LIKE ?) ORDER BY last_active DESC LIMIT ? OFFSET ?",
                (bid, like, like, like, lim, off))
    return {"stats": runtime.bot_stats(bid), "users": rows}


@route("GET", "/api/bots/{bid}/users/export")
def users_export(h, bid, **_):
    fmt = h.qs("format", "json")
    f = runtime.users_file(bid, fmt, h.qs("creation", "1") == "1", h.qs("last_active", "1") == "1")
    return Raw(f.data, "text/csv" if fmt == "csv" else "application/json", f'attachment; filename="{f.name}"')


IMPORT_JOBS = {}


@route("POST", "/api/bots/{bid}/users/import")
def users_import(h, bid, **_):
    """Body = raw JSON/CSV/TXT text. ?verify=1 checks each id with Telegram (~10/s) and only keeps reachable users."""
    bot = need_bot(bid)
    items = runtime.parse_user_ids(h.text())
    if not items:
        raise HTTPErr(400, "no Telegram user IDs found in the file")
    verify = h.qs("verify", "0") == "1"
    jid = f"{bid}-{int(time.time())}"
    IMPORT_JOBS[jid] = {"total": len(items), "done": 0, "added": 0, "skipped": 0, "unreachable": 0, "finished": False}

    def work():
        job = IMPORT_JOBS[jid]
        batch = []
        for it in items:
            uid = it["id"]
            ok = True
            if verify:
                try:
                    tg.raw_call(bot["token"], "getChat", {"chat_id": uid}, retries=1, timeout=15)
                except tg.TGError:
                    ok = False
                time.sleep(0.1)
            if ok:
                t = db.now()
                if db.q1("SELECT 1 x FROM users WHERE bot_id=? AND user_id=?", (bid, uid)):
                    job["skipped"] += 1
                else:
                    db.x("INSERT INTO users(bot_id,user_id,first_name,username,created,last_active) VALUES(?,?,?,?,?,?)",
                         (bid, uid, it.get("first_name"), it.get("username"), t, t))
                    job["added"] += 1
            else:
                job["unreachable"] += 1
            job["done"] += 1
        job["finished"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"job": jid, "total": len(items)}


@route("GET", "/api/import-jobs/{jid}")
def import_job(h, jid, **_):
    return IMPORT_JOBS.get(jid) or {"error": "unknown job"}


@route("POST", "/api/bots/{bid}/users/{uid}/block")
def block_user(h, bid, uid, **_):
    db.x("UPDATE users SET blocked=? WHERE bot_id=? AND user_id=?", (1 if h.json().get("blocked", True) else 0, bid, uid))
    return {"ok": True}


# ------------------------------------------------------------------ data (Bot.saveData / User.saveData)
@route("GET", "/api/bots/{bid}/data")
def data_list(h, bid, **_):
    rows = db.q("SELECT name, length(value) size FROM bdata WHERE bot_id=? ORDER BY name", (bid,))
    return {"bot_data": rows, "user_data_rows": db.q1("SELECT COUNT(*) c FROM udata WHERE bot_id=?", (bid,))["c"]}


@route("GET", "/api/bots/{bid}/data/export")
def data_export(h, bid, **_):
    return Raw(json.dumps(full_backup(bid, include_commands=False), ensure_ascii=False, indent=1), "application/json",
               f'attachment; filename="data_{bid}.json"')


@route("POST", "/api/bots/{bid}/data/import")
def data_import(h, bid, **_):
    need_bot(bid)
    return restore_into(bid, json.loads(h.text()), include_commands=False)


@route("GET", "/api/bots/{bid}/data/key")
def data_get(h, bid, **_):
    r = db.q1("SELECT value FROM bdata WHERE bot_id=? AND name=?", (bid, h.qs("name")))
    if not r:
        raise HTTPErr(404, "key not found")
    return {"name": h.qs("name"), "value": db.loads(r["value"])}


@route("PUT", "/api/bots/{bid}/data/key")
def data_set(h, bid, **_):
    b = h.json()
    s = db.dumps(b.get("value"))
    if len(s.encode()) > 2 * 1024 * 1024:
        raise HTTPErr(400, "value exceeds 2 MB")
    db.x("INSERT OR REPLACE INTO bdata(bot_id,name,value) VALUES(?,?,?)", (bid, b["name"], s))
    return {"ok": True}


@route("DELETE", "/api/bots/{bid}/data/key")
def data_del(h, bid, **_):
    db.x("DELETE FROM bdata WHERE bot_id=? AND name=?", (bid, h.qs("name")))
    return {"ok": True}


@route("GET", "/api/bots/{bid}/userdata")
def userdata(h, bid, **_):
    u = h.qs("user", "")
    if u:
        rows = db.q("SELECT user_id,name,value FROM udata WHERE bot_id=? AND user_id=? ORDER BY name", (bid, u))
    else:
        rows = db.q("SELECT user_id,name,value FROM udata WHERE bot_id=? ORDER BY user_id LIMIT 300", (bid,))
    return [{"user_id": r["user_id"], "name": r["name"], "value": db.loads(r["value"])} for r in rows]


@route("GET", "/api/bots/{bid}/commands/stats")
def cmd_stats(h, bid, **_):
    return db.q("SELECT name,(length(code)-length(replace(code,char(10),''))+1) lines FROM commands WHERE bot_id=? ORDER BY name", (bid,))


@route("GET", "/api/overview")
def overview(h, **_):
    bots = db.q("SELECT id FROM bots")
    return {"bots": len(bots), "running": sum(1 for b in bots if MANAGER.is_running(b["id"])),
            "users": db.q1("SELECT COUNT(*) c FROM users")["c"], "commands": db.q1("SELECT COUNT(*) c FROM commands")["c"],
            "errors": db.q1("SELECT COUNT(*) c FROM errors")["c"], "db_mb": round(os.path.getsize(db.DB_PATH) / 1e6, 2)}


@route("GET", "/api/backup-all")
def backup_all(h, **_):
    return Raw(json.dumps({"format": "selftbc-multi-1", "bots": [dict(full_backup(b["id"]), token=b["token"]) for b in db.q("SELECT id,token FROM bots")]},
                          ensure_ascii=False), "application/json", 'attachment; filename="selftbc_all_bots.json"')


@route("POST", "/api/restore-all")
def restore_all(h, **_):
    d = json.loads(h.text())
    n = 0
    for b in d.get("bots", []):
        bid, meta = str(b["bot"]["id"]), b["bot"]
        if not db.q1("SELECT id FROM bots WHERE id=?", (bid,)):
            db.x("INSERT INTO bots(id,token,name,username,version,created) VALUES(?,?,?,?,?,?)",
                 (bid, b.get("token", ""), meta.get("name"), meta.get("username"), meta.get("version", "1.0.0"), db.now()))
        restore_into(bid, b)
        n += 1
    return {"bots_restored": n}


def backup_loop():
    """Optional: SELFTBC_BACKUP_CHAT=<your telegram id>, SELFTBC_BACKUP_HOURS=6 -> sends a JSON backup of every bot to you."""
    chat = os.environ.get("SELFTBC_BACKUP_CHAT")
    hrs = float(os.environ.get("SELFTBC_BACKUP_HOURS", "0") or 0)
    if not chat or hrs <= 0:
        return
    while True:
        time.sleep(hrs * 3600)
        try:
            bots = db.q("SELECT id,token FROM bots ORDER BY created LIMIT 1")
            if bots:
                data = json.dumps({"format": "selftbc-multi-1", "bots": [dict(full_backup(b["id"]), token=b["token"]) for b in db.q("SELECT id,token FROM bots")]}, ensure_ascii=False).encode()
                tg.raw_call(bots[0]["token"], "sendDocument", {"chat_id": chat, "caption": "SelfTBC auto-backup"},
                            files={"document": (time.strftime("selftbc_%Y%m%d_%H%M.json"), data)})
        except Exception:
            traceback.print_exc()


# ------------------------------------------------------------------ backup / restore (everything)
def full_backup(bid, include_commands=True):
    b = need_bot(bid)
    out = {"format": "selftbc-backup-1", "bot": {"id": b["id"], "name": b["name"], "username": b["username"], "version": b["version"]}}
    if include_commands:
        out["commands"] = {r["name"]: r["code"] for r in db.q("SELECT name,code FROM commands WHERE bot_id=?", (bid,))}
    out["users"] = [{"id": r["user_id"], "first_name": r["first_name"], "username": r["username"], "created": r["created"],
                     "last_active": r["last_active"], "blocked": r["blocked"]} for r in db.q("SELECT * FROM users WHERE bot_id=?", (bid,))]
    out["bot_data"] = {r["name"]: db.loads(r["value"]) for r in db.q("SELECT name,value FROM bdata WHERE bot_id=?", (bid,))}
    ud = {}
    for r in db.q("SELECT user_id,name,value FROM udata WHERE bot_id=?", (bid,)):
        ud.setdefault(r["user_id"], {})[r["name"]] = db.loads(r["value"])
    out["user_data"] = ud
    return out


def restore_into(bid, d, include_commands=True):
    n = {"commands": 0, "users": 0, "bot_data": 0, "user_data": 0}
    if include_commands:
        for name, code in (d.get("commands") or {}).items():
            db.x("INSERT INTO commands(bot_id,name,code,updated) VALUES(?,?,?,?) ON CONFLICT(bot_id,name) DO UPDATE SET code=excluded.code", (bid, name, code, db.now()))
            n["commands"] += 1
    for u in d.get("users") or []:
        t = db.now()
        db.x("INSERT OR IGNORE INTO users(bot_id,user_id,first_name,username,created,last_active,blocked) VALUES(?,?,?,?,?,?,?)",
             (bid, str(u["id"]), u.get("first_name"), u.get("username"), u.get("created") or t, u.get("last_active") or t, u.get("blocked", 0)))
        n["users"] += 1
    for k, v in (d.get("bot_data") or {}).items():
        db.x("INSERT OR REPLACE INTO bdata(bot_id,name,value) VALUES(?,?,?)", (bid, k, db.dumps(v)))
        n["bot_data"] += 1
    for uid, kv in (d.get("user_data") or {}).items():
        for k, v in kv.items():
            db.x("INSERT OR REPLACE INTO udata(bot_id,user_id,name,value) VALUES(?,?,?,?)", (bid, str(uid), k, db.dumps(v)))
            n["user_data"] += 1
    return n


@route("GET", "/api/bots/{bid}/backup")
def backup(h, bid, **_):
    return Raw(json.dumps(full_backup(bid), ensure_ascii=False, indent=1), "application/json", f'attachment; filename="backup_{bid}.json"')


@route("POST", "/api/bots/{bid}/restore")
def restore(h, bid, **_):
    need_bot(bid)
    return restore_into(bid, json.loads(h.text()))


# ------------------------------------------------------------------ broadcasts
@route("GET", "/api/bots/{bid}/broadcasts")
def broadcasts(h, bid, **_):
    return db.q("SELECT * FROM broadcasts WHERE bot_id=? ORDER BY started DESC LIMIT 50", (bid,))


@route("POST", "/api/bots/{bid}/broadcast")
def do_broadcast(h, bid, **_):
    bot = need_bot(bid)
    b = h.json()
    if not runtime.get_command(bid, b.get("command")) and not b.get("code"):
        raise HTTPErr(400, "choose an existing command (it runs once per user) or provide code")
    return {"id": runtime.start_broadcast(bot, command=b.get("command"), code=b.get("code"), speed=b.get("speed", 8))}


@route("POST", "/api/broadcasts/{id}/stop")
def stop_bc(h, id, **_):
    db.x("UPDATE broadcasts SET status='stopped' WHERE id=? AND status='running'", (id,))
    return {"ok": True}


# ------------------------------------------------------------------ HTTP plumbing
class Raw:
    def __init__(self, body, ctype, disp=None):
        self.body = body if isinstance(body, bytes) else body.encode()
        self.ctype, self.disp = ctype, disp


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SelfTBC"

    def log_message(self, *a):
        pass

    # helpers used by routes
    def text(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200 * 1024 * 1024:
            raise HTTPErr(413, "body too large")
        return self.rfile.read(n).decode("utf-8", "replace") if n else ""

    def json(self):
        t = self.text()
        try:
            return json.loads(t) if t else {}
        except ValueError:
            raise HTTPErr(400, "invalid JSON body")

    def qs(self, k, default=None):
        v = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get(k)
        return v[0] if v else default

    def authed(self):
        if not ADMIN_KEY:
            return True
        k = self.headers.get("X-API-Key") or (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if not k:
            k = self.qs("key", "")
        return hmac.compare_digest(k.encode(), ADMIN_KEY.encode())

    def send(self, code, body, ctype="application/json", disp=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if disp:
            self.send_header("Content-Disposition", disp)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_any(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if not path.startswith("/api/"):
            f = os.path.join(STATIC, "index.html")
            with open(f, "rb") as fh:
                return self.send(200, fh.read(), "text/html; charset=utf-8")
        if path == "/api/ping":
            return self.send(200, b'{"ok":true,"auth_required":%s}' % (b"true" if ADMIN_KEY else b"false"))
        if not self.authed():
            return self.send(401, b'{"error":"unauthorized"}')
        for method, rx, fn in ROUTES:
            if method != self.command:
                continue
            m = rx.match(path)
            if m:
                try:
                    res = fn(self, **m.groupdict())
                    if isinstance(res, Raw):
                        return self.send(200, res.body, res.ctype, res.disp)
                    return self.send(200, json.dumps(res, ensure_ascii=False, default=str).encode())
                except HTTPErr as e:
                    return self.send(e.code, json.dumps({"error": e.msg}).encode())
                except tg.TGError as e:
                    return self.send(400, json.dumps({"error": str(e)}).encode())
                except Exception as e:
                    traceback.print_exc()
                    return self.send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        self.send(404, b'{"error":"not found"}')

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_any


def serve(host="0.0.0.0", port=None):
    port = int(port or os.environ.get("PORT", 8080))
    db.conn()
    threading.Thread(target=backup_loop, daemon=True).start()
    MANAGER.start_saved()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"SelfTBC dashboard on http://{host}:{port}  (auth: {'ON' if ADMIN_KEY else 'OFF - set SELFTBC_KEY!'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
