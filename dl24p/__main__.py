"""Quick check from the command line (read-only, never switches the load on).

    python -m dl24p           # device info, settings and one measurement
    python -m dl24p --watch   # stream measurements until Ctrl+C
"""
import argparse

import dl24p


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dl24p", description="Read an ATORCH DL24/DL24P load.")
    parser.add_argument("--watch", action="store_true", help="stream measurements until Ctrl+C")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between measurements (default 1)")
    args = parser.parse_args()

    with dl24p.DL24P() as load:
        print(f"Device:   {load.product}")
        for key, value in load.settings().as_dict().items():
            print(f"  {key:20s} {value}")
        if not args.watch:
            print(load.read())
            return
        try:
            for m in load.stream(args.interval):
                print(m)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
