import asyncio
import os
from typing import Any, Dict, Tuple
from typing import Optional

import aiohttp

from .config import Config
from .state import RuntimeState

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_COMPLETIONS_URL = f"{OPENROUTER_BASE_URL}/chat/completions"


class OpenRouterError(RuntimeError):
    pass


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


async def openrouter_get_key(cfg, *, timeout_seconds: int = 10) -> Dict[str, Any]:
    url = f"{OPENROUTER_BASE_URL}/key"
    headers = {"Authorization": f"Bearer {cfg.openrouter_api_key}"}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"/key failed HTTP {resp.status}: {str(data)[:300]}")
            return data


async def openrouter_get_credits(cfg, *, timeout_seconds: int = 10) -> Tuple[float, float]:
    url = f"{OPENROUTER_BASE_URL}/credits"
    headers = {"Authorization": f"Bearer {cfg.openrouter_api_key}"}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            payload = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"/credits failed HTTP {resp.status}: {str(payload)[:300]}")

            d = payload.get("data") or {}
            return float(d.get("total_credits", 0.0)), float(d.get("total_usage", 0.0))


async def openrouter_get_free_daily_limit(cfg) -> int:
    total_credits, _ = await openrouter_get_credits(cfg)
    return 1000 if total_credits >= 10.0 else 50


def _to_int(v: Optional[str]) -> Optional[int]:
    if not v:
        return None
    try:
        return int(float(v))
    except Exception:
        return None


async def openrouter_generate(
        prompt: str,
        cfg: Config,
        *,
        state: RuntimeState | None = None,
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
                    if state is not None:
                        state.or_limit = _to_int(resp.headers.get("X-RateLimit-Limit"))
                        state.or_remaining = _to_int(resp.headers.get("X-RateLimit-Remaining"))
                        state.or_reset_ms = _to_int(resp.headers.get("X-RateLimit-Reset"))

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
