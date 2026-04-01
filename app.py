from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import hashlib
import os

app = Flask(__name__)
CORS(app)

BOT_TOKEN = "8645066724:AAFwkbpnQDmpAjEf-lf-3nraM-A72Q9pd8Q"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

verified_devices = {}

# 🌐 Serve frontend
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# 🔁 Ping route (no sleep help)
@app.route("/ping")
def ping():
    return "OK"

# 🔐 Verify API
@app.route("/verify", methods=["POST"])
def verify():
    data = request.json

    user_id = str(data.get("id"))

    fingerprint = hashlib.md5(
        (request.remote_addr + request.headers.get("User-Agent")).encode()
    ).hexdigest()

    # 🟡 Same device
    if user_id in verified_devices:
        if verified_devices[user_id] == fingerprint:

            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "✔️ Already Verified (Same Device)"
            })

            return jsonify({
                "status": "info",
                "message": "Already Verified"
            })

        else:
            # 🔴 Different device
            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "🚫 Different Device Detected!"
            })

            return jsonify({
                "status": "error",
                "message": "Different Device"
            })

    # 🟢 New verification
    verified_devices[user_id] = fingerprint

    # 🔥 AUTO VERIFY COMMAND
    requests.post(f"{API}/sendMessage", json={
        "chat_id": user_id,
        "text": "/verified_webhook"
    })

    return jsonify({
        "status": "success",
        "message": "Verification Successful"
    })

# 🚀 Run (Koyeb compatible)
port = int(os.environ.get("PORT", 8000))
app.run(host="0.0.0.0", port=port)
