import { getUser, saveUser } from "./data.js"

export default async function handler(req, res) {

  if (req.method !== "POST") {
    return res.status(405).json({ message: "Method not allowed" })
  }

  const { id } = req.body

  const BOT_TOKEN = process.env.BOT_TOKEN

  // 🔥 fingerprint (IP + User-Agent)
  const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress
  const ua = req.headers["user-agent"]

  const fingerprint = ip + "_" + ua

  const existing = getUser(id)

  // 🟡 SAME DEVICE
  if (existing) {
    if (existing === fingerprint) {

      return res.status(200).json({
        status: "info",
        message: "Already Verified"
      })
    }

    // 🔴 DIFFERENT DEVICE
    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        chat_id: id,
        text: "🚫 Different device detected! Access denied."
      })
    })

    return res.status(200).json({
      status: "error",
      message: "Different device"
    })
  }

  // 🟢 NEW USER
  saveUser(id, fingerprint)

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
    message: "Verified successfully"
  })
}
