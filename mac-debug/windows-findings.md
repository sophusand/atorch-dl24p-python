# DL24 on macOS: findings from the working Windows PC

Reply to *"DL24P on macOS: the load receives commands but never replies"*.
Measured on 2026-09-23 on the Windows 10 PC with the same load (serial `989407`, `ATORCH DL24 V1.1.0`),
using USBPcap from unplug/replug through `python -m dl24p`.

## Short version

On Windows the load needs nothing special. After a fresh plug-in the first command gets a reply
about 1 ms later. Windows sends no hidden init sequence beyond the standard HID class driver setup.
Several of the report's theories are ruled out, and one test on the Mac should settle the rest:
**connect the load without the hub dongle** (see "Tests on the Mac", test A).

## Answers to "Please do on the Windows PC"

| Question | Answer |
|---|---|
| Does `python -m dl24p` work? | Yes, including right after a fresh replug. |
| Direct or through a hub? | **Through a hub.** The load is on port 4 of the chipset's **Intel Rate Matching Hub `8087:0024`**, a USB 2.0 high-speed hub with a Transaction Translator, behind an Intel 6-series **EHCI** controller. Windows therefore also uses split transactions. The difference from the Mac is the hub chip (Intel RMH vs. the dongle's hub) and the controller (EHCI vs. xHCI). |
| How is it powered? | **From its own barrel-jack supply. The USB cable (USB-C on the load, USB-A on the host) carries data only.** The config descriptor claims otherwise (**bus-powered, 100 mA, remote wakeup**: `bmAttributes 0xA0`, `bMaxPower 0x32`), which is harmless. Power is therefore the same on both hosts, which rules out theory 4. |
| Interrupt OUT or SET_REPORT? | **Interrupt OUT on EP 0x01.** A `SET_REPORT(Output)` on EP0 is **STALLed** by the load (`USBD_STATUS_STALL_PID`) and the command is not executed. Because `0x46` did open the menu on the Mac, macOS must have used interrupt OUT too, so this is not the difference. |
| OUT → IN timing | The reply is on the wire 0.9–1.0 ms after the OUT completes. Host round trip 0.94–1.04 ms (20/20 replies). |
| Anything before the first frame? | No. Only the standard sequence below. |

## Wire sequence on Windows after replug (from the capture)

```
hub   PORT_RESET (x2), enumeration
dev   GET_DESCRIPTOR DEVICE  -> 12 01 10 01 00 00 00 40 83 04 50 57 00 01 01 02 03 01
                                (USB 1.1, EP0 64 bytes, 0483:5750, bcdDevice 1.00)
dev   GET_DESCRIPTOR CONFIG  -> 09 02 29 00 01 01 00 a0 32 ... (bus-powered, remote wakeup, 100 mA)
dev   SET_CONFIGURATION(1)
dev   HID SET_IDLE(0)                      <- sent by the Windows HID class driver
dev   GET_DESCRIPTOR HID REPORT (34 bytes, same as on the Mac)
dev   2x interrupt IN queued on EP 0x81     <- Windows always keeps IN transfers pending
dev   string descriptors 1..3 (hidapi enumerate)
...   ~3 s idle
dev   INT OUT EP 0x01: 55 05 01 03 ...   -> 1.9 ms later INT IN EP 0x81: aa 05 01 03 ...
dev   INT OUT EP 0x01: 55 05 01 05 ...   -> ~1 ms later  INT IN EP 0x81: aa 05 01 05 ...
dev   HID SET_REPORT(Output, id 0) on EP0 -> STALL
dev   20x OUT/IN pairs, all answered
...   5 s after the last handle closes:
hub   Windows cancels the IN transfers and sends SET_PORT_FEATURE(PORT_SUSPEND) (selective suspend).
      On the next open it resumes the port and sends SET_IDLE(0) again before polling IN.
```

## What this rules out, and what is left

- **No secret init or handshake.** A fresh device replies to the very first command after the standard enumeration.
- **Not SET_REPORT vs. interrupt OUT.** The load only accepts interrupt OUT.
- **A TT/hub in general is not the problem**, because Windows also goes through one. A problem with
  *this specific dongle hub*, or with FS interrupt IN splits on Apple's xHCI, is still possible.
- **The `InputReportCount` rising together with `SetReportCount` in the Mac's DebugState** fits
  "the load answers every command, but the IN packet is lost or errors out before it reaches any
  client", for example in the hub's split transactions. It fits less well with "the load does not answer".
- **Not power.** The load runs from its barrel-jack supply on both hosts.
- Differences from Windows that are still open: **the hub dongle**, **no HID `SET_IDLE(0)`** (macOS may
  not send it to a vendor-defined device), and **no selective-suspend/resume cycles** on the Mac.

## Tests on the Mac, in order

**A. Bypass the hub (most important).** Use the same USB-C (load) → USB-A cable, but plug it into a
*passive* USB-C (male) → USB-A (female) adapter with no hub or card reader, then run `python -m dl24p`.
The adapter provides the CC resistor that the load's own USB-C port lacks, so the Mac will enumerate it.
- Works: the dongle's hub is the cause. Use a direct adapter, or another/powered USB 2.0 hub.
- Still fails: the hub is ruled out, so continue with B.

**B. Clean power cycle.** **Pull the barrel jack.** Unplugging the USB cable does *not* restart the
load's MCU, because the barrel-jack supply keeps it running, so every "replug" so far kept the firmware
state. Wait 10 s, reconnect power and USB, and run `python -m dl24p` as the very first thing. This tests
the "IN path stuck" theory.

**C. Repeat Windows' exact sequence at USB level:** `sudo python3 mac_probe.py` (in this folder).
It detaches the macOS driver and sends `SET_CONFIGURATION(1)` and `HID SET_IDLE(0)`. It then sends
`0x46`/`0x45` so you can **watch the display confirm the libusb OUT path**, and reads replies in
two ways: write-then-read, and read-pending-before-write like Windows. Run it once normally and once
with `--reset`. The script also prints the port path, which shows whether a hub is in the path.
- If step 2 moves the display but steps 3/4 time out, and test A also failed, then the load really
  does not send on the Mac. In that case compare with the Windows capture in Wireshark.

**D. Tell us what the display shows when connected to Windows**, compared with "HID" on the Mac.
A different text would mean the firmware treats the two hosts differently.

## Files

- `mac_probe.py` (this folder): the probe for test C. Written on Windows and not run on a Mac yet.
- The USBPcap capture (4.7 MB, not in git) is kept on the Windows PC in `mac-debug/`. It covers the
  unplug, the replug and enumeration, `python -m dl24p`, the SET_REPORT attempt and 20 OUT/IN pairs,
  then the selective suspend 5 s later. It can be opened in Wireshark, and the DL24 is device address
  2 after the replug. Ask for it if you want to compare packet by packet.
