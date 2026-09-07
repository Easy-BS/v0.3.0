# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 16:32:56 2025

@author: user
"""

# energyplus_runner.py

import os
import subprocess
from state_schema import SimulationState

ENERGYPLUS_EXE_PATH = "C:/EnergyPlusV8-9-0/energyplus.exe"  
WEATHER_FILE = "C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw"                          
#OUTPUT_DIR = "eplusout"
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..','..', 'eplusout'))
#idf_path = './generated_idfs/geom_bui.idf'

def run_energyplus_simulation(idf_path: str, epw_path: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    
    '''command = [
        ENERGYPLUS_EXE_PATH,
        "--weather", epw_path,
        "--output-directory", output_dir,
        "--idf", idf_path
    ]'''
    command = [
    ENERGYPLUS_EXE_PATH,
    "-w", epw_path,
    "-d", output_dir,
    "-r", idf_path
    ]  #8.9.0version_E+

    print("Running EnergyPlus simulation...") 
    
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        print(f"√ [EnergyPlus] Simulation finished.")
        return os.path.join(output_dir, "eplusout.csv")
    except subprocess.CalledProcessError as e:
        print(f"[EnergyPlus] Error: {e.stderr}")
        return None

def parse_simulation_output(csv_path: str) -> dict:
    import pandas as pd

    try:
        df = pd.read_csv(csv_path, skiprows=0)  # May need to adjust depending on output format
        # Example: Extract total cooling energy (you should customize this)
        cooling_energy = df[df.columns[-1]].sum()  # Dummy example: use real variable name
        return {
            "cooling_energy_kWh": round(cooling_energy, 2),
            "raw_output_path": csv_path
        }
    except Exception as e:
        print(f"[Parser] Error parsing simulation result: {e}")
        return {"error": str(e)}

def energyplus_runner(state: SimulationState) -> SimulationState:
    # --- ERROR GATE (see energyplus_defi_output.py for the rationale) ------
    if state.get("errors"):
        return {"errors": list(state["errors"])}
    blocking = [d for d in (state.get("layout_diagnostics") or [])
                if d.get("code") != "D6_OPENING"]
    if blocking:
        return {"errors": [d.get("message", str(d)) for d in blocking]}
    # ----------------------------------------------------------------------
    idf_path = state.get("idf_path")
    epw_path = state.get("epw_path", WEATHER_FILE)
    output_dir = state.get("output_dir", OUTPUT_DIR)

    if not idf_path or not os.path.exists(idf_path):
        return {"errors": ["No IDF file found for simulation."]}

    print(f"[Runner] Launching EnergyPlus with IDF: {idf_path}")

    csv_path = run_energyplus_simulation(idf_path, epw_path, output_dir)

    if csv_path:
        results = parse_simulation_output(csv_path)
        return {"simulation_result": results}
    else:
        return {"errors": ["EnergyPlus simulation failed."]}

