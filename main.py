import asyncio
from dotenv import load_dotenv

from app.config import load_config
from app.bot import setup_bot


async def main():
    load_dotenv()
    cfg = load_config()

    bot = None
    dp = None
    app = None

    try:
        bot, dp, app = setup_bot(cfg)
        await dp.start_polling(bot)
    finally:
        if bot is not None:
            await bot.session.close()

        if app is not None and getattr(app, "http", None) is not None and not app.http.closed:
            await app.http.close()


if __name__ == "__main__":
    asyncio.run(main())
