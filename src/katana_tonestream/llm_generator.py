"""LLM-powered patch generator.

Public API: generate_patch(artist, song, api_key, model, on_progress) -> dict
Everything else — phase structure, prompts, catalogs — is internal.
"""

import json
import logging
from collections.abc import Callable

import litellm
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from .katana_catalog import (
    DELAY_TYPES,
    FX_TYPES,
    OD_TYPES,
    PREAMP_TYPES,
    REVERB_TYPES,
)
from .logging_setup import tame_litellm_logging

log = logging.getLogger(__name__)


class PatchGenerationError(Exception):
    """Raised with a clear, user-facing message when generation cannot complete."""


# ---------------------------------------------------------------------------
# Prompts — edit here without touching any logic
# ---------------------------------------------------------------------------

# System prompt for the structured (JSON) phases.
_SYSTEM = (
    "You are a guitar tone expert with deep knowledge of the Boss Katana Mk2 amplifier. "
    "Respond ONLY with valid JSON — no prose, no markdown, no code fences."
)

# System prompt for the opening reasoning phase, where free-form prose is wanted.
_SYSTEM_REASONING = (
    "You are a guitar tone expert with deep knowledge of the Boss Katana Mk2 amplifier "
    "and the gear, amps, and pedals that famous guitarists use."
)

_PROMPT_CHARACTER = """\
Artist: {artist}
Song: {song}
{extra_block}
Analyze this guitar tone for the purpose of recreating it on a Boss Katana Mk2.
In a few sentences, describe the overall character (gain level, EQ balance, dynamics)
and which effect categories are audibly active: booster/overdrive, modulation/FX,
delay, and reverb. Mention the specific amps and pedals this artist is known for using.
Write plainly — no JSON, no headings, just your reasoning.
"""

_PROMPT_TYPES = """\
Artist: {artist}
Song: {song}

Tone analysis:
{character}
{extra_block}
Select the best Boss Katana Mk2 options for each slot. Consider which gear and pedals
this artist is associated with. Even if a slot is off, still pick the most plausible type.

Return a JSON object with exactly these fields:
{{
  "preamp_type": <int>,
  "od_type": <int>,
  "fx1_type": <int>,
  "fx2_type": <int>,
  "delay_type": <int>,
  "reverb_type": <int>
}}

Preamp options: {preamp_options}
Booster/OD options: {od_options}
FX slot options: {fx_options}
Delay options: {delay_options}
Reverb options: {reverb_options}
"""

_PROMPT_VALUES = """\
Artist: {artist}
Song: {song}

Tone analysis:
{character}

Preamp: {preamp_name}
Booster/OD: {od_name}
FX1: {fx1_name}
FX2: {fx2_name}
Delay: {delay_name}
Reverb: {reverb_name}
{extra_block}
Provide the exact dial settings. Return a JSON object with exactly these fields:
{{
  "preamp_gain": <int 0-120>,
  "bass": <int 0-100>,
  "mid": <int 0-100>,
  "treble": <int 0-100>,
  "presence": <int 0-100>,
  "od_on": <bool>,
  "od_drive": <int 0-120>,
  "od_level": <int 0-100>,
  "fx1_on": <bool>,
  "fx2_on": <bool>,
  "delay_on": <bool>,
  "delay_level": <int 0-120>,
  "reverb_on": <bool>,
  "reverb_level": <int 0-100>,
  "confidence": <int 0-100>
}}

od_drive is the booster/overdrive pedal's gain; od_level is its output level.
delay_level and reverb_level are the effect mix levels for those sections.

EQ at 50 = flat. confidence reflects how precisely this tone is documented.
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _catalog_str(mapping: dict[int, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in mapping.items())


def _extra_block(extra: str) -> str:
    extra = (extra or "").strip()
    if not extra:
        return ""
    return f"\nAdditional user request (give this strong weight): {extra}\n"


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _schema(*, integers: tuple[str, ...] = (), booleans: tuple[str, ...] = ()) -> dict:
    """Build a strict JSON schema (all fields required, no extras) for a phase."""
    props: dict[str, dict] = {name: {"type": "integer"} for name in integers}
    props.update({name: {"type": "boolean"} for name in booleans})
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_TYPES_SCHEMA = _schema(
    integers=("preamp_type", "od_type", "fx1_type", "fx2_type", "delay_type", "reverb_type")
)
_VALUES_SCHEMA = _schema(
    integers=(
        "preamp_gain",
        "bass",
        "mid",
        "treble",
        "presence",
        "od_drive",
        "od_level",
        "delay_level",
        "reverb_level",
        "confidence",
    ),
    booleans=("od_on", "fx1_on", "fx2_on", "delay_on", "reverb_on"),
)


def _supports_schema(model: str) -> bool:
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:
        return False


def _json_kwargs(model: str, schema: dict, schema_name: str) -> dict:
    """litellm kwargs that force or coax a structured JSON reply for ``model``.

    Prefers the provider's native JSON-schema enforcement when supported; falls
    back to Ollama's native JSON mode, and finally to nothing (we then rely on
    the system prompt plus the parse-retry in ``_call_json``).
    """
    if _supports_schema(model):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
        }
    provider = model.split("/", 1)[0] if "/" in model else ""
    if provider in ("ollama", "ollama_chat"):
        return {"format": "json"}
    return {}


def _strip_fences(raw: str) -> str:
    """Drop a leading ```/```json fence and trailing ``` if the model added them."""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    s = s[3:]
    if s[:4].lower() == "json":
        s = s[4:]
    s = s.strip()
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _call_text(client, prompt: str, *, temperature: float = 0.4) -> str:
    """Free-form (non-JSON) completion — used for the opening reasoning phase."""
    tame_litellm_logging()
    response = client.complete(
        messages=[
            {"role": "system", "content": _SYSTEM_REASONING},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        extra_kwargs={},
    )
    return (response.choices[0].message.content or "").strip()


def _call_json(client, prompt: str, schema: dict, schema_name: str) -> dict:
    """Structured-JSON completion with a one-shot corrective retry.

    When the provider supports JSON-schema output the reply is guaranteed valid
    and the retry never fires; the retry only rescues providers that fall back
    to prompt-only JSON and occasionally wrap or malform it.
    """
    tame_litellm_logging()
    extra_kwargs = _json_kwargs(client.model, schema, schema_name)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ]
    last_raw = ""
    for _ in range(2):
        response = client.complete(messages=messages, temperature=0.3, extra_kwargs=extra_kwargs)
        raw = (response.choices[0].message.content or "").strip()
        log.debug("LLM response: %s", raw)
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            last_raw = raw
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Reply with ONLY the JSON object — "
                        "no prose, no code fences."
                    ),
                },
            ]
    raise ValueError(f"model did not return valid JSON ({_short(last_raw, 80)})")


def _friendly_llm_error(exc: Exception, model: str) -> str:
    """Translate a litellm/provider exception into a clear, actionable message."""
    provider = model.split("/", 1)[0] if "/" in model else "the provider"
    if isinstance(exc, AuthenticationError):
        return "API key was rejected. Open Settings and check your LLM API key."
    if isinstance(exc, NotFoundError):
        return (
            f"Model '{model}' is not available for {provider}. "
            "Open Settings, click 'Fetch available models', and pick a current one."
        )
    if isinstance(exc, RateLimitError):
        return "Rate limited by the provider. Wait a moment and try again."
    if isinstance(exc, (Timeout, APIConnectionError, ServiceUnavailableError)):
        return f"Could not reach {provider}. Check your connection and try again."
    if isinstance(exc, BadRequestError):
        return f"{provider} rejected the request: {_short(getattr(exc, 'message', str(exc)))}"
    if isinstance(exc, APIError):
        return f"{provider} error: {_short(getattr(exc, 'message', str(exc)))}"
    return f"Generation failed: {_short(str(exc))}"


def _clamp(val, lo: int, hi: int, key: str) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        raise ValueError(f"Field '{key}' has invalid value: {val!r}")


def _snap_type(val, catalog: dict[int, str], key: str) -> int:
    """Coerce an LLM-chosen type id to the nearest valid catalog entry.

    The catalogs have gaps (types with no Tone Studio editor support are not
    offered), so out-of-catalog picks snap to the closest offered id instead
    of merely clamping to the range ends.
    """
    try:
        v = int(val)
    except (TypeError, ValueError):
        raise ValueError(f"Field '{key}' has invalid value: {val!r}")
    if v in catalog:
        return v
    return min(catalog, key=lambda k: abs(k - v))


def _phase_character(artist: str, song: str, extra: str, client) -> str:
    """Free-form tone analysis that seeds the later structured phases."""
    prompt = _PROMPT_CHARACTER.format(artist=artist, song=song, extra_block=_extra_block(extra))
    text = _call_text(client, prompt)
    if not text:
        raise ValueError("Character phase returned an empty response")
    return text


def _phase_types(artist: str, song: str, character: str, extra: str, client) -> dict:
    prompt = _PROMPT_TYPES.format(
        artist=artist,
        song=song,
        character=_short(character, 1200),
        extra_block=_extra_block(extra),
        preamp_options=_catalog_str(PREAMP_TYPES),
        od_options=_catalog_str(OD_TYPES),
        fx_options=_catalog_str(FX_TYPES),
        delay_options=_catalog_str(DELAY_TYPES),
        reverb_options=_catalog_str(REVERB_TYPES),
    )
    data = _call_json(client, prompt, _TYPES_SCHEMA, "katana_types")
    for key in ("preamp_type", "od_type", "fx1_type", "fx2_type", "delay_type", "reverb_type"):
        if key not in data:
            raise ValueError(f"Types phase missing field '{key}'")
    return {
        "preamp_type": _snap_type(data["preamp_type"], PREAMP_TYPES, "preamp_type"),
        "od_type": _snap_type(data["od_type"], OD_TYPES, "od_type"),
        "fx1_type": _snap_type(data["fx1_type"], FX_TYPES, "fx1_type"),
        "fx2_type": _snap_type(data["fx2_type"], FX_TYPES, "fx2_type"),
        "delay_type": _snap_type(data["delay_type"], DELAY_TYPES, "delay_type"),
        "reverb_type": _snap_type(data["reverb_type"], REVERB_TYPES, "reverb_type"),
    }


def _phase_values(artist: str, song: str, character: str, types: dict, extra: str, client) -> dict:
    prompt = _PROMPT_VALUES.format(
        artist=artist,
        song=song,
        character=_short(character, 1200),
        extra_block=_extra_block(extra),
        preamp_name=PREAMP_TYPES.get(types["preamp_type"], str(types["preamp_type"])),
        od_name=OD_TYPES.get(types["od_type"], str(types["od_type"])),
        fx1_name=FX_TYPES.get(types["fx1_type"], str(types["fx1_type"])),
        fx2_name=FX_TYPES.get(types["fx2_type"], str(types["fx2_type"])),
        delay_name=DELAY_TYPES.get(types["delay_type"], str(types["delay_type"])),
        reverb_name=REVERB_TYPES.get(types["reverb_type"], str(types["reverb_type"])),
    )
    data = _call_json(client, prompt, _VALUES_SCHEMA, "katana_values")
    required = (
        "preamp_gain",
        "bass",
        "mid",
        "treble",
        "presence",
        "od_on",
        "od_drive",
        "od_level",
        "fx1_on",
        "fx2_on",
        "delay_on",
        "delay_level",
        "reverb_on",
        "reverb_level",
        "confidence",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"Values phase missing field '{key}'")
    return {
        "preamp_gain": _clamp(data["preamp_gain"], 0, 120, "preamp_gain"),
        "bass": _clamp(data["bass"], 0, 100, "bass"),
        "mid": _clamp(data["mid"], 0, 100, "mid"),
        "treble": _clamp(data["treble"], 0, 100, "treble"),
        "presence": _clamp(data["presence"], 0, 100, "presence"),
        "od_on": bool(data["od_on"]),
        "od_drive": _clamp(data["od_drive"], 0, 120, "od_drive"),
        "od_level": _clamp(data["od_level"], 0, 100, "od_level"),
        "fx1_on": bool(data["fx1_on"]),
        "fx2_on": bool(data["fx2_on"]),
        "delay_on": bool(data["delay_on"]),
        "delay_level": _clamp(data["delay_level"], 0, 120, "delay_level"),
        "reverb_on": bool(data["reverb_on"]),
        "reverb_level": _clamp(data["reverb_level"], 0, 100, "reverb_level"),
        "confidence": _clamp(data["confidence"], 0, 100, "confidence"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_patch(
    artist: str,
    song: str,
    api_key: str,
    model: str,
    on_progress: Callable[[str], None] | None = None,
    extra: str = "",
) -> dict:
    """Generate Boss Katana Mk2 parameters for artist/song via a 3-phase LLM conversation.

    ``extra`` is an optional free-text request from the user (e.g. "brighter, less gain")
    that is woven into every phase's prompt when provided.

    Returns a dict with all patch fields. Always raises PatchGenerationError (with a
    clear, user-facing message) on any failure — provider errors, an invalid/retired
    model, or an unparseable response.
    """
    effective_model = model.strip() or "openai/gpt-4o"
    artist = artist.strip()
    song = song.strip()
    extra = (extra or "").strip()

    log.info("Generating patch for '%s' by '%s' via %s", song, artist, effective_model)

    # How JSON is requested per call is decided in _json_kwargs (native schema
    # enforcement where supported, Ollama JSON mode otherwise); the client just
    # forwards whatever kwargs the caller computed.
    class _Client:
        model = effective_model

        def complete(self, messages, temperature, extra_kwargs):
            return litellm.completion(
                model=effective_model,
                api_key=api_key or None,
                messages=messages,
                temperature=temperature,
                **extra_kwargs,
            )

    client = _Client()

    try:
        if on_progress:
            on_progress("Analyzing tone character…")
        character = _phase_character(artist, song, extra, client)

        if on_progress:
            on_progress("Selecting effects and pedals…")
        types = _phase_types(artist, song, character, extra, client)

        if on_progress:
            on_progress("Dialing in parameters…")
        values = _phase_values(artist, song, character, types, extra, client)

        return {**types, **values}
    except ValueError as exc:
        # Our own parse/validation failures (bad/missing JSON fields).
        raise PatchGenerationError(
            f"The model's response could not be used: {_short(str(exc))}. "
            "Try again or pick a different model."
        ) from exc
    except Exception as exc:
        # litellm/provider failures — invalid model, auth, rate limit, network, etc.
        log.warning("LLM call failed: %s", exc)
        raise PatchGenerationError(_friendly_llm_error(exc, effective_model)) from exc
