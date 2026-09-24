# atorch-dl24p-python

Control an **ATORCH DL24 / DL24P electronic load** from Python over USB. No official PC app is needed.

```python
import dl24p

with dl24p.DL24P() as load:
    load.set_current(0.5)   # constant current, 0.5 A
    load.on()
    print(load.read())      # [ON ]   5.122 V    0.497 A    2.552 W ...
    load.off()
```

The USB protocol was reverse-engineered from the official ATORCH PC app by sniffing its USB
traffic. It is documented byte by byte in [PROTOCOL.md](PROTOCOL.md).

> Not affiliated with or endorsed by ATORCH. Use at your own risk.

## Tested with

- ATORCH DL24 (USB product string `ATORCH DL24 V1.1.0`), USB ID `0483:5750`
- Windows 10, Python 3.11
- macOS 26 on an M2 MacBook, Python 3.14. See [Platform notes](#platform-notes).
- Linux should work through `hidapi` but has **not been tested** yet.

If you try it on another platform or firmware, please open an issue with the result.

## Install

```
pip install git+https://github.com/sophusand/atorch-dl24p-python.git
```

Or clone/download this repository and run `pip install .` in the folder.
The only dependency is [`hidapi`](https://pypi.org/project/hidapi/). Do not confuse it with the
unrelated `hid` package, which uses the same `import hid` name and does not work here.

**Close the ATORCH PC app** while you use this library. The app polls the load all the time, and
its replies mix with yours.

## Quick check from the command line

```
python -m dl24p            # device info, all settings and one measurement
python -m dl24p --watch    # stream measurements until Ctrl+C
```

Both commands only read from the load and never switch it on.

## Usage

```python
import dl24p

with dl24p.DL24P() as load:
    m = load.read()
    print(m.voltage, m.current, m.power, m.capacity_mah, m.energy_wh, m.temp_mos)

    load.set_power(2.0)          # constant power, 2 W
    load.set_time_limit(0, 30)   # the load switches itself off after 30 minutes
    load.on()
    for m in load.stream(interval=1.0):
        print(m)
        if not m.output_on:      # switched off by the time limit or a protection
            break
    load.off()
```

See [examples/battery_discharge.py](examples/battery_discharge.py) for a complete battery
discharge test with CSV logging:

```
python examples/battery_discharge.py --current 1.0 --cutoff 3.0 --out discharge.csv
```

## API

| Method | Description |
|---|---|
| `dl24p.find()` | List connected loads (hidapi info dicts) |
| `DL24P(path=None)` | Open the first load, or a specific one via `path` from `find()` |
| `read()` | Live values → `Measurement`: `voltage`, `current`, `power`, `resistance`, `energy_wh`, `capacity_mah`, `temp_cpu`, `temp_mos`, `fan_rpm`, `output_on`, `raw` |
| `settings()` | All settings → `Settings` (mode, set point, protections, display, calibration factors) |
| `stream(interval)` | Endless generator of measurements |
| `on()` / `off()` / `is_on` | Switch the load on/off (checked against the device) |
| `set_mode("CC" \| "CV" \| "CR" \| "CP")` | Change mode (this switches the load off) |
| `set_value(x)` | Set point of the current mode: A / V / Ω / W |
| `set_current(A)`, `set_voltage(V)`, `set_resistance(Ω)`, `set_power(W)` | Change mode and set the value in one call |
| `set_time_limit(h, m)` | Maximum run time, enforced by the load (0, 0 = off) |
| `set_cutoff_voltage(V)` | Stored and displayed, but **not enforced** by the load (see below) |
| `set_over_current(A)`, `set_over_power(W)` | Protections |
| `set_over_temp_ext(°C)`, `set_over_temp_mos(°C)` | Temperature protections |
| `set_brightness(1-9)`, `set_standby_brightness(1-9)`, `set_standby_time(1-60)` | Display |
| `set_language("CN-A" \| "CN-B" \| "EN-A")` | Display language |
| `send(cmd, data)` / `query(cmd)` | Raw protocol access |

Every `set_*` method reads the settings back and raises `dl24p.DL24PError` if the load did not
accept the value.

## Good to know

- **Timing:** `read()` takes about 2 ms, but the load only updates its values about every 170 ms
  (~6 Hz). The current ramps up softly: a step from 0.2 A to 0.8 A takes about 2 s to settle.
- **Changing mode switches the load off.** `set_mode()` only sends the command when the mode
  actually changes, and waits for the load to settle before returning.
- **Cut-off voltage is not enforced.** The load stores and displays `D_Cutoff Volt`, but firmware
  V1.1.0 kept drawing current below it during testing, even with the cut-off set above the
  source voltage. Check `m.voltage` in your own script, like the discharge example does.
- **Time limit is enforced** by the load. It switched off after 60.1 s with a 0:01 limit.
- `set_full_voltage()` / `set_full_current()` (C_Cutoff V/A) were ignored by the load.
- The BRT, PT, CT, CDC, CDCDC and CDxn modes shown in the PC app are not supported by DL24
  firmware V1.1.0.
- Not implemented yet, because sniffing them would have changed data or calibration on the test
  unit: *Data clearing* (reset Wh/mAh), *Current clearing* (zero no-load current),
  *Restore settings* and calibration.

## Safety

- **Built-in limits from the user manual.** `set_current()`, `set_voltage()`, `set_resistance()`,
  `set_power()` and `set_value()` raise `DL24PError` before sending anything if the set point is
  outside the load's ratings, given the input voltage measured at that moment:

  | Limit | Value |
  |---|---|
  | Current | 0–20 A (`dl24p.MAX_CURRENT`) |
  | Voltage | 2–200 V (`dl24p.MIN_VOLTAGE`, `dl24p.MAX_VOLTAGE`) |
  | Power below 36 V | 150 W |
  | Power 36–80 V | 60 W |
  | Power 80–200 V | 45 W (`dl24p.max_power(voltage)`) |

  These limits apply to both the DL24 and the DL24P. The DL24P's 180 W is only its absolute
  maximum. The library enforces them like this:
  1. **Whole-state check before every change.** Before `send()` sends a set point, a protection
     or "load on", it reads all settings and the present voltage, applies the command to them,
     and checks the resulting state as a whole: set point, the current and power it gives,
     and the load's own over-current/over-power protections. If anything is outside the limits,
     nothing is sent and `DL24PError` is raised. Raw `send()` calls are checked the same way.
  2. **Protections within the limits.** `set_over_current()` accepts at most 20 A and
     `set_over_power()` at most the power limit at the present voltage. If the load's
     protections are still above the limits (the factory setting is 25 A), they are lowered to
     the limit at the next change. The load then enforces them itself while it runs.
  3. **Switching on** runs the same check at the present voltage, because the set point may
     have been set before the source was connected.
  4. **Watchdog:** every `read()` (and so `stream()`) switches the load off and raises
     `DL24PError` if the load is on and exceeds the limits, with a 2 % margin for noise.

  The watchdog only works while your script calls `read()`. Between readings, only the load's own
  protections (point 3) are active.
- Stay within the source's limits as well, and set `set_over_current()` / `set_over_power()`
  lower than the load's limits if the source needs it.
- **Be careful with CV mode on stiff sources** (power supplies, batteries). If you set a voltage
  below the source voltage, the load pulls as much current as it can to reach it, limited only by
  the over-current protection.

## Platform notes

- **Windows:** works out of the box, no driver needed.
- **macOS:** tested on an M2 MacBook with macOS 26. Two things to know:
  - The load's USB-C port does not work with a USB-C to USB-C cable (the Mac does not detect it).
    Use a USB-A cable with a USB-C to USB-A adapter or hub.
  - After it is plugged in, the load often receives commands but does not reply, because macOS
    does not send the HID `SET_IDLE` request that Windows sends. `DL24P()` detects this and
    fixes it automatically: the library sends the request through libusb and connects again.
    It first tries without a password; only if that is not enough, macOS asks for your password.
    It needs `pyusb` (installed automatically on macOS) and libusb (`brew install libusb`). You can also run the fix by hand with
    `sudo python -m dl24p.macfix`, or turn it off with `DL24P(mac_fix=False)`.
- **Linux:** needs permission to access the device (not tested). A udev rule such as
  `/etc/udev/rules.d/99-atorch-dl24.rules`:

  ```
  SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5750", MODE="0666"
  SUBSYSTEM=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5750", MODE="0666"
  ```

  Then run `sudo udevadm control --reload-rules` and re-plug the load.

`0483:5750` is STMicroelectronics' generic custom-HID ID, and other STM32 gadgets use it too.
`find()` skips devices whose product string does not mention ATORCH or DL24.

## License

[MIT](LICENSE)
