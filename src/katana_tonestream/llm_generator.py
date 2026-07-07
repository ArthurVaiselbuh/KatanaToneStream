"""LLM-powered patch generator.

Public API:
  ToneSession — holds one LLM conversation: generate() runs the 3-phase
    creation flow, refine() continues it chat-style with partial param updates.
    refine() also works from turn one (free mode: the user describes the tone
    directly) since the system message carries the catalogs and field ranges.
  generate_patch(artist, song, api_key, model, ...) -> dict — one-shot wrapper
    around a throwaway ToneSession, kept for callers that don't need refinement.

Everything else — phase structure, prompts, catalogs — is internal.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

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

# A single system prompt governs the whole multi-step conversation (generation
# phases, free chat, and refinement alike); each user turn states the output
# format it needs — prose vs. a raw JSON object — so there is no per-phase
# system prompt. The Katana reference below is appended to it (_SYSTEM_FULL),
# putting the catalogs and ranges in context from the very first turn.
_SYSTEM = """\
You are a guitar tone expert who knows the Boss Katana Mk2 inside out, along with the amps
and pedals famous guitarists use. You work with the user in one ongoing conversation: they
either name an artist and song whose tone you recreate in three steps (analyze its character,
pick amp/effect types, dial in settings), or simply describe the tone they want. Either way,
you then refine the tone from their feedback until it sounds right to them.

Tone requests describe how the tone should SOUND, often vaguely ("brighter", "too
muddy", "more 80s"). Translate them into concrete parameter moves: brightness lives in
treble and presence; mud in bass and gain; aggression and sustain in preamp gain and OD
drive; space and depth in delay/reverb levels; character shifts may need a different preamp,
pedal, or effect type (pick ids from the catalogs in the reference below).
If a request is ambiguous, pick the most likely
interpretation and say briefly what you assumed.

Draw on the whole conversation so far, and follow the output format each message asks
for exactly: plain prose when asked to analyze, otherwise a single raw JSON object with no
markdown and no code fences.
"""

# Machine-readable Katana reference appended to the system prompt. Living in the
# system message (re-sent on every call anyway) means both flows — 3-phase
# generation and free chat — have the catalogs and field ranges available on
# every turn without any prompt restating them.
_SYSTEM_REFERENCE = """\
Boss Katana Mk2 reference — every *_type id must come from these catalogs (id=name):
Preamp: {preamp_options}
Booster/OD: {od_options}
FX slots (fx1, fx2): {fx_options}
Delay: {delay_options}
Reverb: {reverb_options}

Adjustable fields: preamp_type, od_type, fx1_type, fx2_type, delay_type, reverb_type
(catalog ids); preamp_gain, od_drive, delay_level 0-120; bass, mid, treble, presence,
od_level, reverb_level 0-100 (EQ at 50 = flat); od_on, fx1_on, fx2_on, delay_on,
reverb_on booleans. od_drive is the booster/overdrive pedal's gain and od_level its
output level; delay_level and reverb_level are effect mix levels.
"""

# Turn 1 — free-form analysis. Artist/song and the user's extra request live here
# and stay in context for every later turn, so they are never restated.
_PROMPT_CHARACTER = """\
Artist: {artist}
Song: {song}
{extra_block}
Analyze this guitar tone so it can be recreated on a Boss Katana Mk2. In a few sentences,
describe the overall character (gain level, EQ balance, dynamics) and which effect categories
are audibly active: booster/overdrive, modulation/FX, delay, and reverb. Mention the specific
amps and pedals this artist is known for using. Write plainly — no JSON, no headings.
"""

# Turn 2 — type selection. Your analysis is already in the conversation and the
# catalogs are in the system reference, so neither is pasted back in.
_PROMPT_TYPES = """\
Using your analysis above, choose the best Boss Katana Mk2 option for each slot from the
catalogs in the system reference. Even if a slot would be switched off in the final tone,
still pick the most plausible type.

Reply with ONLY a JSON object with these integer fields: preamp_type, od_type, fx1_type,
fx2_type, delay_type, reverb_type.
"""

# Turn 3 — dial settings. The chosen type ids are already in the conversation; we
# remind the model of their names only (ids are opaque) so the values stay coherent.
_PROMPT_VALUES = """\
You selected: preamp {preamp_name}; booster/OD {od_name}; FX1 {fx1_name}; FX2 {fx2_name};
delay {delay_name}; reverb {reverb_name}.

Now give the exact dial settings for this tone. Reply with ONLY a JSON object with exactly
these fields:
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
  "reverb_level": <int 0-100>
}}

od_drive is the booster/overdrive pedal's gain; od_level is its output level. delay_level and
reverb_level are the effect mix levels. EQ at 50 = flat.
"""

# Chat turns — refinements or free-mode tone requests — continue the same
# conversation. We pass the live settings because the user may have nudged dials
# by hand since the last message. The scope line differs on the very first turn
# of a session: there the current settings are leftovers with no meaning, so a
# requested tone must be defined completely rather than diffed against them.
_PROMPT_REFINE = """\
Current Katana settings:
{params_json}

User request: {message}

{scope}

Reply with ONLY a JSON object of this shape:
{{"message": "<briefly explain what you did and why>",
 "params": {{<only the fields you include>}}}}
Omit "params" (or leave it empty) if no settings should change.
"""

_REFINE_SCOPE_ONGOING = """\
Update the settings to satisfy the request, using the catalogs and field ranges from the
system reference. Change only the fields the request calls for: a small tweak should touch
few fields, while a bigger change of direction may set many."""

_REFINE_SCOPE_FIRST = """\
This is the first message of the conversation, so the current settings are just leftovers —
do not treat them as a starting tone. If the request describes a tone, build it from scratch
and include EVERY adjustable field from the system reference in params: all six *_type ids,
every dial, and every on/off switch. If the message is only a question, just answer it."""

# Appended to nudge a provider that replied with non-JSON on a structured turn.
_JSON_CORRECTION = (
    "That was not valid JSON. Reply with ONLY the JSON object — no prose, no code fences."
)

# Titles for the collapsed generation-prompt bubbles in the chat UI.
_LABEL_CHARACTER = "Phase 1/3 — analyze tone character"
_LABEL_TYPES = "Phase 2/3 — select amp & effect types"
_LABEL_VALUES = "Phase 3/3 — dial in settings"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _catalog_str(mapping: dict[int, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in mapping.items())


# The complete system message every session is seeded with. Catalogs are static,
# so this is built once at import time.
_SYSTEM_FULL = (
    _SYSTEM
    + "\n"
    + _SYSTEM_REFERENCE.format(
        preamp_options=_catalog_str(PREAMP_TYPES),
        od_options=_catalog_str(OD_TYPES),
        fx_options=_catalog_str(FX_TYPES),
        delay_options=_catalog_str(DELAY_TYPES),
        reverb_options=_catalog_str(REVERB_TYPES),
    )
)


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
    ),
    booleans=("od_on", "fx1_on", "fx2_on", "delay_on", "reverb_on"),
)


def _supports_schema(model: str) -> bool:
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:
        return False


def _json_kwargs(model: str, schema: dict, schema_name: str, strict: bool = True) -> dict:
    """litellm kwargs that force or coax a structured JSON reply for ``model``.

    Prefers the provider's native JSON-schema enforcement when supported; falls
    back to Ollama's native JSON mode, and finally to nothing (we then rely on
    the system prompt plus the parse-retry in ``_call_json``). ``strict=False``
    is needed for schemas with optional fields (the refine reply), which strict
    mode forbids.
    """
    if _supports_schema(model):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": strict},
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
    if isinstance(exc, Timeout):
        return (
            f"{provider} timed out — a local model may still be loading, or the "
            "provider is slow. Give it a moment and try again."
        )
    if isinstance(exc, ServiceUnavailableError):
        # 503 means the provider answered — overload/rate limiting, not a
        # connection problem.
        return f"{provider} is temporarily overloaded or rate limited. Wait a moment and try again."
    if isinstance(exc, APIConnectionError):
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


# Every user-adjustable patch field with its validation rule; drives the
# partial-params handling in the refine flow. Ranges match _phase_values.
_PARAM_SPECS: dict[str, tuple] = {
    "preamp_type": ("type", PREAMP_TYPES),
    "od_type": ("type", OD_TYPES),
    "fx1_type": ("type", FX_TYPES),
    "fx2_type": ("type", FX_TYPES),
    "delay_type": ("type", DELAY_TYPES),
    "reverb_type": ("type", REVERB_TYPES),
    "preamp_gain": ("int", 0, 120),
    "bass": ("int", 0, 100),
    "mid": ("int", 0, 100),
    "treble": ("int", 0, 100),
    "presence": ("int", 0, 100),
    "od_on": ("bool",),
    "od_drive": ("int", 0, 120),
    "od_level": ("int", 0, 100),
    "fx1_on": ("bool",),
    "fx2_on": ("bool",),
    "delay_on": ("bool",),
    "delay_level": ("int", 0, 120),
    "reverb_on": ("bool",),
    "reverb_level": ("int", 0, 100),
}

_REFINE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "params": {
            "type": "object",
            "properties": {
                key: {"type": "boolean" if spec[0] == "bool" else "integer"}
                for key, spec in _PARAM_SPECS.items()
            },
            "additionalProperties": False,
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}


def _validate_partial_params(data: dict) -> dict:
    """Snap/clamp a partial params dict from the refine reply.

    Unknown keys and unusable values are dropped with a warning rather than
    failing the whole reply — a mostly-good refinement should still apply.
    """
    out: dict = {}
    for key, val in data.items():
        spec = _PARAM_SPECS.get(key)
        if spec is None:
            log.warning("Refine reply contains unknown param %r — ignored", key)
            continue
        try:
            if spec[0] == "type":
                out[key] = _snap_type(val, spec[1], key)
            elif spec[0] == "bool":
                out[key] = bool(val)
            else:
                out[key] = _clamp(val, spec[1], spec[2], key)
        except ValueError:
            log.warning("Refine reply param %r has unusable value %r — ignored", key, val)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ChatEntry:
    """One turn of the LLM conversation, as sent to / received from the provider.

    ``content`` is the exact text on the wire. ``display`` overrides it in the UI:
    a refine user turn wraps the typed text in a prompt (only the typed text should
    show), and an assistant JSON reply shows its ``message`` field, not raw JSON.
    ``auto`` marks the machine-generated generation prompts the UI collapses;
    ``label`` titles those collapsed bubbles.
    """

    role: str  # "user" | "assistant"
    content: str
    label: str | None = None
    auto: bool = False
    display: str | None = None


class ToneSession:
    """One LLM conversation about a Katana tone, with two ways in.

    Either ``generate()`` runs the 3-phase creation flow and ``refine()`` continues
    it chat-style, or — free mode — ``refine()`` is called from turn one and the
    user simply describes the tone they want. Both work because the system message
    (``_SYSTEM_FULL``) carries the Katana reference: the option catalogs and field
    ranges are in context on every turn of every session.

    All turns extend a *single* growing conversation. The model's own analysis and
    choices are real ``assistant`` messages that stay in context, instead of being
    pasted back into later prompts as text. Providers are stateless, so the whole
    message list is re-sent on every call.

    Model and API key are set at construction and can be switched later with
    ``set_engine`` — the UI does so before every send, so the conversation always
    follows the provider/model dropdowns.

    ``on_request`` (if given) fires with the user ``ChatEntry`` the
    moment a turn is dispatched to the provider, and ``on_entry`` fires for every
    committed ``ChatEntry`` — the same user entry object again, then the assistant
    reply — so the UI can show a pending bubble immediately and settle it on
    success. Both run on whatever thread drives generate/refine. A turn is
    committed only after a successful reply, so a failed call leaves the
    conversation unchanged and is safe to retry.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        on_entry: Callable[[ChatEntry], None] | None = None,
        on_request: Callable[[ChatEntry], None] | None = None,
    ) -> None:
        self.model = (model or "").strip() or "openai/gpt-4o"
        self._api_key = api_key
        self._timeout = timeout
        self._on_entry = on_entry
        self._on_request = on_request
        self._messages: list[dict] = [{"role": "system", "content": _SYSTEM_FULL}]
        self.history: list[ChatEntry] = []

    def api_messages(self) -> list[dict]:
        """The provider-facing message list (system prompt included), as a copy."""
        return list(self._messages)

    def set_engine(self, api_key: str, model: str) -> None:
        """Point the conversation at a different provider/model.

        The history is plain role/content messages and providers are stateless,
        so a conversation can switch engines mid-flight — the next call simply
        re-sends the whole thread to the new one.
        """
        self.model = (model or "").strip() or self.model
        self._api_key = api_key

    # ── provider I/O ──────────────────────────────────────────────────────────

    def _complete(self, messages: list[dict], temperature: float, extra_kwargs: dict) -> str:
        tame_litellm_logging()
        response = litellm.completion(
            model=self.model,
            api_key=self._api_key or None,
            messages=messages,
            temperature=temperature,
            timeout=self._timeout,
            **extra_kwargs,
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _assistant_display(raw: str) -> str | None:
        """A JSON reply's ``message`` field for the UI; None for non-JSON/typeless replies."""
        try:
            parsed = json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
            return parsed["message"].strip() or None
        return None

    def _commit(self, user_entry: ChatEntry, assistant_raw: str) -> None:
        """Append a completed user+assistant exchange to the conversation and stream it."""
        assistant_entry = ChatEntry(
            "assistant", assistant_raw, display=self._assistant_display(assistant_raw)
        )
        for entry in (user_entry, assistant_entry):
            self.history.append(entry)
            self._messages.append({"role": entry.role, "content": entry.content})
            if self._on_entry:
                try:
                    self._on_entry(entry)
                except Exception:
                    log.exception("on_entry callback failed")

    def _notify_request(self, user_entry: ChatEntry) -> None:
        """Tell the UI a user turn is on the wire (before any reply exists)."""
        if self._on_request:
            try:
                self._on_request(user_entry)
            except Exception:
                log.exception("on_request callback failed")

    def _ask_text(self, user_content: str, *, label: str, temperature: float = 0.4) -> str:
        """Free-form turn; commit and return the assistant's prose."""
        user_entry = ChatEntry("user", user_content, label=label, auto=True)
        self._notify_request(user_entry)
        working = [*self._messages, {"role": "user", "content": user_content}]
        raw = self._complete(working, temperature, {})
        if not raw:
            raise ValueError("model returned an empty response")
        self._commit(user_entry, raw)
        return raw

    def _ask_json(
        self,
        user_content: str,
        schema: dict,
        schema_name: str,
        *,
        label: str | None = None,
        auto: bool = True,
        display: str | None = None,
        strict: bool = True,
        temperature: float = 0.3,
    ) -> dict:
        """Structured-JSON turn with a one-shot corrective retry.

        The correction exchange is worked on a local copy and discarded — only the
        original prompt and the final valid reply are committed, so the persistent
        conversation never carries the malformed attempt.
        """
        extra_kwargs = _json_kwargs(self.model, schema, schema_name, strict=strict)
        user_entry = ChatEntry("user", user_content, label=label, auto=auto, display=display)
        self._notify_request(user_entry)
        working = [*self._messages, {"role": "user", "content": user_content}]
        last_raw = ""
        for attempt in range(2):
            raw = self._complete(working, temperature, extra_kwargs)
            log.debug("LLM JSON response (%s): %s", schema_name, raw)
            try:
                data = json.loads(_strip_fences(raw))
            except json.JSONDecodeError:
                last_raw = raw
                if attempt == 0:
                    working = [
                        *working,
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": _JSON_CORRECTION},
                    ]
                continue
            self._commit(user_entry, raw)
            return data
        raise ValueError(f"model did not return valid JSON ({_short(last_raw, 80)})")

    # ── generation phases ─────────────────────────────────────────────────────

    def _phase_character(self, artist: str, song: str, extra: str) -> str:
        prompt = _PROMPT_CHARACTER.format(artist=artist, song=song, extra_block=_extra_block(extra))
        log.debug("LLM phase 1/3 (character) prompt:\n%s", prompt)
        text = self._ask_text(prompt, label=_LABEL_CHARACTER)
        log.debug("LLM phase 1/3 (character) analysis:\n%s", text)
        return text

    def _phase_types(self) -> dict:
        data = self._ask_json(_PROMPT_TYPES, _TYPES_SCHEMA, "katana_types", label=_LABEL_TYPES)
        log.debug("LLM phase 2/3 (types) parsed response: %s", data)
        for key in ("preamp_type", "od_type", "fx1_type", "fx2_type", "delay_type", "reverb_type"):
            if key not in data:
                raise ValueError(f"Types phase missing field '{key}'")
        result = {
            "preamp_type": _snap_type(data["preamp_type"], PREAMP_TYPES, "preamp_type"),
            "od_type": _snap_type(data["od_type"], OD_TYPES, "od_type"),
            "fx1_type": _snap_type(data["fx1_type"], FX_TYPES, "fx1_type"),
            "fx2_type": _snap_type(data["fx2_type"], FX_TYPES, "fx2_type"),
            "delay_type": _snap_type(data["delay_type"], DELAY_TYPES, "delay_type"),
            "reverb_type": _snap_type(data["reverb_type"], REVERB_TYPES, "reverb_type"),
        }
        log.debug(
            "LLM phase 2/3 (types) resolved: %s",
            {
                "preamp": f"{result['preamp_type']}={PREAMP_TYPES.get(result['preamp_type'])}",
                "od": f"{result['od_type']}={OD_TYPES.get(result['od_type'])}",
                "fx1": f"{result['fx1_type']}={FX_TYPES.get(result['fx1_type'])}",
                "fx2": f"{result['fx2_type']}={FX_TYPES.get(result['fx2_type'])}",
                "delay": f"{result['delay_type']}={DELAY_TYPES.get(result['delay_type'])}",
                "reverb": f"{result['reverb_type']}={REVERB_TYPES.get(result['reverb_type'])}",
            },
        )
        return result

    def _phase_values(self, types: dict) -> dict:
        prompt = _PROMPT_VALUES.format(
            preamp_name=PREAMP_TYPES.get(types["preamp_type"], str(types["preamp_type"])),
            od_name=OD_TYPES.get(types["od_type"], str(types["od_type"])),
            fx1_name=FX_TYPES.get(types["fx1_type"], str(types["fx1_type"])),
            fx2_name=FX_TYPES.get(types["fx2_type"], str(types["fx2_type"])),
            delay_name=DELAY_TYPES.get(types["delay_type"], str(types["delay_type"])),
            reverb_name=REVERB_TYPES.get(types["reverb_type"], str(types["reverb_type"])),
        )
        data = self._ask_json(prompt, _VALUES_SCHEMA, "katana_values", label=_LABEL_VALUES)
        log.debug("LLM phase 3/3 (values) parsed response: %s", data)
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
        )
        for key in required:
            if key not in data:
                raise ValueError(f"Values phase missing field '{key}'")
        result = {
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
        }
        log.debug("LLM phase 3/3 (values) resolved: %s", result)
        return result

    # ── public flow ───────────────────────────────────────────────────────────

    def generate(
        self,
        artist: str,
        song: str,
        extra: str = "",
        on_progress: Callable[[str], None] | None = None,
    ) -> dict:
        """Run the 3-phase generation as one conversation, recording every turn.

        Returns a dict with all patch fields. Always raises PatchGenerationError
        (with a clear, user-facing message) on any failure.
        """
        artist = artist.strip()
        song = song.strip()
        extra = (extra or "").strip()
        log.info("Generating patch for '%s' by '%s' via %s", song, artist, self.model)

        try:
            if on_progress:
                on_progress("Analyzing tone character…")
            self._phase_character(artist, song, extra)

            if on_progress:
                on_progress("Selecting effects and pedals…")
            types = self._phase_types()

            if on_progress:
                on_progress("Dialing in parameters…")
            values = self._phase_values(types)

            merged = {**types, **values}
            log.debug("LLM final merged patch params: %s", merged)
            return merged
        except ValueError as exc:
            # Our own parse/validation failures (bad/missing JSON fields).
            raise PatchGenerationError(
                f"The model's response could not be used: {_short(str(exc))}. "
                "Try again or pick a different model."
            ) from exc
        except Exception as exc:
            # litellm/provider failures — invalid model, auth, rate limit, network, etc.
            log.warning("LLM call failed: %s", exc)
            raise PatchGenerationError(_friendly_llm_error(exc, self.model)) from exc

    def refine(self, user_message: str, current_params: dict) -> tuple[str, dict | None]:
        """Continue the conversation with a chat message; return (reply, partial params).

        Also valid as the *first* turn of a fresh session (free mode) — the system
        reference supplies the catalogs and ranges, so no generation phases are
        required beforehand. On that first turn the prompt demands a complete param
        set for a requested tone (the current settings are meaningless leftovers);
        on later turns it asks for a minimal diff.

        ``current_params`` is a snapshot of the UI controls so manual tweaks made
        between messages are visible to the model. The whole conversation is already
        the message history, so only the new turn is added. The partial params dict
        (or None when nothing changed) contains only validated fields to merge over
        the current settings. Raises PatchGenerationError on any failure.
        """
        user_message = user_message.strip()
        first_turn = not self.history
        prompt = _PROMPT_REFINE.format(
            params_json=json.dumps(current_params, indent=2),
            message=user_message,
            scope=_REFINE_SCOPE_FIRST if first_turn else _REFINE_SCOPE_ONGOING,
        )
        log.debug("LLM refine prompt:\n%s", prompt)
        try:
            data = self._ask_json(
                prompt,
                _REFINE_SCHEMA,
                "katana_refine",
                label=None,
                auto=False,
                display=user_message,
                strict=False,
            )
            log.debug("LLM refine parsed response: %s", data)
            message = str(data.get("message") or "").strip()
            if not message:
                raise ValueError("Refine reply is missing 'message'")
            raw_params = data.get("params")
            partial = _validate_partial_params(raw_params) if isinstance(raw_params, dict) else {}
            if first_turn and partial and len(partial) < len(_PARAM_SPECS):
                missing = sorted(set(_PARAM_SPECS) - set(partial))
                log.warning("First-turn tone reply is missing fields: %s", ", ".join(missing))
            log.debug("LLM refine validated partial params: %s", partial)
            return message, (partial or None)
        except ValueError as exc:
            raise PatchGenerationError(
                f"The model's response could not be used: {_short(str(exc))}. "
                "Try rephrasing your request."
            ) from exc
        except Exception as exc:
            log.warning("LLM refine call failed: %s", exc)
            raise PatchGenerationError(_friendly_llm_error(exc, self.model)) from exc


def generate_patch(
    artist: str,
    song: str,
    api_key: str,
    model: str,
    on_progress: Callable[[str], None] | None = None,
    extra: str = "",
    timeout: float = 120.0,
) -> dict:
    """Generate Boss Katana Mk2 parameters for artist/song via a 3-phase LLM conversation.

    One-shot wrapper around a throwaway ToneSession — use ToneSession directly
    when the conversation should continue with ``refine``.

    ``extra`` is an optional free-text request from the user (e.g. "brighter, less gain")
    added to the opening analysis turn, where it stays in context for the whole run.

    ``timeout`` bounds each provider call (seconds) so a hung provider — e.g. a local
    Ollama model that never finishes loading — raises instead of blocking forever.

    Returns a dict with all patch fields. Always raises PatchGenerationError (with a
    clear, user-facing message) on any failure — provider errors, an invalid/retired
    model, or an unparseable response.
    """
    session = ToneSession(api_key=api_key, model=model, timeout=timeout)
    return session.generate(artist, song, extra=extra, on_progress=on_progress)
