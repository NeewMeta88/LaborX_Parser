# LaborX Parser Telegram Bot

This project monitors the LaborX jobs page and sends new job listings to you via a Telegram bot.

---

## Requirements

- Python 3.13
- Telegram account
- A Telegram bot token from **@BotFather**

---

## Setup

### 1) Clone the repository
```bash
git clone https://github.com/NeewMeta88/LaborX_Parser.git
```

### 2) Create and activate a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Install dependencies and Playwright browser
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Telegram Bot Setup

1) Open **@BotFather**
2) Create a new bot with `/newbot`
3) Copy the **bot token** (looks like `123456:ABC...`)

> You don’t need to manually set a `chat_id`. The bot will automatically use your DM chat when you run `/start`.

---

## Environment Variables

1) Create a `.env` file (in the project root)
2) Copy values from `.env.example` and fill in your token

Example:
```env
TG_BOT_TOKEN=123456:ABCDEF...
```

---

## Run

```bash
python main.py
```

Open your bot in Telegram and use the commands below.

---

## Commands

```
/start — start monitoring
/stop — stop monitoring
/status — check parser status
```

---

## Notes

- On first start, the bot may send several messages (depending on how many jobs are currently in the top list and how long the descriptions are).