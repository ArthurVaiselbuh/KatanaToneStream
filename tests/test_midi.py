"""Tests for the Roland DT1 SysEx helpers."""

from katana_tonestream.katana_midi import (
    DEVICE_ID,
    KATANA_MODEL_ID,
    ROLAND_MANUFACTURER,
    _build_dt1,
    _roland_addr_add,
    roland_checksum,
)


class TestRolandChecksum:
    def test_simple(self):
        # sum = 0x60 = 96; (0x80 - 96) & 0x7F = 32
        assert roland_checksum([0x60, 0x00, 0x00, 0x00, 0x00]) == 0x20

    def test_wraps_modulo_128(self):
        # sum = 254; 254 % 128 = 126; 0x80 - 126 = 2
        assert roland_checksum([0x7F, 0x7F]) == 0x02

    def test_exact_multiple_of_128_is_zero(self):
        # sum = 128; 128 % 128 = 0; (0x80 - 0) & 0x7F = 0
        assert roland_checksum([0x40, 0x40]) == 0x00

    def test_result_always_7bit(self):
        for n in range(0, 1000, 7):
            assert 0 <= roland_checksum([n]) <= 0x7F


class TestRolandAddrAdd:
    def test_no_carry(self):
        assert _roland_addr_add([0x60, 0x00, 0x00, 0x00], 0x10) == [0x60, 0x00, 0x00, 0x10]

    def test_carry_at_128_boundary(self):
        assert _roland_addr_add([0x60, 0x00, 0x00, 0x7F], 1) == [0x60, 0x00, 0x01, 0x00]

    def test_carry_across_two_bytes(self):
        assert _roland_addr_add([0x60, 0x00, 0x7F, 0x7F], 1) == [0x60, 0x01, 0x00, 0x00]

    def test_does_not_mutate_input(self):
        base = [0x60, 0x00, 0x00, 0x7F]
        _roland_addr_add(base, 5)
        assert base == [0x60, 0x00, 0x00, 0x7F]


class TestBuildDt1:
    def test_framing(self):
        addr = [0x60, 0x00, 0x00, 0x10]
        data = [0x01, 0x02, 0x03]
        msg = _build_dt1(addr, data)

        assert msg[0] == 0xF0
        assert msg[1] == ROLAND_MANUFACTURER
        assert msg[2] == DEVICE_ID
        assert msg[3:7] == KATANA_MODEL_ID
        assert msg[7] == 0x12  # DT1 command
        assert msg[8:12] == addr
        assert msg[12:15] == data
        assert msg[-1] == 0xF7

    def test_checksum_position_and_value(self):
        addr = [0x60, 0x00, 0x00, 0x10]
        data = [0x01, 0x02, 0x03]
        msg = _build_dt1(addr, data)
        assert msg[-2] == roland_checksum(addr + data)
