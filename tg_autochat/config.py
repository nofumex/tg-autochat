from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value.strip() == "":
        return default
    return int(value)


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _read_system_prompt() -> tuple[str, str]:
    env_prompt = os.getenv("LLM_SYSTEM_PROMPT")
    if env_prompt and env_prompt.strip():
        return env_prompt.strip(), "env:LLM_SYSTEM_PROMPT"

    prompt_path = Path(os.getenv("SYSTEM_PROMPT_FILE", os.getenv("BACK_PROMPT_FILE", "back_prompt.md")))
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8-sig").strip(), str(prompt_path)

    return (
        (
            "Ты отвечаешь от имени компании Синай потенциальным агентам из HH. "
            "Отвечай по-русски, спокойно, коротко и по делу. "
            "Не обещай гарантированный доход и не давай юридических гарантий. "
            "Веди диалог к следующему шагу: ФИО агента, понимание условий или контакт потенциального клиента."
        ),
        "built-in fallback",
    )


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int | None
    state_file: Path
    poll_timeout: int
    auto_delete_webhook: bool
    responder_mode: str
    reply_prefix: str
    free_llm_api_key: str | None
    free_llm_base_url: str | None
    free_llm_endpoint: str
    free_llm_model: str
    free_llm_fallback_models: list[str]
    free_llm_timeout: int
    free_llm_max_tokens: int
    llm_system_prompt: str
    llm_system_prompt_source: str


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_API_KEY") or os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_API_KEY is missing in .env")

    free_llm_api_key = os.getenv("FREE_LLM_API_KEY") or os.getenv("LLM_API_KEY")
    free_llm_base_url = os.getenv("BASE_URL") or os.getenv("LLM_BASE_URL")
    has_llm = bool(free_llm_api_key and free_llm_base_url)
    system_prompt, system_prompt_source = _read_system_prompt()
    free_llm_model = os.getenv("FREE_LLM_MODEL", os.getenv("LLM_MODEL", "auto")).strip()
    fallback_models = _csv(os.getenv("FREE_LLM_FALLBACK_MODELS"))
    if not fallback_models:
        fallback_models = [
            free_llm_model,
            "@cf/openai/gpt-oss-120b",
            "@cf/qwen/qwen3-30b-a3b-fp8",
            "DeepSeek-V3.2",
            "DeepSeek-V3.1",
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
            "@cf/meta/llama-4-scout-17b-16e-instruct",
            "gpt-oss-120b",
            "poolside/laguna-m.1:free",
            "poolside/laguna-xs.2:free",
            "groq/compound-mini",
        ]

    return Settings(
        bot_token=bot_token,
        admin_id=_int(os.getenv("ADMIN_ID")),
        state_file=Path(os.getenv("STATE_FILE", ".data/state.json")),
        poll_timeout=int(os.getenv("POLL_TIMEOUT", "30")),
        auto_delete_webhook=_bool(os.getenv("AUTO_DELETE_WEBHOOK"), True),
        responder_mode=os.getenv("RESPONDER_MODE", "llm" if has_llm else "off").strip().lower(),
        reply_prefix=os.getenv("REPLY_PREFIX", "").strip(),
        free_llm_api_key=free_llm_api_key,
        free_llm_base_url=free_llm_base_url,
        free_llm_endpoint=os.getenv("ENDPOINT", "/chat/completions").strip(),
        free_llm_model=free_llm_model,
        free_llm_fallback_models=_unique(fallback_models),
        free_llm_timeout=int(os.getenv("LLM_TIMEOUT", "90")),
        free_llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "700")),
        llm_system_prompt=system_prompt,
        llm_system_prompt_source=system_prompt_source,
    )
