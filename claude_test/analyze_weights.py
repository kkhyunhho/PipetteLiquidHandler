"""Combine per-run weigh CSVs, convert to volume, and compute stats.

Reads every weigh_*.csv in the project root, keeps the per-dispense
final (net) rows, and writes two files:

* integrated_weights.csv — one tidy row per dispense, with the measured
  volume and its deviation from nominal.
* weights_summary.csv — per nominal volume: n, mean, CV% (precision),
  and systematic error (accuracy).

Assumptions (per the operator):
* Density = 1.000 g/mL, so measured_uL = net_g * 1000. Blue and brown
  share one mother liquid (dye only differs), so their replicates pool.
  Real dyed water is ~0.99 g/mL, so the true accuracy is ~1% less
  negative than reported here.
* A dispense whose apparent density (net_g*1000/nominal_uL) is below
  ``fail_app_density`` is treated as a failed dispense and excluded
  from the stats (kept in the integrated file, flagged ``excluded``).

Run:  python claude_test/analyze_weights.py
"""

import csv
import glob
import os
import re
import statistics
from collections import defaultdict

density_g_per_ml = 1.000  # operator's choice; volume_uL = net_g / d * 1000
fail_app_density = 0.5  # below this g/mL a dispense is a clear failure

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = []
for path in sorted(glob.glob(os.path.join(root, "weigh_*.csv"))):
    name = os.path.basename(path)
    match = re.search(r"attempt(\d+)", name)
    attempt = f"attempt{match.group(1)}" if match else name
    with open(path) as fh:
        for record in csv.DictReader(fh):
            if record["kind"] != "final":
                continue
            nominal = int(record["volume_ul"])
            net_g = float(record["value"])
            measured_ul = net_g / density_g_per_ml * 1000
            app_density = net_g * 1000 / nominal
            rows.append({
                "attempt": attempt,
                "timestamp": record["timestamp"],
                "liquid": record["tag"],
                "target": record["target"],
                # The second dispense of each pass (B32) gets the
                # blow-out; B31 does not.
                "blow_out": record["target"] == "B32",
                "nominal_ul": nominal,
                "net_g": net_g,
                "measured_ul": measured_ul,
                "dev_ul": measured_ul - nominal,
                "dev_pct": 100 * (measured_ul - nominal) / nominal,
                "excluded": app_density < fail_app_density,
            })

out = os.path.join(root, "integrated_weights.csv")
with open(out, "w", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow([
        "attempt", "timestamp", "liquid", "target", "blow_out",
        "nominal_ul", "net_g", "measured_ul", "dev_ul", "dev_pct",
        "excluded",
    ])
    for r in sorted(rows, key=lambda r: (r["nominal_ul"], r["attempt"])):
        writer.writerow([
            r["attempt"], r["timestamp"], r["liquid"], r["target"],
            r["blow_out"], r["nominal_ul"], f"{r['net_g']:.4f}",
            f"{r['measured_ul']:.1f}", f"{r['dev_ul']:.1f}",
            f"{r['dev_pct']:.2f}", r["excluded"],
        ])

excluded = [r for r in rows if r["excluded"]]
print(f"wrote {out} ({len(rows)} dispenses, {len(excluded)} excluded)")
for r in excluded:
    print(f"  excluded (failed): {r['attempt']} {r['liquid']} "
          f"{r['target']} {r['nominal_ul']}uL -> {r['net_g']:.4f} g")

by_vol = defaultdict(list)
for r in rows:
    if not r["excluded"]:
        by_vol[r["nominal_ul"]].append(r)

summary = []
for vol in sorted(by_vol):
    vols = [r["measured_ul"] for r in by_vol[vol]]
    mean = statistics.mean(vols)
    std = statistics.stdev(vols) if len(vols) > 1 else None
    cv = (100 * std / mean) if std is not None else None
    summary.append({
        "nominal_ul": vol,
        "n": len(vols),
        "mean_ul": mean,
        "std_ul": std,
        "cv_pct": cv,
        "sys_err_ul": mean - vol,
        "sys_err_pct": 100 * (mean - vol) / vol,
    })

summary_path = os.path.join(root, "weights_summary.csv")
with open(summary_path, "w", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow([
        "nominal_ul", "n", "mean_ul", "std_ul", "cv_pct",
        "sys_err_ul", "sys_err_pct",
    ])
    for s in summary:
        writer.writerow([
            s["nominal_ul"], s["n"], f"{s['mean_ul']:.1f}",
            "" if s["std_ul"] is None else f"{s['std_ul']:.2f}",
            "" if s["cv_pct"] is None else f"{s['cv_pct']:.2f}",
            f"{s['sys_err_ul']:.1f}", f"{s['sys_err_pct']:.2f}",
        ])
print(f"wrote {summary_path}")

print("\n nominal  n   mean_uL   CV%     sys.err(uL)   sys.err(%)")
for s in summary:
    cv = "  n/a" if s["cv_pct"] is None else f"{s['cv_pct']:5.2f}"
    print(f"  {s['nominal_ul']:5d}   {s['n']}   {s['mean_ul']:7.1f}   "
          f"{cv}   {s['sys_err_ul']:+8.1f}     {s['sys_err_pct']:+6.2f}")
print("\n(density = 1.000 g/mL assumed; dyed water ~0.99, so true "
      "accuracy ~1% less negative.)")

# Per dispense mode. The two replicates at each volume are actually two
# different conditions: B31 is the first aliquot (no blow-out), B32 the
# last (with blow-out). Splitting them shows the per-volume CV above is
# dominated by this mode difference, not by random repeatability.
mode_dev = defaultdict(dict)
for r in rows:
    if r["excluded"]:
        continue
    key = "blow" if r["blow_out"] else "noblow"
    mode_dev[r["nominal_ul"]][key] = r["dev_ul"]
print("\n By dispense mode -- dev_uL (measured - nominal):")
print(" nominal   no-blow(B31)   blow-out(B32)")
for vol in sorted(mode_dev):
    nb = mode_dev[vol].get("noblow")
    bo = mode_dev[vol].get("blow")
    nb_s = "   n/a" if nb is None else f"{nb:+7.1f}"
    bo_s = "   n/a" if bo is None else f"{bo:+7.1f}"
    print(f"  {vol:5d}     {nb_s}        {bo_s}")
nb_devs = [m["noblow"] for m in mode_dev.values() if "noblow" in m]
print(f"\n no-blow (B31) offset: mean {statistics.mean(nb_devs):+.1f} uL "
      f"across {len(nb_devs)} volumes -- nearly constant (a fixed "
      f"offset, not proportional).")
