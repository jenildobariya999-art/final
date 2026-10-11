"""End-to-end test against a fake Telegram Bot API. Run: python3 tests/e2e.py"""
import json, os, sys, tempfile, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456789:" + "A" * 35
SENT, QUEUE, LOCK = [], [], threading.Lock()


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        ct = self.headers.get("Content-Type", "")
        data = {}
        if "json" in ct: data = json.loads(body or b"{}")
        elif "multipart" in ct: data = {"_multipart": True, "_len": len(body)}
        else: data = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
        m = self.path.rsplit("/", 1)[-1]
        res = True
        if m == "getMe": res = {"id": 123456789, "first_name": "Fake", "username": "fake_bot", "is_bot": True}
        elif m == "getUpdates":
            time.sleep(0.3)
            with LOCK:
                res, QUEUE[:] = list(QUEUE), []
        elif m == "getChatMember": res = {"status": "member", "user": {"id": 1}}
        elif m.startswith("send"):
            with LOCK: SENT.append((m, data))
            res = {"message_id": len(SENT), "chat": {"id": data.get("chat_id")}}
        else:
            with LOCK: SENT.append((m, data))
        out = json.dumps({"ok": True, "result": res}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


def start_fake():
    s = ThreadingHTTPServer(("127.0.0.1", 0), Fake); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


def wait_for(pred, t=8):
    end = time.time() + t
    while time.time() < end:
        with LOCK:
            if pred(): return True
        time.sleep(0.1)
    return False


port = start_fake()
os.environ["TG_API_BASE"] = f"http://127.0.0.1:{port}"
os.environ["SELFTBC_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["SELFTBC_KEY"] = "secret"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from selftbc import server, db  # noqa
import requests

db.conn()
srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{srv.server_address[1]}"
H = {"X-API-Key": "secret"}
def call(m, p, **kw): 
    r = requests.request(m, API + p, headers=H, **kw); 
    return r.status_code, (r.json() if r.headers.get("Content-Type", "").startswith("application/json") else r.text)

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, extra)

check("auth required", requests.get(API + "/api/bots").status_code == 401)
s, b = call("POST", "/api/bots", json={"token": TOKEN}); check("create bot", s == 200 and b["id"] == "123456789", b)
bid = "123456789"

export_text = """=== /start ===
User.saveData("seen", (User.getData("seen") or 0) + 1)
bot.sendMessage(f"Hi {message.from_user.first_name}! ref={params} seen={User.getData('seen')} brand={BRAND}")
bot.sendMessage("Your name?", reply_markup={"inline_keyboard":[[{"text":"ok","callback_data":"cb_ok x1"}]]})
handleNextCommand("get_name", {"k": 1})

=== get_name ===
User.saveData("name", msg)
bot.sendMessage(f"Saved {msg} opt={options}")

=== cb_ok ===
bot.answerCallbackQuery(text="clicked " + params)

=== ~ ===
BRAND = "Acme"

=== @ ===
if msg == "/blocked":
    bot.sendMessage("gated")
    raise ReturnCommand

=== * ===
bot.sendMessage("fallback: " + msg)

=== ! ===
bot.sendMessage("ERR: " + str(options))

=== /boom ===
x = 1 / 0

=== /chain ===
bot.sendMessage("a")
Bot.runCommand("c2")

=== c2 ===
bot.sendMessage("b")
Bot.runCommand("c3")

=== c3 ===
bot.sendMessage("c")
Bot.runCommand("c4")

=== c4 ===
bot.sendMessage("d")
Bot.runCommand("c5")

=== c5 ===
bot.sendMessage("e-SHOULD-NOT-RUN")

=== /later ===
Bot.runCommandAfter(1, "delayed", {"x": 5})

=== delayed ===
bot.sendMessage("delayed " + str(options))

=== /data ===
Bot.saveData("cfg", {"a": [1, 2]})
bot.sendMessage(str(Bot.getData("cfg")))
f = Bot.getBotUsersFile("csv", True, True)
bot.sendDocument(f)

=== /member ===
bot.sendMessage("member=" + str(MembershipCheck(["@ch"], u)))

=== /handler_edited_message ===
bot.sendMessage("edited " + message.text)

=== .env ===
SECRET = "xyz"
"""
s, r = call("POST", f"/api/bots/{bid}/commands/import", data=export_text.encode()); check("import commands", s == 200 and r["rejected"] == [] and r["added"] >= 15, r)
s, r = call("POST", f"/api/bots/{bid}/commands/import", data=b"=== bad ===\nimport os\n=== bad2 ===\nreturn 5\n=== bad3 ===\nx = (1).__class__\n")
check("rejects import/return/dunder", len(r["rejected"]) == 3, r)
s, r = call("POST", "/api/validate", json={"code": "if True:\n    return\n"}); check("validate return hint", not r["ok"] and "ReturnCommand" in r["error"], r)
s, r = call("GET", f"/api/bots/{bid}/commands/export"); check("export roundtrip", "=== /start ===" in r)

call("POST", f"/api/bots/{bid}/start")
def upd(i, **k): 
    with LOCK: QUEUE.append({"update_id": i, **k})
U = {"id": 777, "is_bot": False, "first_name": "Ravi", "language_code": "en"}
C = {"id": 777, "type": "private"}
def msg(text, i, ut="message"): upd(i, **{ut: {"message_id": i, "from": U, "chat": C, "text": text, "date": 1}})

msg("/start ref9", 1)
check("start replies w/ params+preface+userdata", wait_for(lambda: any("ref=ref9 seen=1 brand=Acme" in d.get("text", "") for m, d in SENT)), SENT)
msg("Ravi", 2)
check("handleNextCommand", wait_for(lambda: any("Saved Ravi opt={'k': 1}" in d.get("text", "") for m, d in SENT)), SENT[-3:])
upd(3, callback_query={"id": "cq1", "from": U, "data": "cb_ok x1", "message": {"message_id": 5, "chat": C}})
check("callback_query", wait_for(lambda: any(m == "answerCallbackQuery" and d.get("text") == "clicked x1" and d.get("callback_query_id") == "cq1" for m, d in SENT)), SENT[-3:])
msg("hello there", 4); check("wildcard *", wait_for(lambda: any(d.get("text") == "fallback: hello there" for m, d in SENT)))
msg("/blocked", 5); check("@ gate", wait_for(lambda: any(d.get("text") == "gated" for m, d in SENT)))
msg("/boom", 6); check("! error handler", wait_for(lambda: any("ERR: ZeroDivisionError" in d.get("text", "") for m, d in SENT)), SENT[-3:])
_, errs = call("GET", f"/api/bots/{bid}/errors"); check("error recorded", any(e["command"] == "/boom" for e in errs))
msg("/chain", 7); wait_for(lambda: any(d.get("text") == "d" for m, d in SENT)); time.sleep(0.8)
texts = [d.get("text") for m, d in SENT]
check("chain limit 3", "d" in texts and "e-SHOULD-NOT-RUN" not in texts, texts[-6:])
msg("/later", 8); check("runCommandAfter", wait_for(lambda: any(d.get("text") == "delayed {'x': 5}" for m, d in SENT), 6))
msg("/data", 9); check("Bot data + file send", wait_for(lambda: any(m == "sendDocument" for m, d in SENT)) and any("{'a': [1, 2]}" in (d.get("text") or "") for m, d in SENT))
msg("/member", 10); check("MembershipCheck", wait_for(lambda: any(d.get("text") == "member=True" for m, d in SENT)))
upd(11, edited_message={"message_id": 1, "from": U, "chat": C, "text": "new", "date": 1}); check("special update handler", wait_for(lambda: any(d.get("text") == "edited new" for m, d in SENT)))

s, r = call("GET", f"/api/bots/{bid}/users"); check("user tracked", r["stats"]["total"] == 1 and r["users"][0]["user_id"] == "777", r)
s, csvtxt = call("GET", f"/api/bots/{bid}/users/export?format=csv"); check("users export csv", "777" in csvtxt and "Ravi" in csvtxt, csvtxt)
s, r = call("POST", f"/api/bots/{bid}/users/import", data=b"id,first_name\n1001,A\n1002,B\n777,Dup\n"); time.sleep(1)
_, j = call("GET", f"/api/import-jobs/{r['job']}"); check("users import csv", j["finished"] and j["added"] == 2 and j["skipped"] == 1, j)
s, r = call("POST", f"/api/bots/{bid}/users/import?verify=1", data=json.dumps([{"user_id": 2001}, 2002]).encode()); time.sleep(1)
_, j = call("GET", f"/api/import-jobs/{r['job']}"); check("users import json + verify", j["finished"] and j["added"] == 2, j)
s, bk = call("GET", f"/api/bots/{bid}/backup"); check("backup", bk["bot_data"]["cfg"] == {"a": [1, 2]} and len(bk["users"]) == 5, bk["bot_data"])
s, r = call("POST", f"/api/bots/{bid}/search", json={"q": "bot.sendMessage", "replace": "bot.sendMessage", "regex": False}); check("search/replace", len(r["matches"]) > 5, r["changed"])
s, r = call("POST", f"/api/bots/{bid}/test", json={"user_id": 777, "text": "/boom"}); check("test endpoint reports errors", r["errors"] and "ZeroDivisionError" in r["errors"][0]["error"], r)
s, r = call("POST", f"/api/bots/{bid}/broadcast", json={"command": "c4", "speed": 25}); time.sleep(2.5)
_, bc = call("GET", f"/api/bots/{bid}/broadcasts"); check("broadcast", bc[0]["status"] == "done" and bc[0]["sent"] == 5, bc)
s, r = call("PUT", f"/api/bots/{bid}/data/key", json={"name": "k1", "value": {"x": 1}}); s, r = call("GET", f"/api/bots/{bid}/data/key?name=k1"); check("bot data key api", r["value"] == {"x": 1}, r)
s, r = call("GET", f"/api/bots/{bid}/userdata?user=777"); check("userdata api", any(x["name"] == "seen" for x in r), r)
s, r = call("GET", "/api/overview"); check("overview", r["bots"] == 1 and r["users"] >= 5, r)
s, r = call("GET", f"/api/bots/{bid}"); check("bot has last_active/errors", r["last_active"] and r["errors"] >= 1, r)
s, r = call("GET", "/api/backup-all"); check("backup-all has token", r["bots"][0]["token"] == TOKEN)
s, r2 = call("POST", "/api/restore-all", data=json.dumps(r).encode()); check("restore-all", r2["bots_restored"] == 1, r2)
call("POST", f"/api/bots/{bid}/stop"); s, b = call("GET", f"/api/bots/{bid}"); check("stop", b["running"] is False)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
