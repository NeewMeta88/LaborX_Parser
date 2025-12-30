import asyncio
from dotenv import load_dotenv

from app.config import load_config
from app.bot import setup_bot

try:
    from aiogram.utils.backoff import BackoffConfig
except Exception:
    BackoffConfig = None


async def main():
    load_dotenv()
    cfg = load_config()

    bot = None
    dp = None
    app = None

    try:
        bot, dp, app = setup_bot(cfg)

        start_polling_kwargs = {
            "polling_timeout": 30,
            "allowed_updates": dp.resolve_used_update_types(),
        }

        if BackoffConfig is not None:
            start_polling_kwargs["backoff_config"] = BackoffConfig(
                min_delay=5.0,
                max_delay=60.0,
                factor=2.0,
                jitter=0.1,
            )

        await dp.start_polling(bot, **start_polling_kwargs)

    finally:
        if bot is not None:
            await bot.session.close()
        if app is not None and getattr(app, "http", None) is not None and not app.http.closed:
            await app.http.close()


if __name__ == "__main__":
    asyncio.run(main())
