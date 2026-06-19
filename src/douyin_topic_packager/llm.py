from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping
from urllib import parse, request


OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": "https://api.openai.com",
    "openai-compatible": "",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode",
    "kimi": "https://api.moonshot.cn",
    "moonshot": "https://api.moonshot.cn",
    "zhipu": "https://open.bigmodel.cn/api/paas",
    "minimax": "https://api.minimax.io",
    "minimax-cn": "https://api.minimaxi.com",
}

NATIVE_PROVIDERS = {
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}


def load_dotenv(path: str | Path = ".env") -> None:
    source = Path(path)
    if not source.exists():
        return
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def provider_presets() -> Dict[str, Dict[str, str]]:
    presets = {
        key: {"type": "openai-compatible", "base_url": value}
        for key, value in OPENAI_COMPATIBLE_PROVIDERS.items()
        if key != "openai-compatible"
    }
    presets["openai-compatible"] = {"type": "openai-compatible", "base_url": "user-defined"}
    presets["anthropic"] = {"type": "anthropic", "base_url": NATIVE_PROVIDERS["anthropic"]}
    presets["gemini"] = {"type": "gemini", "base_url": NATIVE_PROVIDERS["gemini"]}
    return presets


@dataclass
class LLMConfig:
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: int = 180

    @property
    def normalized_provider(self) -> str:
        return (self.provider or "").strip().lower().replace("_", "-")

    @property
    def is_configured(self) -> bool:
        return bool(self.normalized_provider and self.model.strip() and self.api_key.strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        source = env or os.environ
        try:
            timeout = int(source.get("LLM_TIMEOUT", "180"))
        except ValueError:
            timeout = 180
        return cls(
            provider=source.get("LLM_PROVIDER", ""),
            model=source.get("LLM_MODEL", ""),
            api_key=source.get("LLM_API_KEY", ""),
            base_url=source.get("LLM_BASE_URL", ""),
            timeout=timeout,
        )


class LLMClient:
    def __init__(
        self,
        provider: str = "",
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        timeout: int | None = None,
    ):
        env_config = LLMConfig.from_env()
        self.config = LLMConfig(
            provider=provider or env_config.provider,
            model=model or env_config.model,
            api_key=api_key or env_config.api_key,
            base_url=base_url or env_config.base_url,
            timeout=timeout if timeout is not None else env_config.timeout,
        )

    def complete(self, messages: List[Dict[str, str]], temperature: float = 0.3, max_tokens: int = 5000) -> str:
        if not self.config.is_configured:
            raise RuntimeError("LLM 未配置，请设置 LLM_PROVIDER、LLM_MODEL、LLM_API_KEY")
        last_error = ""
        for attempt in range(3):
            try:
                return self._post(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last_error}")

    def _post(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        provider = self.config.normalized_provider
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return self._post_openai_compatible(provider, messages, temperature, max_tokens)
        if provider == "anthropic":
            return self._post_anthropic(messages, temperature, max_tokens)
        if provider == "gemini":
            return self._post_gemini(messages, temperature, max_tokens)
        raise ValueError(f"不支持的 LLM_PROVIDER：{self.config.provider}")

    def _resolve_base_url(self, provider: str) -> str:
        if self.config.base_url.strip():
            return self.config.base_url.rstrip("/")
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            host = OPENAI_COMPATIBLE_PROVIDERS[provider]
        else:
            host = NATIVE_PROVIDERS.get(provider, "")
        if not host:
            raise ValueError(f"{provider} 需要配置 LLM_BASE_URL")
        return host.rstrip("/")

    def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        req = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=self.config.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def _post_openai_compatible(
        self,
        provider: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.config.model.startswith("gpt-5"):
            payload.pop("temperature", None)
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = max_tokens
        if provider in {"minimax", "minimax-cn"}:
            payload["thinking"] = {"type": "disabled"}
        data = self._post_json(
            f"{self._resolve_base_url(provider)}/v1/chat/completions",
            {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )
        return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    def _post_anthropic(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        system_parts: List[str] = []
        chat_messages: List[Dict[str, str]] = []
        for item in messages:
            role = item.get("role", "user")
            content = item.get("content", "")
            if role == "system":
                system_parts.append(content)
            else:
                chat_messages.append({"role": role if role in {"user", "assistant"} else "user", "content": content})
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": chat_messages or [{"role": "user", "content": "\n".join(system_parts)}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts and chat_messages:
            payload["system"] = "\n".join(system_parts)
        data = self._post_json(
            f"{self._resolve_base_url('anthropic')}/v1/messages",
            {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload,
        )
        parts = data.get("content") or []
        return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))

    def _post_gemini(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        contents = []
        system_parts: List[str] = []
        for item in messages:
            role = item.get("role", "user")
            content = item.get("content", "")
            if role == "system":
                system_parts.append(content)
            else:
                contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
        if system_parts:
            contents.insert(0, {"role": "user", "parts": [{"text": "\n".join(system_parts)}]})
        model = parse.quote(self.config.model, safe="")
        key = parse.quote(self.config.api_key, safe="")
        data = self._post_json(
            f"{self._resolve_base_url('gemini')}/v1beta/models/{model}:generateContent?key={key}",
            {"Content-Type": "application/json"},
            {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}},
        )
        candidates = data.get("candidates") or []
        content = ((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))


def parse_json_from_llm_text(text: str) -> Any:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.S | re.I).strip()
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[1].strip()
    if "<think>" in cleaned:
        cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.S | re.I).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if not cleaned:
        return None
    if cleaned[0] not in "[{":
        starts = [idx for idx in (cleaned.find("["), cleaned.find("{")) if idx >= 0]
        if starts:
            cleaned = cleaned[min(starts):]
    if cleaned.startswith("[") and "]" in cleaned:
        cleaned = cleaned[: cleaned.rfind("]") + 1]
    elif cleaned.startswith("{") and "}" in cleaned:
        cleaned = cleaned[: cleaned.rfind("}") + 1]
    cleaned = re.sub(r",(\s*[\]}])", r"\1", cleaned)
    return json.loads(cleaned)
