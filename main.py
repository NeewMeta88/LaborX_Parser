import asyncio
from dotenv import load_dotenv

from app.config import load_config
from app.bot import setup_bot

async def main():
    load_dotenv()
    cfg = load_config()
    bot, dp, _app = setup_bot(cfg)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())