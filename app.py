from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import hashlib

app = Flask(__name__)
CORS(app)

BOT_TOKEN = "8645066724:AAFwkbpnQDmpAjEf-lf-3nraM-A72Q9pd8Q"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# memory storage
verified_devices = {}

# 🌐 frontend serve
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# 🔐 verify API
@app.route("/verify", methods=["POST"])
def verify():
    data = request.json

    user_id = str(data.get("id"))
    name = data.get("name")

    # 🔥 fingerprint
    fingerprint = hashlib.md5(
        (request.remote_addr + request.headers.get("User-Agent")).encode()
    ).hexdigest()

    # 🟡 same device
    if user_id in verified_devices:
        if verified_devices[user_id] == fingerprint:

            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "✔️ Already Verified (Same Device)"
            })

            return jsonify({
                "status": "info",
                "message": "Already Verified ✅"
            })

        else:
            # 🔴 different device
            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "🚫 Different Device Detected!"
            })

            return jsonify({
                "status": "error",
                "message": "Different Device 🚫"
            })

    # 🟢 new verification
    verified_devices[user_id] = fingerprint

    # 🔥 AUTO COMMAND TRIGGER
    requests.post(f"{API}/sendMessage", json={
        "chat_id": user_id,
        "text": "/verified_webhook"
    })

    return jsonify({
        "status": "success",
        "message": "Verification Successful ✅"
    })


app.run(host="0.0.0.0", port=5000)
