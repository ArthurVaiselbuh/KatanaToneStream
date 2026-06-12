"""LLM-powered patch generator.

Public API: generate_patch(artist, song, api_key, model, on_progress) -> dict
Everything else — phase structure, prompts, catalogs — is internal.
"""

import json
import logging
from collections.abc import Callable

from .katana_catalog import (
    DELAY_TYPES,
    FX_TYPES,
    OD_TYPES,
    PREAMP_TYPES,
    REVERB_TYPES,
)

log = logging.getLogger(__name__)


class PatchGenerationError(Exception):
    """Raised with a clear, user-facing message when generation cannot complete."""


# ---------------------------------------------------------------------------
# Output normalization — models tend to overestimate drive/level parameters
# (amp gain, booster/effect levels), so scale them down before use. Tune the
# factor (1.0 = no change) or add keys to NORMALIZED_PARAMS as needed.
# ---------------------------------------------------------------------------

NORMALIZATION_FACTOR = 1.0

# key -> max value (used to re-clamp after scaling). Only "power"/level params
# belong here — EQ (bass/mid/treble/presence) is left untouched.
NORMALIZED_PARAMS: dict[str, int] = {
    "preamp_gain": 120,  # amp gain
    "od_drive": 120,  # booster/overdrive gain
    "od_level": 100,  # booster/overdrive output level
}


def _normalize_levels(values: dict, factor: float = NORMALIZATION_FACTOR) -> dict:
    out = dict(values)
    for key, hi in NORMALIZED_PARAMS.items():
        raw = out.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[key] = max(0, min(hi, int(round(raw * factor))))
    return out


# ---------------------------------------------------------------------------
# Prompts — edit here without touching any logic
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a guitar tone expert with deep knowledge of the Boss Katana Mk2 amplifier. "
    "Respond ONLY with valid JSON — no prose, no markdown, no code fences."
)

_PROMPT_CHARACTER = """\
Artist: {artist}
Song: {song}
{extra_block}
Describe the overall tone character and which effect categories are active in this recording.
Return a JSON object with exactly these fields:
{{
  "booster_on": <bool>,
  "fx_on": <bool>,
  "delay_on": <bool>,
  "reverb_on": <bool>,
  "overall_character": <string — one paragraph describing the tone>,
  "key_traits": <list of 3-5 short strings, e.g. ["high gain", "mid-scooped", "spring reverb"]>
}}
"""

_PROMPT_TYPES = """\
Tone character: {overall_character}
Key traits: {key_traits}
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


def _call(client, prompt: str) -> dict:
    response = client.completion(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    raw = (response.choices[0].message.content or "").strip()
    log.debug("LLM response: %s", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return valid JSON ({_short(raw, 80)})") from exc


def _friendly_llm_error(exc: Exception, model: str) -> str:
    """Translate a litellm/provider exception into a clear, actionable message."""
    import litellm

    provider = model.split("/", 1)[0] if "/" in model else "the provider"
    if isinstance(exc, litellm.AuthenticationError):
        return "API key was rejected. Open Settings and check your LLM API key."
    if isinstance(exc, litellm.NotFoundError):
        return (
            f"Model '{model}' is not available for {provider}. "
            "Open Settings, click 'Fetch available models', and pick a current one."
        )
    if isinstance(exc, litellm.RateLimitError):
        return "Rate limited by the provider. Wait a moment and try again."
    unreachable = (litellm.Timeout, litellm.APIConnectionError, litellm.ServiceUnavailableError)
    if isinstance(exc, unreachable):
        return f"Could not reach {provider}. Check your connection and try again."
    if isinstance(exc, litellm.BadRequestError):
        return f"{provider} rejected the request: {_short(getattr(exc, 'message', str(exc)))}"
    if isinstance(exc, litellm.APIError):
        return f"{provider} error: {_short(getattr(exc, 'message', str(exc)))}"
    return f"Generation failed: {_short(str(exc))}"


def _clamp(val, lo: int, hi: int, key: str) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except TypeError, ValueError:
        raise ValueError(f"Field '{key}' has invalid value: {val!r}")


def _phase_character(artist: str, song: str, extra: str, client) -> dict:
    prompt = _PROMPT_CHARACTER.format(artist=artist, song=song, extra_block=_extra_block(extra))
    data = _call(client, prompt)
    for key in ("booster_on", "fx_on", "delay_on", "reverb_on", "overall_character", "key_traits"):
        if key not in data:
            raise ValueError(f"Character phase missing field '{key}'")
    return data


def _phase_types(character: dict, extra: str, client) -> dict:
    prompt = _PROMPT_TYPES.format(
        overall_character=character["overall_character"],
        key_traits=character["key_traits"],
        extra_block=_extra_block(extra),
        preamp_options=_catalog_str(PREAMP_TYPES),
        od_options=_catalog_str(OD_TYPES),
        fx_options=_catalog_str(FX_TYPES),
        delay_options=_catalog_str(DELAY_TYPES),
        reverb_options=_catalog_str(REVERB_TYPES),
    )
    data = _call(client, prompt)
    for key in ("preamp_type", "od_type", "fx1_type", "fx2_type", "delay_type", "reverb_type"):
        if key not in data:
            raise ValueError(f"Types phase missing field '{key}'")
    return {
        "preamp_type": _clamp(data["preamp_type"], 0, max(PREAMP_TYPES), "preamp_type"),
        "od_type": _clamp(data["od_type"], 0, max(OD_TYPES), "od_type"),
        "fx1_type": _clamp(data["fx1_type"], 0, max(FX_TYPES), "fx1_type"),
        "fx2_type": _clamp(data["fx2_type"], 0, max(FX_TYPES), "fx2_type"),
        "delay_type": _clamp(data["delay_type"], 0, max(DELAY_TYPES), "delay_type"),
        "reverb_type": _clamp(data["reverb_type"], 0, max(REVERB_TYPES), "reverb_type"),
    }


def _phase_values(artist: str, song: str, types: dict, extra: str, client) -> dict:
    prompt = _PROMPT_VALUES.format(
        artist=artist,
        song=song,
        extra_block=_extra_block(extra),
        preamp_name=PREAMP_TYPES.get(types["preamp_type"], str(types["preamp_type"])),
        od_name=OD_TYPES.get(types["od_type"], str(types["od_type"])),
        fx1_name=FX_TYPES.get(types["fx1_type"], str(types["fx1_type"])),
        fx2_name=FX_TYPES.get(types["fx2_type"], str(types["fx2_type"])),
        delay_name=DELAY_TYPES.get(types["delay_type"], str(types["delay_type"])),
        reverb_name=REVERB_TYPES.get(types["reverb_type"], str(types["reverb_type"])),
    )
    data = _call(client, prompt)
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
    import litellm  # local import — optional dependency

    from .logging_setup import tame_litellm_logging

    tame_litellm_logging()

    effective_model = model.strip() or "openai/gpt-4o"
    artist = artist.strip()
    song = song.strip()
    extra = (extra or "").strip()

    log.info("Generating patch for '%s' by '%s' via %s", song, artist, effective_model)

    class _Client:
        def completion(self, messages, temperature):
            return litellm.completion(
                model=effective_model,
                api_key=api_key or None,
                messages=messages,
                temperature=temperature,
            )

    client = _Client()

    try:
        if on_progress:
            on_progress("Analyzing tone character…")
        character = _phase_character(artist, song, extra, client)

        if on_progress:
            on_progress("Selecting effects and pedals…")
        types = _phase_types(character, extra, client)

        if on_progress:
            on_progress("Dialing in parameters…")
        values = _normalize_levels(_phase_values(artist, song, types, extra, client))

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
