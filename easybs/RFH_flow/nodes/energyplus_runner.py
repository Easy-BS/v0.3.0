# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 16:32:56 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/nodes/energyplus_runner.py
import os
import subprocess
from state_schema import SimulationState

# --- Defaults (override via state["epw_path"] / state["output_dir"]) ---
ENERGYPLUS_EXE_PATH = r"C:/EnergyPlusV8-9-0/energyplus.exe"
WEATHER_FILE       = r"C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw"
OUTPUT_DIR         = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "eplusout"))

def _run_energyplus(idf_path: str, epw_path: str, output_dir: str) -> str | None:
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        ENERGYPLUS_EXE_PATH,
        "-w", epw_path,
        "-d", output_dir,
        "-r", idf_path     # EP 8.9 compatible flags
    ]
    print("[EnergyPlus] Starting…")
    try:
        # capture_output to avoid console noise; text=True for str
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("[EnergyPlus] Finished with return code 0.")
        # EP standard CSV name
        csv_path = os.path.join(output_dir, "eplusout.csv")
        return csv_path if os.path.exists(csv_path) else None
    except subprocess.CalledProcessError as e:
        # keep stderr in server logs
        print("[EnergyPlus] Error:")
        print(e.stderr or "")
        return None

def _parse_output(csv_path: str) -> dict:
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        # Minimal placeholder: return row/column counts; customize as you like
        return {
            "raw_output_path": csv_path,
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
        }
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw_output_path": csv_path}

def energyplus_runner(state: SimulationState) -> SimulationState:
    # Inputs from previous node(s)
    idf_path   = state.get("idf_path")
    epw_path   = state.get("epw_path", WEATHER_FILE)
    output_dir = state.get("output_dir", OUTPUT_DIR)

    if not idf_path or not os.path.exists(idf_path):
        return {"errors": [f"No IDF found for simulation: {idf_path}"]}

    print(f"[Runner] Launching EnergyPlus with IDF: {idf_path}")
    csv_path = _run_energyplus(idf_path, epw_path, output_dir)

    # Preserve idf_path; attach results
    if csv_path:
        state["simulation_result"] = _parse_output(csv_path)
    else:
        state["errors"] = ["EnergyPlus simulation failed."]
    state["idf_path"] = state.get("idf_path") or idf_path
    return state
