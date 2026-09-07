# -*- coding: utf-8 -*-
"""
Created on Sat Sep 27 16:05:56 2025

@author: user
"""

import os
import sys
from geomeppy import IDF
from state_schema import SimulationState

# Set the IDD path
IDD_PATH = "C:/EnergyPlusV8-9-0/Energy+.idd"
IDF.setiddname(IDD_PATH)

def remove_named_constructions(idf, names_to_remove):
    target_names = {str(x).strip().upper() for x in names_to_remove}
    removed = []

    for cons in list(idf.idfobjects["CONSTRUCTION"]):
        name = str(cons.Name).strip().upper()
        if name in target_names:
            idf.removeidfobject(cons)
            removed.append(cons.Name)

    print(f"✓ Removed {len(removed)} constructions: {removed}")
  
def set_simulation_control_to_runperiod_only(idf):
    """Ensure SimulationControl runs only for weather-file run periods, not sizing."""
    # Remove any existing SimulationControl objects
    for sc in list(idf.idfobjects["SIMULATIONCONTROL"]):
        idf.removeidfobject(sc)

    # Create a clean SimulationControl
    sc = idf.newidfobject("SIMULATIONCONTROL")
    sc.Do_Zone_Sizing_Calculation = "No"
    sc.Do_System_Sizing_Calculation = "No"
    sc.Do_Plant_Sizing_Calculation = "No"
    sc.Run_Simulation_for_Sizing_Periods = "No"
    sc.Run_Simulation_for_Weather_File_Run_Periods = "Yes"

    print("✓ SimulationControl updated: RunPeriod only (sizing calculations disabled).")
    return sc



def idf_defi_output(state: SimulationState) -> SimulationState:
    # --- ERROR GATE -------------------------------------------------------
    # If any upstream node failed, stop here. Without this, a stale IDF left
    # on disk by a PREVIOUS successful run passes the os.path.exists() check
    # below and gets simulated, so a failed run silently returns the previous
    # building's results.
    if state.get("errors"):
        return {"errors": list(state["errors"])}
    blocking = [d for d in (state.get("layout_diagnostics") or [])
                if d.get("code") != "D6_OPENING"]
    if blocking:
        return {"errors": [d.get("message", str(d)) for d in blocking]}
    # ----------------------------------------------------------------------
    print("[Multi] entering energyplus_defi_output with:", state.get("idf_path"),
          file=sys.stderr)

    idf_path = state.get("idf_path")
    epw_path = state.get("epw_path")
    user_input = state.get("user_input", "").lower()

    if not idf_path or not os.path.exists(idf_path):
        return {"errors": ["No IDF path found to modify."]}

    try:
        idf = IDF(idf_path)

        # Remove all existing outputs
        idf.idfobjects["OUTPUT:VARIABLE"] = []
        idf.idfobjects["OUTPUT:METER"] = []
        
        # Remove all design day sizing periods
        #idf.idfobjects["SIZINGPERIOD:DESIGNDAY"] = []
        
        # Adjust timestep to 4 per hour
        idf.idfobjects["TIMESTEP"][0].Number_of_Timesteps_per_Hour = 4
        
        # Remove existing Site:Location objects if any
        idf.idfobjects["SITE:LOCATION"] = []
        
        '''# Add Seoul location
        idf.newidfobject(
            "SITE:LOCATION",
            Name="SEOUL_KOR_WMO_471100",
            Latitude=37.566,
            Longitude=126.978,
            Time_Zone=9,
            Elevation=86
        )'''

        
        # Adjust RunPeriod (Jan 1 to Jan 2)
        if idf.idfobjects["RUNPERIOD"]:
            runperiod = idf.idfobjects["RUNPERIOD"][0]
            runperiod.Begin_Month = 1
            runperiod.Begin_Day_of_Month = 1
            runperiod.End_Month = 12
            runperiod.End_Day_of_Month = 31
        else:
            idf.newidfobject(
                "RUNPERIOD",
                Name="Custom RunPeriod",
                Begin_Month=1,
                Begin_Day_of_Month=1,
                End_Month=1,
                End_Day_of_Month=2
            )

        idf.idfobjects["SIZINGPERIOD:DESIGNDAY"] = []
        idf.idfobjects["SITE:GROUNDTEMPERATURE:BUILDINGSURFACE"] = []

        idf.newidfobject(
            "SITE:GROUNDTEMPERATURE:BUILDINGSURFACE",
            January_Ground_Temperature=18.0,
            February_Ground_Temperature=18.0,
            March_Ground_Temperature=18.0,
            April_Ground_Temperature=18.0,
            May_Ground_Temperature=18.0,
            June_Ground_Temperature=18.0,
            July_Ground_Temperature=18.0,
            August_Ground_Temperature=18.0,
            September_Ground_Temperature=18.0,
            October_Ground_Temperature=18.0,
            November_Ground_Temperature=18.0,
            December_Ground_Temperature=18.0
        )
        
        remove_named_constructions(
            idf,
            [
                "PROJECT WALL",
                "PROJECT PARTITION",
                "PROJECT CEILING",
                "PROJECT DOOR",
                "PROJECT EXTERNAL WINDOW",
            ]
        )
        # ✅ Add indoor temperature outputs if requested
        #print("❗User input received:", repr(user_input))

        #if "indoor temperature" in user_input or "zone temperature" in user_input:
        for zone in idf.idfobjects["ZONE"]:
            idf.newidfobject(
                "OUTPUT:VARIABLE",
                Key_Value=zone.Name,
                Variable_Name="Zone Mean Air Temperature",
                Reporting_Frequency="hourly"
            )        
        
        set_simulation_control_to_runperiod_only(idf)
        #%%
        # Save with updated path (IMPORTANT!)
        # ✅ Save modified IDF
        modified_path = idf_path.replace(".idf", "_modified.idf")
        idf.saveas(modified_path)

        return {
            "idf_path": modified_path,
            "epw_path": epw_path or "C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw",
            "output_dir": state.get("output_dir", "eplusout"),
            "message": "IDF modified: only hourly indoor temperature outputs included."
        }

    except Exception as e:
        return {"errors": [f"Error modifying IDF: {str(e)}"]}
