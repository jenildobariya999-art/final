let users = {}

export default async function handler(req, res) {

  if (req.method !== "POST") {
    return res.status(405).json({
      status: "error",
      message: "Method not allowed"
    })
  }

  try {

    const { id } = req.body

    if (!id) {
      return res.status(400).json({
        status: "error",
        message: "User ID missing"
      })
    }

    const BOT_TOKEN = process.env.BOT_TOKEN

    if (!BOT_TOKEN) {
      return res.status(500).json({
        status: "error",
        message: "Bot token not set"
      })
    }

    const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress
    const ua = req.headers["user-agent"]

    const fingerprint = ip + "_" + ua

    // SAME DEVICE
    if (users[id] && users[id] === fingerprint) {
      return res.status(200).json({
        status: "info",
        message: "Already Verified (Same Device)"
      })
    }

    // DIFFERENT DEVICE
    if (users[id] && users[id] !== fingerprint) {

      await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          chat_id: id,
          text: "🚫 Different device detected!"
        })
      })

      return res.status(200).json({
        status: "error",
        message: "Different Device Detected"
      })
    }

    // NEW USER
    users[id] = fingerprint

    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        chat_id: id,
        text: "/verified_webhook"
      })
    })

    return res.status(200).json({
      status: "success",
      message: "Verified Successfully"
    })

  } catch (err) {

    return res.status(500).json({
      status: "error",
      message: "Server Error: " + err.message
    })
  }
}
