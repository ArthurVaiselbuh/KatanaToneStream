"""MIDI SysEx engine for the Boss Katana Mk2.

Roland DT1 packet structure:
  F0 41 <devId> 00 00 00 33 12 <addr4> <data...> <checksum> F7

Checksum = (0x80 - (sum(addr + data) % 0x80)) & 0x7F

TONE area (live/current patch buffer) base address: 0x60 0x00 0x00 0x00
Section addresses are 4-byte Roland 7-bit (base-128 per byte).

The section base addresses below were decoded from a real Boss Tone Studio
capture (captures/tonestudio_send.midi2, see tools/decode_capture.py): Tone
Studio writes each UserPatch%* section to a fixed, 0x100-page-aligned base, NOT
to tightly-packed offsets accumulated from section sizes. The earlier computed
offsets were wrong and overlapped amp routing tables (which made the physical
volume knobs stop responding). These addresses are ground truth.
"""

import logging
import time
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .models import KatanaPatch

log = logging.getLogger(__name__)

KATANA_PORT_KEYWORD = "KATANA"
ROLAND_MANUFACTURER = 0x41
KATANA_MODEL_ID = [0x00, 0x00, 0x00, 0x33]
DEVICE_ID = 0x00  # what Boss Tone Studio actually sends (confirmed by capture)
SEND_DELAY_S = 0.025  # 25 ms between SysEx packets (Roland spec)
MAX_CHUNK = 128  # Tone Studio caps DT1 payloads at 128 bytes, splitting larger
# sections at the 0x100 page boundary (e.g. Fx = 128 + 97)

try:
    import rtmidi

    _RTMIDI_OK = True
except ImportError:
    _RTMIDI_OK = False
    log.warning("python-rtmidi not available — MIDI output disabled")


# ---------------------------------------------------------------------------
# Roland address helpers
# ---------------------------------------------------------------------------


def roland_checksum(data: list[int]) -> int:
    """Roland checksum over address + data bytes."""
    return (0x80 - sum(data) % 0x80) & 0x7F


def _roland_addr_add(base: list[int], offset: int) -> list[int]:
    """Add a linear byte offset to a Roland 4-byte 7-bit address (base-128 carry)."""
    result = list(base)
    carry = offset
    for i in (3, 2, 1, 0):
        val = result[i] + carry
        result[i] = val & 0x7F
        carry = val >> 7
        if carry == 0:
            break
    return result


def _build_dt1(address: list[int], data: list[int]) -> list[int]:
    """Build a complete Roland DT1 SysEx message."""
    payload = address + data
    chk = roland_checksum(payload)
    return [0xF0, ROLAND_MANUFACTURER, DEVICE_ID] + KATANA_MODEL_ID + [0x12] + payload + [chk, 0xF7]


# ---------------------------------------------------------------------------
# TONE area section map — decoded from captures/tonestudio_send.midi2.
#
# Each entry is a UserPatch%* section and the fixed 0x100-page-aligned base
# address Tone Studio writes it to (TONE = live/current patch buffer at base
# 0x60000000). Order is irrelevant for DT1 writes. Sections larger than MAX_CHUNK (only
# Fx(1)/Fx(2), 225 B) are split at the 128-byte page boundary by send_patch;
# _roland_addr_add(base, 128) yields the next page base the device expects.
#
# These replace the earlier accumulated offsets, which were wrong and overlapped
# amp routing tables. Every section here is confirmed against a live capture, so
# all of them are sent — there is no longer an "unverified, skip" set.
# ---------------------------------------------------------------------------


class Section(NamedTuple):
    """A named TONE-area region and its Roland 4-byte base address."""

    key: str
    addr: list[int]


_TONE_SECTIONS: list[Section] = [
    Section("UserPatch%PatchName", [0x60, 0x00, 0x00, 0x00]),
    Section("UserPatch%Patch_0", [0x60, 0x00, 0x00, 0x10]),
    Section("UserPatch%Fx(1)", [0x60, 0x00, 0x01, 0x00]),
    Section("UserPatch%Fx(2)", [0x60, 0x00, 0x03, 0x00]),
    Section("UserPatch%Delay(1)", [0x60, 0x00, 0x05, 0x00]),
    Section("UserPatch%Delay(2)", [0x60, 0x00, 0x05, 0x20]),
    Section("UserPatch%Patch_1", [0x60, 0x00, 0x05, 0x40]),
    Section("UserPatch%Patch_2", [0x60, 0x00, 0x06, 0x20]),
    Section("UserPatch%Status", [0x60, 0x00, 0x06, 0x50]),
    Section("UserPatch%KnobAsgn", [0x60, 0x00, 0x07, 0x00]),
    Section("UserPatch%ExpPedalAsgn", [0x60, 0x00, 0x08, 0x00]),
    Section("UserPatch%ExpPedalAsgnMinMax", [0x60, 0x00, 0x08, 0x30]),
    Section("UserPatch%GafcExp1Asgn", [0x60, 0x00, 0x09, 0x00]),
    Section("UserPatch%GafcExp1AsgnMinMax", [0x60, 0x00, 0x09, 0x30]),
    Section("UserPatch%GafcExp2Asgn", [0x60, 0x00, 0x0A, 0x00]),
    Section("UserPatch%GafcExp2AsgnMinMax", [0x60, 0x00, 0x0A, 0x30]),
    Section("UserPatch%FsAsgn", [0x60, 0x00, 0x0F, 0x08]),
    Section("UserPatch%Patch_Mk2V2", [0x60, 0x00, 0x0F, 0x10]),
    Section("UserPatch%Contour(1)", [0x60, 0x00, 0x0F, 0x30]),
    Section("UserPatch%Contour(2)", [0x60, 0x00, 0x0F, 0x38]),
    Section("UserPatch%Contour(3)", [0x60, 0x00, 0x0F, 0x40]),
    Section("UserPatch%GafcExp3Asgn", [0x60, 0x00, 0x0B, 0x00]),
    Section("UserPatch%GafcExp3AsgnMinMax", [0x60, 0x00, 0x0B, 0x30]),
    Section("UserPatch%GafcExExp1Asgn", [0x60, 0x00, 0x0C, 0x00]),
    Section("UserPatch%GafcExExp1AsgnMinMax", [0x60, 0x00, 0x0C, 0x30]),
    Section("UserPatch%GafcExExp2Asgn", [0x60, 0x00, 0x0D, 0x00]),
    Section("UserPatch%GafcExExp2AsgnMinMax", [0x60, 0x00, 0x0D, 0x30]),
    Section("UserPatch%GafcExExp3Asgn", [0x60, 0x00, 0x0E, 0x00]),
    Section("UserPatch%GafcExExp3AsgnMinMax", [0x60, 0x00, 0x0E, 0x30]),
    Section("UserPatch%CtrlAsgn", [0x60, 0x00, 0x0F, 0x00]),
    Section("UserPatch%Eq(2)", [0x60, 0x00, 0x00, 0x60]),
]


# ---------------------------------------------------------------------------
# KatanaMidi
# ---------------------------------------------------------------------------


class KatanaMidi:
    def __init__(self) -> None:
        self._out = None
        self._probe = None  # reused MidiOut for port enumeration (polled every ~5s)
        self.port_name: str | None = None

    def scan_ports(self) -> list[str]:
        if not _RTMIDI_OK:
            return []
        if self._probe is None:
            self._probe = rtmidi.MidiOut()
        return self._probe.get_ports()

    def connect(self) -> bool:
        if not _RTMIDI_OK:
            return False
        ports = self.scan_ports()
        for i, name in enumerate(ports):
            if KATANA_PORT_KEYWORD.lower() in name.lower():
                self._out = rtmidi.MidiOut()
                self._out.open_port(i)
                self.port_name = name
                log.info("Connected to MIDI port: %s", name)
                return True
        log.debug("No Katana MIDI port found. Available: %s", ports)
        return False

    def is_connected(self) -> bool:
        if not _RTMIDI_OK or self._out is None:
            return False
        if not self._out.is_port_open():
            self._out = None
            self.port_name = None
            return False
        # Verify the physical port still exists (detects USB unplug)
        if not any(KATANA_PORT_KEYWORD.lower() in p.lower() for p in self.scan_ports()):
            self._out.close_port()
            self._out = None
            self.port_name = None
            return False
        return True

    def disconnect(self) -> None:
        if self._out:
            self._out.close_port()
            self._out = None
            self.port_name = None

    def _send_sysex(self, message: list[int]) -> None:
        if self._out:
            self._out.send_message(message)
            time.sleep(SEND_DELAY_S)

    def send_program_change(self, pc: int, midi_channel: int = 1) -> None:
        """Send a MIDI Program Change to recall a stored tone-setting channel.

        The Katana Mk2 recalls channels via PC per its RX PC map (defaults from
        address_map.js: bank A CH1-4 = PC 0-3, PANEL = PC 4, bank B CH1-4 =
        PC 5-8 on the 100 W; the 50 W has CH1-2 per bank = PC 0-1 / 5-6). See
        katana_channels for names and the map.
        midi_channel is 1-indexed (default 1).
        """
        if not self.is_connected():
            return
        ch_byte = 0xC0 | ((midi_channel - 1) & 0x0F)
        self._out.send_message([ch_byte, pc & 0x7F])
        log.info("Program Change → PC %d (ch %d)", pc, midi_channel)
        time.sleep(0.5)  # give amp time to finish loading patch before SysEx

    def send_patch(self, patch: "KatanaPatch", target_patch: int | None = None) -> None:
        """Send a KatanaPatch to the amp's live TONE buffer via Roland DT1 SysEx.

        If target_patch is given (a MIDI PC number; see katana_channels), a
        Program Change is sent first to recall that channel before writing the
        SysEx data into the live TONE buffer.
        """
        if not self.is_connected():
            log.warning("send_patch called but Katana not connected")
            return

        raw_bytes: dict | None = patch.raw_bytes
        if not raw_bytes:
            log.warning(
                "Patch '%s' has no raw_bytes (TSL param-dict format). "
                "Cannot build SysEx — download the patch first to get ALB-format data.",
                patch.display_name,
            )
            return

        if target_patch is not None:
            self.send_program_change(target_patch)

        log.info("Sending patch '%s' to Katana TONE buffer…", patch.display_name)
        sections_sent = 0
        packets_sent = 0

        for section in _TONE_SECTIONS:
            hex_list = raw_bytes.get(section.key)
            if not hex_list:
                log.debug("  %s: not in patch, skipping", section.key)
                continue

            data = [int(h, 16) for h in hex_list]
            log.debug(
                "  %s: %d bytes @ %s",
                section.key,
                len(data),
                " ".join(f"{b:02X}" for b in section.addr),
            )

            # Send in MAX_CHUNK-byte chunks; each chunk gets its own offset address
            for chunk_start in range(0, len(data), MAX_CHUNK):
                chunk = data[chunk_start : chunk_start + MAX_CHUNK]
                addr = _roland_addr_add(section.addr, chunk_start)
                msg = _build_dt1(addr, chunk)
                if packets_sent == 0:
                    log.info("First SysEx packet: %s", " ".join(f"{b:02X}" for b in msg))
                self._send_sysex(msg)
                packets_sent += 1

            sections_sent += 1

        log.info(
            "Patch '%s' sent: %d section(s), %d SysEx packet(s)",
            patch.display_name,
            sections_sent,
            packets_sent,
        )
