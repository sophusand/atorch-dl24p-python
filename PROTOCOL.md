# ATORCH DL24 USB-HID protocol

Reverse-engineered in September 2026 by capturing the USB traffic of the official ATORCH PC app
(`BW150_Application.exe` V1.0.5, "ATORCH BenFang System APP") with USBPcap, against a DL24
running firmware V1.1.0. Each button in the app was clicked with a timestamped log, so every
command below was matched to exactly one action. Nothing here is guessed unless marked.

## USB

- VID `0x0483`, PID `0x5750`, product string `ATORCH DL24 V1.1.0` (`... APP` in the enumeration)
- HID, vendor usage page `0xFF02`, **no report IDs**, 64-byte input/output reports on interrupt EP1
- There is no serial/COM port. The load only sends data in reply to a request.
- With hidapi, write `00` followed by the 64 bytes (report ID 0 first). Reads return the 64 bytes
  without a report ID.
- Commands must go out as interrupt OUT on EP `0x01`. A `SET_REPORT(Output)` on EP0 is stalled.
- After power-up the load only replies once it has received the HID class request
  `SET_IDLE(0)` (`21 0a 00 00 00 00 00 00`). Before that it still executes commands, but it sends
  nothing back. Windows sends `SET_IDLE(0)` right after enumeration. macOS does not, so the
  library sends it itself (see `dl24p/macfix.py`). The load remembers it until it loses power.

## Frame format

```
PC   -> load: 55 05 <addr> <cmd> d0 d1 d2 d3 ee ff   (padded with 00 to 64 bytes)
load -> PC  : aa 05 <addr> <cmd> ...payload... ee ff
```

`addr` is 1. The PC app scans addresses 1-10 with command `04` at startup, but this is not
required, and no reply to it was seen. There is no checksum. Set commands get no reply, so
read the settings (`03`) to confirm them.

## Commands

| cmd | data | meaning |
|---|---|---|
| `03` | – | read settings (reply below) |
| `04` | – | address scan (no reply observed) |
| `05` | – | read measurements (reply below) |
| `20` | `[n,0,0,0]` | display language (3 = EN-A) |
| `21` | float BE | set point of the current mode |
| `22` | `[0,0,0,n]` | display brightness |
| `23` | `[0,0,0,n]` | standby brightness |
| `24` | `[0,0,0,n]` | standby time |
| `25` | `[1,0,0,0]` / `[0,0,0,0]` | load ON / OFF |
| `29` | float BE | D_Cutoff Volt (stored, but not enforced by FW V1.1.0) |
| `2a` | float BE | C_Cutoff Volt / Full.U (ignored in testing) |
| `2b` | float BE | C_Cutoff Amp / Full.I (ignored in testing) |
| `2c` | float BE | FULL.I over-current protection (A) |
| `2d` | float BE | over-power protection (W) |
| `2e` | float BE | external temperature protection (°C) |
| `2f` | float BE | MOSFET temperature protection (°C) |
| `31` | `[h,0,0,1]` / `[m,0,0,2]` | time limit, hours / minutes |
| `45` | – | PC app "Return" to main menu |
| `46` | – | PC app opens its settings page |
| `47`–`4a` | – | mode CC / CV / CR / CP (switches the load off) |
| `4b`–`50` | – | BRT / PT / CT / CDC / CDCDC / CDxn (not accepted by FW V1.1.0) |
| `51` | – | seen once, meaning unknown |

"float BE" = IEEE-754 32-bit float, big-endian, e.g. 3.0 = `40 40 00 00`.

## Reply to `03`: settings

Offsets from the start of the frame (`aa` = 0):

| offset | type | field |
|---|---|---|
| 4 | float BE | set point (current mode) |
| 8 | float BE | external temperature calibration factor |
| 12 | float BE | voltage calibration factor |
| 16 | float BE | current calibration factor |
| 20 | float BE | D_Cutoff Volt |
| 24 | float BE | C_Cutoff Volt (Full.U) |
| 28 | float BE | C_Cutoff Amp (Full.I) |
| 32 | float BE | FULL.I over-current |
| 36 | float BE | over-power |
| 40 | float BE | external over-temperature |
| 44 | float BE | MOSFET over-temperature |
| 48 | u8 | mode (0 CC, 1 CV, 2 CR, 3 CP) |
| 49 | u8 | language |
| 50 | u8 | display brightness |
| 51 | u8 | standby brightness |
| 52 | u8 | standby time |
| 53 | u8 | time limit, hours |
| 54 | u8 | time limit, minutes |

The load keeps a separate set point for each mode. The value at offset 4 belongs to the active mode.

## Reply to `05`: measurements

14 × uint32 little-endian from offset 4, plus a uint16 at offset 60:

| field | offset | meaning | scale |
|---|---|---|---|
| w0 | 4 | unknown (always 0) | |
| w1 | 8 | voltage | /1000 → V |
| w2 | 12 | current | /1000 → A |
| w3 | 16 | power | /1000 → W |
| w4 | 20 | resistance | /1000 → Ω (9999991 = no current) |
| w5 | 24 | energy | /1000 → Wh |
| w6 | 28 | capacity | /1000 → mAh |
| w7 | 32 | CPU temperature | /1000 → °C |
| w8 | 36 | unknown (~46640, noisy; raw ADC?) | |
| w9 | 40 | MOSFET temperature | /1000 → °C |
| w10 | 44 | fan | /1000 → RPM (scale not confirmed) |
| w11 | 48 | unknown (0; external NTC not connected?) | |
| w12 | 52 | load switched on (0/1) | |
| w13 | 56 | unknown | |
| – | 60 | u16 status: `0x20` idle, `0xA0` drawing current (bit 7) | |

Voltage, current and power were confirmed with a 5 V source at 0.2–1.0 A, where
V·I = P and V/I = R hold.

## Timing (measured)

- A request/reply round trip takes about 2 ms, but the values only update about every 170 ms (~6 Hz).
- The current ramps softly: a step from 0.2 A to 0.8 A takes about 2 s to reach ~90 %.
- A mode change (`47`–`4a`) switches the load off, and is applied about 0.25 s after the command.
  If ON is sent right after a mode change, the load switches on and then off again.
- The time limit (`31`) is enforced by the load. A 0:01 limit switched it off after 60.1 s.
