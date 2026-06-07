import json
import httpx
from typing import AsyncGenerator
from app.config import settings

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def stream_chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.3,
) -> AsyncGenerator[dict, None]:
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    tool_calls_buf: dict[int, dict] = {}
    content_buf = ""

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw == "[DONE]":
                    break

                chunk = json.loads(raw)
                choice = chunk["choices"][0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                if delta.get("content"):
                    content_buf += delta["content"]
                    yield {"type": "token", "content": delta["content"]}

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    buf = tool_calls_buf[idx]
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        buf["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        buf["function"]["arguments"] += fn["arguments"]

                if finish_reason == "tool_calls":
                    yield {
                        "type": "finish",
                        "reason": "tool_calls",
                        "tool_calls": list(tool_calls_buf.values()),
                        "content": content_buf or None,
                    }
                    return

                if finish_reason == "stop":
                    yield {"type": "finish", "reason": "stop", "content": content_buf}
                    return


async def complete(messages: list[dict], temperature: float = 0.3) -> str:
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
