"""LLM provider catalog and model discovery.

list_models() queries a provider's own /models endpoint (via litellm's
get_valid_models with check_provider_endpoint=True) so the returned list reflects
what the given API key can actually call — no hard-coded model names that go stale.
"""

import logging

import litellm

from . import config
from .logging_setup import tame_litellm_logging

log = logging.getLogger(__name__)

# (display label, litellm provider key). Keys must be valid litellm LlmProviders values.
PROVIDERS: list[tuple[str, str]] = [
    ("OpenAI", "openai"),
    ("Anthropic Claude", "anthropic"),
    ("Google Gemini", "gemini"),
    ("xAI Grok", "xai"),
    ("Mistral", "mistral"),
    ("Groq", "groq"),
    ("DeepSeek", "deepseek"),
    ("Ollama (local)", "ollama"),
]


def configured_providers() -> list[tuple[str, str]]:
    """Return (label, provider_key) for every provider that has an API key stored.

    Ollama is local and needs no key, so it is always available.
    """
    out = []
    for label, key in PROVIDERS:
        if key == "ollama" or config.llm_api_key(key):
            out.append((label, key))
    return out


# Substrings that mark a model as non-chat (embeddings, media, etc.) — filtered out
# so the picker only shows models usable for chat completion.
_NON_CHAT_MARKERS = (
    "embed",
    "imagen",
    "image",
    "veo",
    "tts",
    "whisper",
    "audio",
    "rerank",
    "moderation",
    "dall-e",
    "stable-diffusion",
    "flux",
    "guard",
)


def _is_chat_model(name: str) -> bool:
    low = name.lower()
    return not any(marker in low for marker in _NON_CHAT_MARKERS)


def _normalize(name: str, provider: str) -> str:
    if name.startswith(f"{provider}/"):
        return name
    if "/" in name:
        return name
    return f"{provider}/{name}"


def list_models(provider: str, api_key: str) -> list[str]:
    """Return chat-capable model strings available to api_key for provider.

    Returns an empty list if the provider can't be queried (bad key, offline, or a
    provider that doesn't support endpoint discovery). Never raises.
    """
    tame_litellm_logging()
    try:
        raw = litellm.get_valid_models(
            check_provider_endpoint=True,
            custom_llm_provider=provider,
            api_key=api_key or None,
        )
    except Exception:
        log.warning("Model discovery failed for provider %s", provider, exc_info=True)
        return []

    models = {_normalize(m, provider) for m in raw if _is_chat_model(m)}
    return sorted(models)
