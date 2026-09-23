"""Control an ATORCH DL24 / DL24P electronic load over USB-HID.

The protocol was reverse-engineered from the official ATORCH "BenFang System APP"
(BW150_Application.exe) by sniffing its USB traffic. See PROTOCOL.md for details.

    import dl24p

    with dl24p.DL24P() as load:
        load.set_current(0.5)        # constant current, 0.5 A
        load.on()
        print(load.read())
        load.off()
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, asdict
from typing import Iterator

import hid

__version__ = "0.1.0"
__all__ = ["DL24P", "Measurement", "Settings", "DL24PError", "MODES", "MODE_UNITS", "LANGUAGES", "find"]

VID = 0x0483
PID = 0x5750
REPORT_LEN = 64

# Command byte (byte 3 of a frame)
CMD_READ_SETTINGS = 0x03
CMD_SCAN = 0x04
CMD_READ_MEASUREMENT = 0x05
CMD_LANGUAGE = 0x20
CMD_SET_VALUE = 0x21
CMD_BRIGHTNESS = 0x22
CMD_STANDBY_BRIGHTNESS = 0x23
CMD_STANDBY_TIME = 0x24
CMD_OUTPUT = 0x25
CMD_CUTOFF_VOLTAGE = 0x29
CMD_FULL_VOLTAGE = 0x2A
CMD_FULL_CURRENT = 0x2B
CMD_OVER_CURRENT = 0x2C
CMD_OVER_POWER = 0x2D
CMD_OVER_TEMP_EXT = 0x2E
CMD_OVER_TEMP_MOS = 0x2F
CMD_TIME_LIMIT = 0x31
CMD_HOME = 0x45
CMD_SETTINGS_PAGE = 0x46

# Mode name -> (command, mode index reported by the device)
MODES = {
    "CC": (0x47, 0),  # constant current, value in A
    "CV": (0x48, 1),  # constant voltage, value in V
    "CR": (0x49, 2),  # constant resistance, value in ohm
    "CP": (0x4A, 3),  # constant power, value in W
    # Present in the PC app, but DL24 firmware V1.1.0 ignores them (mode byte does not change):
    "BRT": (0x4B, None),
    "PT": (0x4C, None),
    "CT": (0x4D, None),
    "CDC": (0x4E, None),
    "CDCDC": (0x4F, None),
    "CDXN": (0x50, None),
}
_MODE_BY_INDEX = {idx: name for name, (_, idx) in MODES.items() if idx is not None}
MODE_UNITS = {"CC": "A", "CV": "V", "CR": "ohm", "CP": "W"}

# Language index as reported in the settings (EN-A = 3 is observed; the others are inferred)
LANGUAGES = {"CN-A": 1, "CN-B": 2, "EN-A": 3, "EN-B": 4}


class DL24PError(Exception):
    pass


@dataclass
class Measurement:
    voltage: float          # V
    current: float          # A
    power: float            # W
    resistance: float       # ohm (9999.99 = no current)
    energy_wh: float        # Wh (accumulated)
    capacity_mah: float     # mAh (accumulated)
    temp_cpu: float         # °C
    temp_mos: float         # °C
    fan_rpm: float          # fan speed (scaling not fully confirmed)
    output_on: bool         # load is switched on
    raw: tuple              # all 14 raw uint32 fields + status word, for debugging

    def __str__(self) -> str:
        state = "ON " if self.output_on else "OFF"
        return (f"[{state}] {self.voltage:7.3f} V  {self.current:7.3f} A  {self.power:8.3f} W  "
                f"{self.energy_wh:8.3f} Wh  {self.capacity_mah:9.1f} mAh  "
                f"CPU {self.temp_cpu:4.1f}°C  MOS {self.temp_mos:4.1f}°C")


@dataclass
class Settings:
    mode: str                     # "CC", "CV", "CR", "CP"
    value: float                  # set point of the current mode (A / V / ohm / W)
    cutoff_voltage: float         # D_Cutoff Volt: stop when the voltage drops below this (V)
    full_voltage: float           # C_Cutoff Volt / Full.U (V)
    full_current: float           # C_Cutoff Amp / Full.I (A)
    over_current: float           # FULL.I: over-current protection (A)
    over_power: float             # Over Power (W)
    over_temp_ext: float          # Over Ext.Temp (°C)
    over_temp_mos: float          # Over_Temp MOS (°C)
    time_limit_h: int             # Limit Time Discharge, hours
    time_limit_m: int             # Limit Time Discharge, minutes
    brightness: int               # 02 Display Brightness
    standby_brightness: int       # 03 Standby Brightness
    standby_time: int             # 04 Enter Standby Time
    language: int                 # see LANGUAGES
    cal_temp: float               # 08 Ext_temp calibration factor
    cal_voltage: float            # 06 Voltage calibration factor
    cal_current: float            # 07 Current calibration factor

    def as_dict(self) -> dict:
        return asdict(self)


def find() -> list[dict]:
    """Return hidapi info dicts for all connected DL24 loads.

    0483:5750 is ST's generic custom-HID ID and is shared by other STM32 gadgets,
    so devices whose product string names something else are skipped.
    """
    devices = []
    for d in hid.enumerate(VID, PID):
        name = (d.get("product_string") or "").upper()
        if not name or "DL24" in name or "ATORCH" in name:
            devices.append(d)
    return devices


class DL24P:
    """Connection to one DL24/DL24P.

    Close the ATORCH PC app first, otherwise its polling mixes with yours.
    """

    def __init__(self, path: bytes | None = None, address: int = 1, timeout: float = 1.0):
        self.address = address
        self.timeout = timeout
        if path is None:
            devices = find()
            if not devices:
                raise DL24PError("No ATORCH DL24 found (USB 0483:5750). Is it plugged in?")
            path = devices[0]["path"]
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except OSError as e:
            raise DL24PError(f"Could not open the DL24 ({e}). Is another program using it?") from e

    # ---------- connection ----------
    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "DL24P":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def product(self) -> str:
        return self._dev.get_product_string()

    # ---------- low level ----------
    def _frame(self, cmd: int, data: bytes = b"\0\0\0\0") -> bytes:
        body = bytes([0x55, 0x05, self.address, cmd]) + data + b"\xee\xff"
        # The device uses no HID report IDs, so hidapi needs report ID 0 in front of the 64 bytes
        return b"\x00" + body + bytes(REPORT_LEN - len(body))

    def _flush(self) -> None:
        # Note: in hidapi read(..., timeout_ms=0) means "block forever", so switch to non-blocking
        self._dev.set_nonblocking(True)
        try:
            while self._dev.read(REPORT_LEN):
                pass
        finally:
            self._dev.set_nonblocking(False)

    def send(self, cmd: int, data: bytes = b"\0\0\0\0") -> None:
        """Send a raw command with 4 data bytes."""
        if len(data) != 4:
            raise ValueError("data must be exactly 4 bytes")
        self._dev.write(self._frame(cmd, data))

    def query(self, cmd: int) -> bytes:
        """Send a read command and return the matching 64-byte reply."""
        self._flush()
        self.send(cmd)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = self._dev.read(REPORT_LEN, 100)
            if r and r[0] == 0xAA and r[2] == self.address and r[3] == cmd:
                return bytes(r)
        raise DL24PError(f"No reply to command 0x{cmd:02x}")

    def _set_float(self, cmd: int, value: float) -> None:
        self.send(cmd, struct.pack(">f", float(value)))

    def _set_byte_last(self, cmd: int, value: int) -> None:
        self.send(cmd, bytes([0, 0, 0, int(value) & 0xFF]))

    def _verify(self, getter, expected, what: str, tol: float = 1e-3) -> None:
        deadline = time.monotonic() + 2.0
        got = None
        while time.monotonic() < deadline:
            got = getter(self.settings())
            if (abs(got - expected) <= tol) if isinstance(expected, float) else got == expected:
                return
            time.sleep(0.1)
        raise DL24PError(f"The device did not accept {what}={expected!r} (still {got!r})")

    # ---------- reading ----------
    def read(self) -> Measurement:
        """Read the live measurements."""
        r = self.query(CMD_READ_MEASUREMENT)
        w = struct.unpack_from("<14I", r, 4)
        status = struct.unpack_from("<H", r, 60)[0]
        return Measurement(
            voltage=w[1] / 1000,
            current=w[2] / 1000,
            power=w[3] / 1000,
            resistance=w[4] / 1000,
            energy_wh=w[5] / 1000,
            capacity_mah=w[6] / 1000,
            temp_cpu=w[7] / 1000,
            temp_mos=w[9] / 1000,
            fan_rpm=w[10] / 1000,
            output_on=bool(w[12]),
            raw=w + (status,),
        )

    def settings(self) -> Settings:
        """Read all settings."""
        r = self.query(CMD_READ_SETTINGS)
        f = struct.unpack_from(">11f", r, 4)
        b = r[48:55]
        return Settings(
            mode=_MODE_BY_INDEX.get(b[0], f"?{b[0]}"),
            value=round(f[0], 4),
            cal_temp=round(f[1], 4),
            cal_voltage=round(f[2], 4),
            cal_current=round(f[3], 4),
            cutoff_voltage=round(f[4], 4),
            full_voltage=round(f[5], 4),
            full_current=round(f[6], 4),
            over_current=round(f[7], 4),
            over_power=round(f[8], 4),
            over_temp_ext=round(f[9], 4),
            over_temp_mos=round(f[10], 4),
            language=b[1],
            brightness=b[2],
            standby_brightness=b[3],
            standby_time=b[4],
            time_limit_h=b[5],
            time_limit_m=b[6],
        )

    def stream(self, interval: float = 1.0) -> Iterator[Measurement]:
        """Endless generator yielding one measurement every `interval` seconds."""
        nxt = time.monotonic()
        while True:
            yield self.read()
            nxt += interval
            time.sleep(max(0.0, nxt - time.monotonic()))

    # ---------- control ----------
    def _set_output(self, state: bool) -> None:
        # A recent mode change can switch the load off again shortly after, so require the state to hold
        for _ in range(5):
            self.send(CMD_OUTPUT, bytes([int(state), 0, 0, 0]))
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if self.read().output_on == state:
                    time.sleep(0.4)
                    if self.read().output_on == state:
                        return
                    break
                time.sleep(0.1)
        if state:
            raise DL24PError("The load did not stay on. Check the cut-off voltage, time limit and protections.")
        raise DL24PError("The load did not switch off")

    def on(self) -> None:
        """Switch the load on."""
        self._set_output(True)

    def off(self) -> None:
        """Switch the load off."""
        self._set_output(False)

    @property
    def is_on(self) -> bool:
        return self.read().output_on

    def set_mode(self, mode: str) -> None:
        """Change mode: "CC", "CV", "CR" or "CP". Changing mode switches the load off."""
        key = mode.upper()
        if key not in MODES:
            raise ValueError(f"Unknown mode {mode!r}. Valid: {', '.join(MODES)}")
        cmd, idx = MODES[key]
        if idx is None:
            raise DL24PError(f"Mode {key} is not supported by the DL24 firmware")
        if self.settings().mode == key:
            return
        # A mode change switches the load off, and the device applies it with a small delay
        self.send(cmd)
        self._verify(lambda s: s.mode, key, "mode")
        time.sleep(0.5)

    def set_value(self, value: float) -> None:
        """Set the set point of the current mode (A in CC, V in CV, ohm in CR, W in CP)."""
        if value < 0:
            raise ValueError("value must be >= 0")
        self._set_float(CMD_SET_VALUE, value)
        self._verify(lambda s: s.value, round(float(value), 4), "set point")

    def set_current(self, amps: float) -> None:
        """Switch to CC and set the current."""
        self.set_mode("CC")
        self.set_value(amps)

    def set_voltage(self, volts: float) -> None:
        """Switch to CV and set the voltage."""
        self.set_mode("CV")
        self.set_value(volts)

    def set_resistance(self, ohms: float) -> None:
        """Switch to CR and set the resistance."""
        self.set_mode("CR")
        self.set_value(ohms)

    def set_power(self, watts: float) -> None:
        """Switch to CP and set the power."""
        self.set_mode("CP")
        self.set_value(watts)

    def set_cutoff_voltage(self, volts: float) -> None:
        """D_Cutoff Volt (0 = off).

        The device stores and displays this value, but firmware V1.1.0 did not switch the
        load off when the voltage dropped below it during testing. Check the voltage in your
        own script (see examples/battery_discharge.py).
        """
        self._set_float(CMD_CUTOFF_VOLTAGE, volts)
        self._verify(lambda s: s.cutoff_voltage, round(float(volts), 4), "cutoff_voltage")

    def set_full_voltage(self, volts: float) -> None:
        """C_Cutoff Volt / Full.U. Note: ignored by the device in CC mode during testing."""
        self._set_float(CMD_FULL_VOLTAGE, volts)
        self._verify(lambda s: s.full_voltage, round(float(volts), 4), "full_voltage")

    def set_full_current(self, amps: float) -> None:
        """C_Cutoff Amp / Full.I. Note: ignored by the device in CC mode during testing."""
        self._set_float(CMD_FULL_CURRENT, amps)
        self._verify(lambda s: s.full_current, round(float(amps), 4), "full_current")

    def set_over_current(self, amps: float) -> None:
        """FULL.I: over-current protection (A)."""
        self._set_float(CMD_OVER_CURRENT, amps)
        self._verify(lambda s: s.over_current, round(float(amps), 4), "over_current")

    def set_over_power(self, watts: float) -> None:
        """Over-power protection (W)."""
        self._set_float(CMD_OVER_POWER, watts)
        self._verify(lambda s: s.over_power, round(float(watts), 4), "over_power")

    def set_over_temp_ext(self, celsius: float) -> None:
        """Protection for the external NTC temperature (°C)."""
        self._set_float(CMD_OVER_TEMP_EXT, celsius)
        self._verify(lambda s: s.over_temp_ext, round(float(celsius), 4), "over_temp_ext")

    def set_over_temp_mos(self, celsius: float) -> None:
        """Protection for the MOSFET temperature (°C)."""
        self._set_float(CMD_OVER_TEMP_MOS, celsius)
        self._verify(lambda s: s.over_temp_mos, round(float(celsius), 4), "over_temp_mos")

    def set_time_limit(self, hours: int, minutes: int) -> None:
        """Maximum discharge time. 0:00 = no limit."""
        if not (0 <= hours <= 99 and 0 <= minutes <= 59):
            raise ValueError("hours must be 0-99 and minutes 0-59")
        self.send(CMD_TIME_LIMIT, bytes([hours, 0, 0, 1]))
        self.send(CMD_TIME_LIMIT, bytes([minutes, 0, 0, 2]))
        self._verify(lambda s: (s.time_limit_h, s.time_limit_m), (hours, minutes), "time_limit")

    def set_brightness(self, level: int) -> None:
        """Display brightness while running (1-9)."""
        if not 1 <= level <= 9:
            raise ValueError("level must be 1-9")
        self._set_byte_last(CMD_BRIGHTNESS, level)
        self._verify(lambda s: s.brightness, level, "brightness")

    def set_standby_brightness(self, level: int) -> None:
        """Display brightness in standby (1-9)."""
        if not 1 <= level <= 9:
            raise ValueError("level must be 1-9")
        self._set_byte_last(CMD_STANDBY_BRIGHTNESS, level)
        self._verify(lambda s: s.standby_brightness, level, "standby_brightness")

    def set_standby_time(self, value: int) -> None:
        """Time before standby (the device shows 1-60)."""
        if not 1 <= value <= 60:
            raise ValueError("value must be 1-60")
        self._set_byte_last(CMD_STANDBY_TIME, value)
        self._verify(lambda s: s.standby_time, value, "standby_time")

    def set_language(self, language: str | int) -> None:
        """Display language: "CN-A", "CN-B", "EN-A" (EN-B was rejected by the device during testing)."""
        if isinstance(language, str):
            if language.upper() not in LANGUAGES:
                raise ValueError(f"Unknown language {language!r}. Valid: {', '.join(LANGUAGES)}")
            idx = LANGUAGES[language.upper()]
        else:
            idx = int(language)
        self.send(CMD_LANGUAGE, bytes([idx, 0, 0, 0]))
        self._verify(lambda s: s.language, idx, "language")
