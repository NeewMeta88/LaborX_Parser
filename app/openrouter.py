import asyncio
import os
from typing import Optional

import aiohttp

from .config import Config

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_COMPLETIONS_URL = f"{OPENROUTER_BASE_URL}/chat/completions"


class OpenRouterError(RuntimeError):
    pass


async def openrouter_generate(
        prompt: str,
        cfg: Config,
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        timeout_seconds: int = 90,
        max_retries: int = 3,
) -> str:
    if not cfg.openrouter_api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is missing.")
    if not cfg.openrouter_model:
        raise OpenRouterError("OPENROUTER_MODEL is missing.")

    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
    }

    site_url = os.environ.get("OPENROUTER_SITE_URL")
    app_title = os.environ.get("OPENROUTER_APP_TITLE")
    if site_url:
        headers["HTTP-Referer"] = site_url
    if app_title:
        headers["X-Title"] = app_title

    body = {
        "model": cfg.openrouter_model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }

    if reasoning_enabled:
        body["reasoning"] = {"enabled": True}

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(CHAT_COMPLETIONS_URL, headers=headers, json=body) as resp:
                    if resp.status != 200:
                        txt = await resp.text()
                        raise OpenRouterError(f"HTTP {resp.status}: {txt[:400]}")

                    data = await resp.json()

            try:
                content = data["choices"][0]["message"]["content"]
            except Exception:
                raise OpenRouterError(f"Unexpected response format: {str(data)[:400]}")

            content = (content or "").strip()
            return content if content else "(empty)"

        except (aiohttp.ClientError, asyncio.TimeoutError, OpenRouterError) as e:
            last_err = str(e)

            if attempt >= max_retries:
                break

            backoff = 1.2 * (1.7 ** attempt)
            await asyncio.sleep(backoff)

    raise OpenRouterError(last_err or "OpenRouter request failed.")
