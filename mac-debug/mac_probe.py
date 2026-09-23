"""Mac-side probe for the DL24 "commands arrive, replies never do" problem.

It repeats exactly what Windows does on the wire (see windows-findings.md):
SET_CONFIGURATION(1) -> HID SET_IDLE(0) -> interrupt OUT on EP 0x01 -> interrupt IN on EP 0x81.

Run with:  sudo python3 mac_probe.py            (normal)
           sudo python3 mac_probe.py --reset    (USB port reset first)

Watch the load's display during step 2: it should open its menu and go back.
Not tested on a Mac, since it was written on the Windows PC.
"""
import sys
import threading
import time

import usb.backend.libusb1
import usb.core
import usb.util


def backend():
    be = usb.backend.libusb1.get_backend()
    if be is None:
        be = usb.backend.libusb1.get_backend(find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib")
    return be


def find():
    dev = usb.core.find(idVendor=0x0483, idProduct=0x5750, backend=backend())
    if dev is None:
        sys.exit("DL24 not found")
    return dev


def frame(cmd):
    body = bytes([0x55, 0x05, 0x01, cmd, 0, 0, 0, 0, 0xEE, 0xFF])
    return body + bytes(64 - len(body))


dev = find()
print(f"found: bus {dev.bus} address {dev.address} port path {dev.port_numbers} speed {dev.speed}")
print("       (a port path with more than one number means the load sits behind a hub)")

if "--reset" in sys.argv:
    print("resetting the USB port ...")
    dev.reset()
    time.sleep(2)
    dev = find()

if dev.is_kernel_driver_active(0):
    dev.detach_kernel_driver(0)
try:
    dev.set_configuration(1)
except usb.core.USBError as e:
    print("set_configuration(1):", e, "(continuing)")
usb.util.claim_interface(dev, 0)

# 1) HID SET_IDLE(0): Windows sends this right after SET_CONFIGURATION and after every resume
dev.ctrl_transfer(0x21, 0x0A, 0x0000, 0, None, 1000)
print("1) SET_IDLE(0) sent")

# 2) Prove that libusb OUT writes reach the load (the report left this unverified)
dev.write(0x01, frame(0x46), 1000)
print("2) sent 0x46: the display should show the settings menu now ...")
time.sleep(2)
dev.write(0x01, frame(0x45), 1000)
print("   sent 0x45: the display should go back")
time.sleep(1)


def show(label, fn):
    try:
        data = bytes(fn())
        print(f"   {label}: REPLY {data[:16].hex(' ')}")
        return True
    except usb.core.USBError as e:
        print(f"   {label}: {type(e).__name__} errno={e.errno} {e}")
        return False


# 3) Plain write-then-read, like the report's probe
print("3) write, then read EP 0x81")
for cmd in (0x05, 0x03):
    dev.write(0x01, frame(cmd), 1000)
    show(f"cmd 0x{cmd:02x}", lambda: dev.read(0x81, 64, 1000))

# 4) Read already pending before the write, like Windows (it always has IN transfers queued)
print("4) IN read pending first, then write")
for cmd in (0x05, 0x03):
    result = {}
    t = threading.Thread(target=lambda: result.update(ok=show(f"cmd 0x{cmd:02x}", lambda: dev.read(0x81, 64, 1500))))
    t.start()
    time.sleep(0.1)
    dev.write(0x01, frame(cmd), 1000)
    t.join()

usb.util.release_interface(dev, 0)
try:
    dev.attach_kernel_driver(0)
except (usb.core.USBError, NotImplementedError):
    pass
