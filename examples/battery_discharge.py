"""Discharge a battery at constant current and log it to CSV.

The script stops when the voltage drops below --cutoff, or when the optional --minutes
time limit runs out (the time limit is enforced by the load itself).

Note: the DL24 stores the cut-off voltage but does not act on it (firmware V1.1.0),
so the cut-off is checked here in the script.

    python battery_discharge.py --current 1.0 --cutoff 3.0 --out discharge.csv
"""
import argparse
import csv
import time

import dl24p

parser = argparse.ArgumentParser()
parser.add_argument("--current", type=float, required=True, help="discharge current in A")
parser.add_argument("--cutoff", type=float, required=True, help="stop voltage in V")
parser.add_argument("--minutes", type=int, default=0, help="optional time limit in minutes (0 = none)")
parser.add_argument("--out", default="discharge.csv", help="CSV file to write")
args = parser.parse_args()

with dl24p.DL24P() as load, open(args.out, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["time_s", "voltage_V", "current_A", "power_W", "capacity_mAh", "energy_Wh"])

    load.set_current(args.current)
    load.set_cutoff_voltage(args.cutoff)  # shown on the display; enforced below
    load.set_time_limit(args.minutes // 60, args.minutes % 60)
    start = load.read()
    if start.voltage < args.cutoff:
        raise SystemExit(f"Voltage {start.voltage:.3f} V is already below the cut-off {args.cutoff} V")
    row = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    load.on()
    t0 = time.monotonic()
    try:
        for m in load.stream(1.0):
            row = [round(time.monotonic() - t0, 1), m.voltage, m.current, m.power,
                   round(m.capacity_mah - start.capacity_mah, 3), round(m.energy_wh - start.energy_wh, 3)]
            writer.writerow(row)
            f.flush()
            print(m)
            if m.voltage < args.cutoff:
                print(f"Cut-off voltage {args.cutoff} V reached.")
                break
            if not m.output_on:
                print("Load switched itself off (time limit or protection).")
                break
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        load.off()
    print(f"Discharged {row[4]:.1f} mAh / {row[5]:.3f} Wh in {row[0]:.0f} s -> {args.out}")
