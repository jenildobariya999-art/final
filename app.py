from flask import Flask, request, jsonify, send_from_directory
import requests
import hashlib

app = Flask(__name__)

BOT_TOKEN = "8645066724:AAFwkbpnQDmpAjEf-lf-3nraM-A72Q9pd8Q"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Store verified devices (temporary memory)
verified_devices = {}

# Serve frontend
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# 🔐 VERIFY API
@app.route("/verify", methods=["POST"])
def verify():
    data = request.json

    user_id = str(data.get("id"))
    name = data.get("name")

    # 🔥 DEVICE FINGERPRINT (IMPORTANT PART)
    fingerprint = hashlib.md5(
        (request.remote_addr + request.headers.get("User-Agent")).encode()
    ).hexdigest()

    # 🚫 Check multiple device
    if user_id in verified_devices:
        if verified_devices[user_id] != fingerprint:
            return jsonify({
                "status": "error",
                "message": "Multiple devices detected 🚫"
            })

    # ✅ Save fingerprint
    verified_devices[user_id] = fingerprint

    # ✅ Send success message to user
    requests.post(f"{API}/sendMessage", json={
        "chat_id": user_id,
        "text": "✅ Device Verified Successfully!"
    })

    return jsonify({
        "status": "success",
        "message": "Verified"
    })


# 🚀 Run server
app.run(host="0.0.0.0", port=5000)
