from flask import Flask, request, jsonify, send_from_directory
import requests
import hashlib

app = Flask(__name__)

BOT_TOKEN = "8645066724:AAFwkbpnQDmpAjEf-lf-3nraM-A72Q9pd8Q"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Store verified devices
verified_devices = {}

# 🌐 Serve frontend
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# 🔐 VERIFY API
@app.route("/verify", methods=["POST"])
def verify():
    data = request.json

    user_id = str(data.get("id"))
    name = data.get("name")

    # 🔥 DEVICE FINGERPRINT
    fingerprint = hashlib.md5(
        (request.remote_addr + request.headers.get("User-Agent")).encode()
    ).hexdigest()

    # 🟡 Already verified same device
    if user_id in verified_devices:
        if verified_devices[user_id] == fingerprint:

            # Send Telegram message
            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "✔️ Already Verified on this device"
            })

            return jsonify({
                "status": "info",
                "message": "Already Verified ✅ (Same Device)"
            })

        else:
            # 🔴 Different device detected
            requests.post(f"{API}/sendMessage", json={
                "chat_id": user_id,
                "text": "🚫 Multiple Devices Detected! Access Denied."
            })

            return jsonify({
                "status": "error",
                "message": "Different Device Detected 🚫"
            })

    # 🟢 New verification
    verified_devices[user_id] = fingerprint

    requests.post(f"{API}/sendMessage", json={
        "chat_id": user_id,
        "text": "/verified_webhook"
    })

    return jsonify({
        "status": "success",
        "message": "Verification Successful ✅"
    })


# 🚀 Run server
app.run(host="0.0.0.0", port=5000)
