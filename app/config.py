import os
from dataclasses import dataclass


@dataclass
class Config:
    bot_token: str
    portfolio_url: str
    list_url: str = "https://laborx.com/jobs"
    interval_seconds: int = 600
    max_list_items: int = 5
    seen_limit: int = 50
    headless: bool = True
    user_data_dir: str = "laborx_profile"


def load_config() -> Config:
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("TG_BOT_TOKEN is not set in the environment.")

    portfolio_url = os.environ.get("PORTFOLIO_URL")
    if not portfolio_url:
        raise RuntimeError("Не задан PORTFOLIO_URL в env")

    return Config(bot_token=token, portfolio_url=portfolio_url)
