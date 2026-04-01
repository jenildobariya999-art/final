export default async function handler(req, res) {

  if (req.method !== "POST") {
    return res.status(200).json({ status: "ok" });
  }

  const { id } = req.body;

  const BOT_TOKEN = process.env.BOT_TOKEN;

  if (!id) {
    return res.status(400).json({
      status: "error",
      message: "User ID missing"
    });
  }

  try {

    await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        chat_id: id,
        text: "/verified_webhook"
      })
    });

    return res.status(200).json({
      status: "success",
      message: "Verified Successfully"
    });

  } catch (err) {
    return res.status(500).json({
      status: "error",
      message: "Server Error"
    });
  }
}
