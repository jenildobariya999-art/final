export default async function handler(req, res) {

  if (req.method !== "POST") {
    return res.status(405).json({ message: "Method not allowed" });
  }

  const { id } = req.body;

  const BOT_TOKEN = process.env.BOT_TOKEN;

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
      message: "Verified"
    });

  } catch (err) {
    return res.status(500).json({
      status: "error",
      message: "Server error"
    });
  }
}
