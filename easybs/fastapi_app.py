# -*- coding: utf-8 -*-
"""
Created on Sat Oct 25 01:23:34 2025

@author: Xiguan Liang @SKKU
"""
# ./fastapi_app.py
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, Any
import subprocess, time
import uvicorn
from fastapi.staticfiles import StaticFiles
import matplotlib, os, uuid
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os, glob, time, shutil
from fastapi.staticfiles import StaticFiles
from fastapi import HTTPException
import pandas as pd
import re

from run_graph import build_idf_from_prompt, build_idfs_from_file   

ENERGYPLUS_EXE = r"C:\EnergyPlusV8-9-0\energyplus.exe"   # <-- set correctly
EPW_PATH       = r"C:\EnergyPlusV8-9-0\WeatherData\KOR_INCH'ON_IWEC.epw"  
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "ep_outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="LangGraph + EnergyPlus API", version="0.3.0")

# --- add these imports ---
import glob
from fastapi.staticfiles import StaticFiles
from fastapi import HTTPException

# --- use the SAME on-disk folder where your PNGs are saved ---
#PREVIEW_DIR = os.path.join(os.path.dirname(__file__), "3D_png")
#os.makedirs(PREVIEW_DIR, exist_ok=True)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PLOT_DIR   = os.environ.get("PLOT_DIR",   os.path.join(BASE_DIR, "preview_plots"))
RENDER_DIR = os.environ.get("RENDER_DIR", os.path.join(BASE_DIR, "preview_3d"))

os.makedirs(PLOT_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)

#%%
def _plot_rfh_heating_rate(csv_path: str, out_dir: str, sum_all: bool = True) -> str:
    """
    Plot 'Plant Supply Side Heating Demand Rate [W](Hourly)' from eplusout.csv.
    If multiple plant loops are present, sum them into one curve by default.
    """
    df = pd.read_csv(csv_path)
    if "Date/Time" not in df.columns:
        raise ValueError("CSV missing 'Date/Time' column.")

    # Match columns like: "HOT WATER LOOP:Plant Supply Side Heating Demand Rate [W](Hourly)"
    pat = re.compile(r":\s*Plant Supply Side Heating Demand Rate\s*\[W\]\(Hourly\)\s*$", re.IGNORECASE)
    cols = [c for c in df.columns if pat.search(c)]
    if not cols:
        raise ValueError("No 'Plant Supply Side Heating Demand Rate [W](Hourly)' columns found.")

    # X-axis as 0..N-1 hours (EnergyPlus datetime parsing not needed for a simple hourly plot)
    t = range(len(df))

    plt.ioff()
    fig, ax = plt.subplots(figsize=(10, 4))

    if sum_all and len(cols) > 1:
        y = df[cols].sum(axis=1)
        ax.plot(t, y.values, linewidth=1.2, label="Total Heating Demand Rate [W]")
        ax.legend(loc="upper right", ncol=1, fontsize=9, frameon=False)
    else:
        for c in cols:
            loop_label = c.split(":")[0].strip()
            ax.plot(t, df[c].values, linewidth=1.0, label=loop_label)
        if len(cols) > 1:
            ax.legend(loc="upper right", ncol=1, fontsize=9, frameon=False)

    ax.set_title("Hourly Heating Energy [W]")  # exact title requested
    ax.set_xlabel("Hour")
    ax.set_ylabel("Heating Rate [W]")
    ax.grid(True, linewidth=0.3)

    os.makedirs(out_dir, exist_ok=True)
    fname = f"rfh_heat_{uuid.uuid4().hex[:8]}.png"
    out_path = os.path.join(out_dir, fname)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    return out_path

#%%
def _find_eplus_csv() -> str:
    """
    Try the common locations for eplusout.csv.
    Adjust or hardcode if you always know the exact path.
    """
    # 1) Explicit env var override (recommended if you know it)
    env_path = os.environ.get("EPLUS_CSV")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2) Project-root eplusout/eplusout.csv (your note)
    guess = os.path.join(os.path.dirname(__file__), "eplusout", "eplusout.csv")
    if os.path.isfile(guess):
        return guess

    # 3) Fallback: look in ./ep_outputs (if you use that)
    guess2 = os.path.join(os.path.dirname(__file__), "ep_outputs", "eplusout.csv")
    if os.path.isfile(guess2):
        return guess2

    # 4) Give up
    raise FileNotFoundError("Could not locate eplusout.csv. Set EPLUS_CSV or adjust _find_eplus_csv().")


def _plot_zone_mean_air_temp(csv_path: str, out_dir: str) -> str:
    """
    Read eplusout.csv, extract all 'Zone Mean Air Temperature' columns,
    plot as lines vs time, save PNG, return absolute path.
    """
    df = pd.read_csv(csv_path)
    if "Date/Time" not in df.columns:
        raise ValueError("CSV missing 'Date/Time' column.")

    # extract MAT columns
    pat = re.compile(r":\s*Zone Mean Air Temperature\s*\[C\]\(Hourly\)\s*$", re.IGNORECASE)
    cols = [c for c in df.columns if pat.search(c)]
    if not cols:
        raise ValueError("No 'Zone Mean Air Temperature' columns found.")

    # basic parse of Date/Time (EnergyPlus hourly)
    # EnergyPlus 'Date/Time' is often like '01/01  01:00:00' (no year). We'll treat it as index of 8760.
    # For plotting in order it's okay to just use a range; or create a synthetic datetime with a fake year.
    t = range(len(df))

    # make the plot
    plt.ioff()
    fig, ax = plt.subplots(figsize=(10, 4))
    for c in cols:
        # label uses zone name before the colon
        label = c.split(":")[0].strip()
        ax.plot(t, df[c].values, label=label, linewidth=1.0)

    ax.set_title("Zone Mean Air Temperature [°C]")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Temperature (°C)")
    ax.legend(loc="upper right", ncol=1, fontsize=8, frameon=False)
    ax.grid(True, linewidth=0.3)

    os.makedirs(out_dir, exist_ok=True)
    fname = f"mat_{uuid.uuid4().hex[:8]}.png"
    out_path = os.path.join(out_dir, fname)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    return out_path
#%%
@app.get("/api/plot/zone-mat")
def plot_zone_mat():
    try:
        csv_path = _find_eplus_csv()
        out_path = _plot_zone_mean_air_temp(csv_path, PLOT_DIR)         
        url = "/api/static/plots/" + os.path.basename(out_path)# served by Node

        return {"ok": True, "url": url}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@app.get("/api/plot/rfh-heating-rate")
def plot_rfh_heating_rate():
    try:
        csv_path = _find_eplus_csv()
        out_path = _plot_rfh_heating_rate(csv_path, PLOT_DIR, sum_all=True)
        url = "/api/static/plots/" + os.path.basename(out_path)

        return {"ok": True, "url": url}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#%%
# --- mount static so URLs like /api/static/<filename>.png work ---
app.mount("/api/static/plots",  StaticFiles(directory=PLOT_DIR),   name="api_plots")
app.mount("/api/static/renders", StaticFiles(directory=RENDER_DIR), name="api_renders")

def _find_latest_png_in(folder: str, pattern: str = "*.png"):
    files = glob.glob(os.path.join(folder, pattern))
    return max(files, key=os.path.getmtime) if files else None

@app.get("/api/preview/latest-plot")
def preview_latest_plot():
    latest = _find_latest_png_in(PLOT_DIR, "*.png")
    if not latest:
        raise HTTPException(status_code=404, detail="No plot image found.")
    return {"ok": True, "url": "/api/static/plots/" + os.path.basename(latest)}

@app.get("/api/preview/latest-3d")
def preview_latest_3d():
    latest = _find_latest_png_in(RENDER_DIR, "*.png")
    if not latest:
        raise HTTPException(status_code=404, detail="No 3D render found.")
    return {"ok": True, "url": "/api/static/renders/" + os.path.basename(latest)}


#%%
class SimInput(BaseModel):
    prompt: Optional[str] = None
    read_local_file: bool = False
    input_file_path: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])

def _run_energyplus(idf_path: str, epw_path: str, outdir: str):
    start = time.time()
    cmd = [
        ENERGYPLUS_EXE,
        "--weather", epw_path,
        "--output-directory", outdir,
        "--readvars",
        idf_path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "returncode": p.returncode,
        "duration_s": round(time.time() - start, 2),
        "stdout_tail": _tail(p.stdout, 40),
        "stderr_tail": _tail(p.stderr, 60),
        "output_directory": outdir,
    }

@app.post("/simulate")
def simulate(inp: SimInput):
    try:
        if not inp.read_local_file:
            if not inp.prompt or not inp.prompt.strip():
                return {"ok": False, "error": "Please provide 'prompt' text."}
            final_state, idf_path = build_idf_from_prompt(inp.prompt.strip())
        else:
            # ... your legacy file path flow ...
            pass

        # Helpful while debugging: see exactly what keys your graph produced
        # print("FINAL STATE KEYS:", list(final_state.keys()))  # optional log

        # Be flexible about key names during transition
        idf_path = (
            idf_path
            or final_state.get("idf_path")
            or final_state.get("generated_idf")
            or final_state.get("output_idf")
        )

        if not idf_path or not os.path.isfile(idf_path):
            return {
                "ok": False,
                "error": f"IDF not found. Keys: {list(final_state.keys())}",
                "state": final_state,  # return the whole state for visibility
            }

        ep = _run_energyplus(idf_path, EPW_PATH, OUTPUT_DIR)

        
        return {
            "ok": True,
            "idf_path": idf_path,
            "epw_path": EPW_PATH,
            "energyplus": ep,
            "state": final_state,
        }


    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
