import os
from dataclasses import dataclass

@dataclass
class Config:
    bot_token: str
    list_url: str = "https://laborx.com/jobs"
    interval_seconds: int = 600
    max_list_items: int = 5
    seen_limit: int = 50
    headless: bool = True
    user_data_dir: str = "laborx_profile"

def load_config() -> Config:
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан TG_BOT_TOKEN в env")
    return Config(bot_token=token)
