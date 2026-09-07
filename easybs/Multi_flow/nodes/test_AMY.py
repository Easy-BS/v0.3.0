# -*- coding: utf-8 -*-
"""
make_amy_epw.py

Generate Actual Meteorological Year (AMY) EPW files for a non-US station,
using diyepw for the AMY assembly and a locally supplied TMY EPW as the
template.

Why the wrapper is needed
-------------------------
diyepw.create_amy_epw_file() calls diyepw.get_tmy_epw_file(), whose catalog
is built only from the OneBuilding USA and Canada directories. For a Seoul
WMO index it raises before it ever looks for a local file. The AMY side has
no such limitation: NOAA ISD Lite is global.

This script therefore substitutes our own TMY template and leaves the rest of
diyepw untouched.

Usage
-----
    python make_amy_epw.py --wmo 471080 --years 2024 \
        --tmy "weather/KOR_Seoul.471080_TMYx.2009-2023.epw" \
        --outdir weather/amy

Requirements
------------
    pip install diyepw pandas numpy

Network access is required to https://www1.ncdc.noaa.gov (NOAA ISD Lite).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pandas as pd

import importlib

import diyepw
from diyepw import analyze_noaa_isd_lite_file, get_noaa_isd_lite_file

# diyepw/__init__.py re-exports the function create_amy_epw_file under the
# same name as its module, shadowing it. Reach the module explicitly.
_caef = importlib.import_module("diyepw.create_amy_epw_file")


# --------------------------------------------------------------------------
# TMY template substitution
# --------------------------------------------------------------------------

class _TmyOverride:
    """Context manager that makes diyepw use a specific TMY EPW file.

    create_amy_epw_file imports get_tmy_epw_file into its own module
    namespace at import time, so patching that name is sufficient and does
    not affect any other diyepw entry point.
    """

    def __init__(self, tmy_path: str):
        self.tmy_path = os.path.abspath(tmy_path)
        self._original = None

    def __enter__(self):
        if not os.path.isfile(self.tmy_path):
            raise FileNotFoundError(f"TMY EPW not found: {self.tmy_path}")
        self._original = _caef.get_tmy_epw_file

        def _stub(wmo_index, output_dir=None, allow_downloads=False):
            return self.tmy_path

        _caef.get_tmy_epw_file = _stub
        return self

    def __exit__(self, *exc):
        _caef.get_tmy_epw_file = self._original
        return False


# --------------------------------------------------------------------------
# Quality reporting
# --------------------------------------------------------------------------

def isd_quality(wmo: int, year: int, amy_dir: str) -> dict:
    """Download (if needed) and analyse the ISD Lite file for one year."""
    path = get_noaa_isd_lite_file(wmo, year, output_dir=amy_dir, allow_downloads=True)
    report = analyze_noaa_isd_lite_file(path)
    report["file"] = path
    report["year"] = year
    return report


def _read_epw_drybulb(epw_path: str) -> pd.Series:
    """Return the hourly dry-bulb temperature column of an EPW file."""
    df = pd.read_csv(epw_path, skiprows=8, header=None, low_memory=False)
    # EPW column 6 (0-indexed) is dry-bulb temperature in degrees C
    return pd.to_numeric(df[6], errors="coerce")


def hdd18(epw_path: str) -> float:
    """Annual heating degree days, base 18 degrees C, from hourly dry bulb."""
    t = _read_epw_drybulb(epw_path)
    daily_mean = t.groupby(t.index // 24).mean()
    return float((18.0 - daily_mean).clip(lower=0).sum())


def monthly_hdd18(epw_path: str) -> pd.Series:
    """Monthly heating degree days, base 18 degrees C."""
    df = pd.read_csv(epw_path, skiprows=8, header=None, low_memory=False)
    month = pd.to_numeric(df[1], errors="coerce")
    t = pd.to_numeric(df[6], errors="coerce")
    frame = pd.DataFrame({"month": month, "t": t})
    frame["day_index"] = frame.index // 24
    daily = frame.groupby("day_index").agg(month=("month", "first"), t=("t", "mean"))
    daily["hdd"] = (18.0 - daily["t"]).clip(lower=0)
    return daily.groupby("month")["hdd"].sum()


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def build_amy_epws(
    wmo: int,
    years: List[int],
    tmy_epw: str,
    outdir: str,
    amy_dir: str = None,
    max_missing_amy_rows: int = 700,
    max_records_to_interpolate: int = 6,
    max_records_to_impute: int = 48,
) -> Dict[int, str]:
    """Create one AMY EPW per requested year.

    Returns a mapping of year to the generated EPW path.
    """
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    if amy_dir is None:
        amy_dir = os.path.join(outdir, "_isd_cache")
    os.makedirs(amy_dir, exist_ok=True)

    tmy_hdd = hdd18(tmy_epw)
    print(f"TMY template : {os.path.basename(tmy_epw)}")
    print(f"TMY HDD18    : {tmy_hdd:,.0f}\n")

    results: Dict[int, str] = {}

    with _TmyOverride(tmy_epw):
        for year in years:
            print(f"--- {year} ---")

            # diyepw needs the following year as well, because it shifts the
            # ISD series from UTC into local time.
            for y in (year, year + 1):
                try:
                    rep = isd_quality(wmo, y, amy_dir)
                    print(f"  ISD {y}: missing {rep['total_rows_missing']} rows, "
                          f"largest gap {rep['max_consec_rows_missing']}")
                except Exception as e:
                    print(f"  ISD {y}: FAILED - {e}")
                    raise

            try:
                path = diyepw.create_amy_epw_file(
                    wmo_index=wmo,
                    year=year,
                    amy_epw_dir=outdir,
                    amy_dir=amy_dir,
                    allow_downloads=True,
                    max_records_to_interpolate=max_records_to_interpolate,
                    max_records_to_impute=max_records_to_impute,
                    max_missing_amy_rows=max_missing_amy_rows,
                )
            except Exception as e:
                print(f"  FAILED: {e}\n")
                continue

            amy_hdd = hdd18(path)
            delta = 100.0 * (amy_hdd - tmy_hdd) / tmy_hdd
            print(f"  written  : {os.path.basename(path)}")
            print(f"  HDD18    : {amy_hdd:,.0f}  ({delta:+.1f}% vs TMY)\n")
            results[year] = path

    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wmo", type=int, required=True,
                    help="WMO index of the station, e.g. 471080 for Seoul")
    ap.add_argument("--years", type=int, nargs="+", required=True,
                    help="One or more calendar years, e.g. 2021 2022 2023 2024")
    ap.add_argument("--tmy", required=True,
                    help="Path to a TMY EPW file for the same station")
    ap.add_argument("--outdir", default="weather/amy",
                    help="Directory for the generated AMY EPW files")
    ap.add_argument("--max-missing-rows", type=int, default=700,
                    help="Reject an ISD year with more than this many missing hours")
    ap.add_argument("--report", default=None,
                    help="Optional CSV path for the monthly HDD18 comparison")
    args = ap.parse_args()

    results = build_amy_epws(
        wmo=args.wmo,
        years=args.years,
        tmy_epw=args.tmy,
        outdir=args.outdir,
        max_missing_amy_rows=args.max_missing_rows,
    )

    if not results:
        print("No AMY files were generated.", file=sys.stderr)
        sys.exit(1)

    print("Generated:")
    for y, p in sorted(results.items()):
        print(f"  {y}: {p}")

    if args.report:
        frame = pd.DataFrame({"TMY": monthly_hdd18(args.tmy)})
        for y, p in sorted(results.items()):
            frame[str(y)] = monthly_hdd18(p)
        frame.index.name = "month"
        frame.round(1).to_csv(args.report)
        print(f"\nMonthly HDD18 comparison written to {args.report}")


if __name__ == "__main__":
    main()
