"""macOS fix for a DL24 that receives commands but never replies.

After it is plugged in on a Mac, the load often does not answer until it gets the HID
SET_IDLE(0) request that Windows sends and macOS does not. This module sends that request
through libusb. It first tries without root, next to the macOS HID driver. If that is not
enough, the full fix detaches the HID driver for a moment and also sends SET_CONFIGURATION(1),
like Windows. That needs root:

    sudo python -m dl24p.macfix
    python -m dl24p.macfix --no-root     # only the part that needs no root

DL24P() does this automatically on macOS: if the load does not reply, it tries the fix without
root first, and only if the load still does not reply, it asks for your password with the macOS
dialog, runs the full fix and tries again.

Needs libusb (brew install libusb). It only uses the standard library, because the root
process started from the password dialog may not read files in protected folders such as
the Desktop, where the virtual environment often lives.
"""
import base64
import ctypes
import ctypes.util
import shlex
import subprocess
import sys

VID = 0x0483
PID = 0x5750
_LIBUSB_PATHS = ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib")


def _libusb():
    for path in (ctypes.util.find_library("usb-1.0"),) + _LIBUSB_PATHS:
        if path:
            try:
                lib = ctypes.CDLL(path)
                break
            except OSError:
                pass
    else:
        raise RuntimeError("libusb not found. Install it with: brew install libusb")
    vp, c_int = ctypes.c_void_p, ctypes.c_int
    lib.libusb_init.argtypes = [ctypes.POINTER(vp)]
    lib.libusb_exit.argtypes = [vp]
    lib.libusb_open_device_with_vid_pid.argtypes = [vp, ctypes.c_uint16, ctypes.c_uint16]
    lib.libusb_open_device_with_vid_pid.restype = vp
    lib.libusb_close.argtypes = [vp]
    for name in ("libusb_kernel_driver_active", "libusb_detach_kernel_driver", "libusb_attach_kernel_driver",
                 "libusb_set_configuration", "libusb_claim_interface", "libusb_release_interface"):
        getattr(lib, name).argtypes = [vp, c_int]
    lib.libusb_control_transfer.argtypes = [vp, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
                                            ctypes.c_uint16, ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint]
    lib.libusb_error_name.argtypes = [c_int]
    lib.libusb_error_name.restype = ctypes.c_char_p
    return lib


def send_set_idle(detach: bool = True) -> None:
    """Send HID SET_IDLE(0) to the load.

    With detach=False it sends only SET_IDLE on the default control pipe, next to the macOS HID
    driver. That needs no root and is tried first. With detach=True (must run as root) it detaches
    the HID driver for a moment and also sends SET_CONFIGURATION(1), like Windows.
    """
    lib = _libusb()

    def check(rc, what):
        if rc < 0:
            raise RuntimeError(f"{what} failed: {lib.libusb_error_name(rc).decode()}")

    ctx = ctypes.c_void_p()
    check(lib.libusb_init(ctypes.byref(ctx)), "libusb_init")
    try:
        handle = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
        if not handle:
            raise RuntimeError("could not open the DL24 on USB (not plugged in, or not running as root)")
        try:
            if not detach:
                check(lib.libusb_control_transfer(handle, 0x21, 0x0A, 0, 0, None, 0, 1000), "SET_IDLE")
                return
            detached = lib.libusb_kernel_driver_active(handle, 0) == 1
            if detached:
                check(lib.libusb_detach_kernel_driver(handle, 0), "detaching the macOS HID driver")
            lib.libusb_set_configuration(handle, 1)  # errors ignored: it is usually configured already
            check(lib.libusb_claim_interface(handle, 0), "claiming the interface")
            rc = lib.libusb_control_transfer(handle, 0x21, 0x0A, 0, 0, None, 0, 1000)  # HID SET_IDLE(0)
            lib.libusb_release_interface(handle, 0)
            if detached:
                lib.libusb_attach_kernel_driver(handle, 0)  # macOS also re-attaches it by itself
            check(rc, "SET_IDLE")
        finally:
            lib.libusb_close(handle)
    finally:
        lib.libusb_exit(ctx)


def run_with_password_dialog() -> None:
    """Run this module as root, asking for the password with the macOS dialog."""
    with open(__file__, "rb") as f:
        source = base64.b64encode(f.read()).decode()
    code = f"import base64; exec(base64.b64decode('{source}'), {{'__name__': '__main__'}})"
    # The base interpreter, not the virtual environment (which may be in a protected folder)
    python = getattr(sys, "_base_executable", None) or sys.executable
    command = f"{shlex.quote(python)} -I -c {shlex.quote(code)}"
    applescript_string = command.replace("\\", "\\\\").replace('"', '\\"')
    result = subprocess.run(
        ["osascript", "-e",
         f'do shell script "{applescript_string}" with administrator privileges '
         f'with prompt "The DL24 load needs a USB reset to reply on macOS."'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "the macOS fix was cancelled")


if __name__ == "__main__":
    try:
        send_set_idle(detach="--no-root" not in sys.argv)
    except Exception as e:
        sys.exit(f"DL24 macOS fix failed: {e}")
    print("SET_IDLE sent to the DL24")
