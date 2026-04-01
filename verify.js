export default async function handler(req, res) {

  const BOT_TOKEN = process.env.BOT_TOKEN;

  const { id } = req.body;

  await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      chat_id: id,
      text: "/verified_webhook"
    })
  });

  res.status(200).json({ status: "success" });
}
