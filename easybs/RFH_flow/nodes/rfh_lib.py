# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 07:20:11 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/nodes/rfh_lib.py

import os
import sys
from collections import defaultdict

try:
    from .text_normalize import build_canonical_zone_map, resolve_all
except ImportError:
    from text_normalize import build_canonical_zone_map, resolve_all
    
try:
    from geomeppy import IDF  
except Exception:
    # fallback to eppy if geomeppy isn't available
    from eppy.modeleditor import IDF  # type: ignore

# -----------------------
# Paths (EDIT THESE)
# -----------------------
IDD_PATH = r"C:/EnergyPlusV8-9-0/Energy+.idd"
#IN_IDF   = r"./detached_house_.idf"          # existing building model
#OUT_IDF  = r"./detached_house_RFH.idf"      # output with RFH added



# -----------------------
# Global loop naming (single building-level loop)
# -----------------------
PLANT_LOOP_NAME        = "Hot Water Loop"
SUPPLY_PUMP_NAME       = "HW Circ Pump"
PURCHASED_HEAT_NAME    = "Purchased Heating"  # District heating source
SUPPLY_SPLITTER_NAME   = "Heating Supply Splitter"
SUPPLY_MIXER_NAME      = "Heating Supply Mixer"
DEMAND_SPLITTER_NAME   = "Reheat Splitter"
DEMAND_MIXER_NAME      = "Reheat Mixer"

# Node names
HW_SUP_INLET_NODE      = "HW Supply Inlet Node"
HW_PUMP_OUTLET_NODE    = "HW Pump Outlet Node"
HW_SUP_OUTLET_NODE     = "HW Supply Outlet Node"
HW_DEMAND_INLET_NODE   = "HW Demand Inlet Node"
HW_DEMAND_OUTLET_NODE  = "HW Demand Outlet Node"

# Setpoint / schedules
HW_SETPOINT_SCHED_NAME = "HW Loop Setpoint Schedule"
HW_SETPOINT_C          = 60.0  # °C supply setpoint 
AVAIL_SCHED_NAME       = "Always On Discrete"
AVAIL_SCHED_TYPE       = "On/Off"

# Simple glazing construction name 
WINDOW_CONS_NAME       = "Simple_DoublePane"  # not used by RFH, but kept for consistency

# -----------------------
# Utility helpers
# -----------------------
def Add_output_Heating_Demand(idf,
                           key_value="*",
                           variable_name="Plant Supply Side Heating Demand Rate",
                           reporting_frequency="Hourly"):
    """Add or update a specific Output:Variable without creating duplicates."""
    ovs = idf.idfobjects["OUTPUT:VARIABLE"]
    # Find existing matches (case-insensitive on the variable name)
    matches = [ov for ov in ovs
               if (ov.Key_Value == key_value and ov.Variable_Name.lower() == variable_name.lower())]
    if matches:
        for ov in matches:
            ov.Reporting_Frequency = reporting_frequency
    else:
        idf.newidfobject(
            "OUTPUT:VARIABLE",
            Key_Value=key_value,
            Variable_Name=variable_name,
            Reporting_Frequency=reporting_frequency
        )

#%%
# ---------- ultra-fast overwrite inserts ----------
def _set_first_existing(obj, candidates, value):
    """Set the first attribute that exists on `obj` from `candidates`."""
    for name in candidates:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return name
    return None

def _read_first_existing(obj, candidates):
    """Read the first existing attribute from `candidates` (or '')."""
    for name in candidates:
        if hasattr(obj, name):
            return getattr(obj, name, "") or ""
    return ""

def _del_by_name(idf, key, name):
    """Delete ALL objects of type `key` whose Name matches `name` (case-insensitive)."""
    nl = name.lower()
    objs = list(idf.idfobjects[key])  # copy
    for o in objs:
        if (getattr(o, "Name", "") or "").lower() == nl:
            idf.removeidfobject(o)

def _new_compact_schedule(idf, name, limits, lines):
    """
    Overwrite (delete if exists) and create a Schedule:Compact with given lines.
    Each element of `lines` is a full clause string, e.g. 'Through: 12/31' or 'Until: 24:00,22.00'.
    """
    _del_by_name(idf, "SCHEDULE:COMPACT", name)
    sch = idf.newidfobject("SCHEDULE:COMPACT", Name=name)
    sch.Schedule_Type_Limits_Name = limits
    for i, val in enumerate(lines, start=1):
        setattr(sch, f"Field_{i}", val)
    return sch

def _new_schedtype_limits(idf, name, low, up, numeric, unit=None):
    _del_by_name(idf, "SCHEDULETYPELIMITS", name)
    stl = idf.newidfobject("SCHEDULETYPELIMITS", Name=name)
    stl.Lower_Limit_Value = low
    stl.Upper_Limit_Value = up
    stl.Numeric_Type = numeric
    if unit is not None and hasattr(stl, "Unit_Type"):
        stl.Unit_Type = unit
    return stl

def force_add_fixed_fragments(idf):
    """Force-add the fixed objects exactly as provided (overwrite if present)."""

    # ScheduleTypeLimits
    _new_schedtype_limits(idf, "TEMPERATURE",  -60, 200, "CONTINUOUS", unit="Temperature")
    _new_schedtype_limits(idf, "CONTROL TYPE",   0,   4, "DISCRETE")

    # ZONE CONTROL TYPE SCHED (1 -> 2 -> 1)
    _new_compact_schedule(idf, "ZONE CONTROL TYPE SCHED", "CONTROL TYPE", [
        "Through: 3/31",
        "For: Alldays",
        "Until: 24:00,1",
        "Through: 9/30",
        "For: Alldays",
        "Until: 24:00,2",
        "Through: 12/31",
        "For: Alldays",
        "Until: 24:00,1",
    ])

    # HEATING SETPOINTS
    _new_compact_schedule(idf, "HEATING SETPOINTS", "TEMPERATURE", [
        "Through: 12/31",
        "For: Weekdays Weekends Holidays CustomDay1 CustomDay2",
        "Until: 7:00,15.00",
        "Until: 17:00,20.00",
        "Until: 24:00,15.00",
        "For: SummerDesignDay",
        "Until: 24:00,15.00",
        "For: WinterDesignDay",
        "Until: 24:00,20.00",
    ])

    # COOLING SETPOINTS
    _new_compact_schedule(idf, "COOLING SETPOINTS", "TEMPERATURE", [
        "Through: 12/31",
        "For: Alldays",
        "Until: 24:00,26.00",
    ])

    # RADIANT HEATING SETPOINTS
    _new_compact_schedule(idf, "RADIANT HEATING SETPOINTS", "TEMPERATURE", [
        "Through: 12/31",
        "For: Alldays",
        "Until: 7:00,18.00",
        "Until: 17:00,22.00",
        "Until: 24:00,18.00",
    ])

    # RADIANT COOLING SETPOINTS
    _new_compact_schedule(idf, "RADIANT COOLING SETPOINTS", "TEMPERATURE", [
        "Through: 12/31",
        "For: Alldays",
        "Until: 24:00,26.00",
    ])

    # Thermostat setpoint objects
    _del_by_name(idf, "THERMOSTATSETPOINT:SINGLEHEATING", "Heating Setpoint with SB")
    tsh = idf.newidfobject("THERMOSTATSETPOINT:SINGLEHEATING", Name="Heating Setpoint with SB")
    tsh.Setpoint_Temperature_Schedule_Name = "HEATING SETPOINTS"

    _del_by_name(idf, "THERMOSTATSETPOINT:SINGLECOOLING", "Cooling Setpoint with SB")
    tsc = idf.newidfobject("THERMOSTATSETPOINT:SINGLECOOLING", Name="Cooling Setpoint with SB")
    tsc.Setpoint_Temperature_Schedule_Name = "COOLING SETPOINTS"


def fast_add_zone_thermostats_for_zones(idf, zone_names):
    """Add/overwrite ZoneControl:Thermostat for the given full Zone.Name values.
 
    IDD-robust: uses Zone_or_ZoneList_Name (8.9) or Zone_Name (other).
    """
    existing_by_zone = {}
    for t in idf.idfobjects["ZONECONTROL:THERMOSTAT"]:
        zname = _read_first_existing(t, ["Zone_or_ZoneList_Name", "Zone_Name"]).lower()
        if zname:
            existing_by_zone[zname] = t
 
    known = {(z.Name or "").lower() for z in idf.idfobjects["ZONE"]}
    skipped = []
 
    for zn in zone_names:
        if zn.lower() not in known:
            skipped.append(zn)
            continue
 
        th = existing_by_zone.get(zn.lower())
        if not th:
            _del_by_name(idf, "ZONECONTROL:THERMOSTAT", f"{zn} Thermostat")
            th = idf.newidfobject("ZONECONTROL:THERMOSTAT")
            th.Name = f"{zn} Thermostat"
 
        _set_first_existing(th, ["Zone_or_ZoneList_Name", "Zone_Name"], zn)
        th.Control_Type_Schedule_Name = "ZONE CONTROL TYPE SCHED"
        th.Control_1_Object_Type = "ThermostatSetpoint:SingleHeating"
        th.Control_1_Name = "Heating Setpoint with SB"
        th.Control_2_Object_Type = "ThermostatSetpoint:SingleCooling"
        th.Control_2_Name = "Cooling Setpoint with SB"
        for i in (3, 4):
            _set_first_existing(th, [f"Control_{i}_Object_Type"], "")
            _set_first_existing(th, [f"Control_{i}_Name"], "")
 
    target_lc = {z.lower() for z in zone_names}
    for r in idf.idfobjects["ZONEHVAC:LOWTEMPERATURERADIANT:VARIABLEFLOW"]:
        zref = (getattr(r, "Zone_Name", "") or "").lower()
        if zref in target_lc:
            r.Heating_Control_Temperature_Schedule_Name = "RADIANT HEATING SETPOINTS"
            r.Cooling_Control_Temperature_Schedule_Name = "RADIANT COOLING SETPOINTS"
 
    return skipped

#%%
def add_zone_sizing_objects(idf, zone_names):
    for zname in zone_names:
        sz = idf.newidfobject("SIZING:ZONE")
        sz.Zone_or_ZoneList_Name = zname
        sz.Zone_Cooling_Design_Supply_Air_Temperature_Input_Method = "SupplyAirTemperature"
        sz.Zone_Cooling_Design_Supply_Air_Temperature = 16.0
        sz.Zone_Heating_Design_Supply_Air_Temperature_Input_Method = "SupplyAirTemperature"
        sz.Zone_Heating_Design_Supply_Air_Temperature = 40.0
        sz.Zone_Cooling_Design_Supply_Air_Humidity_Ratio = 0.009
        sz.Zone_Heating_Design_Supply_Air_Humidity_Ratio = 0.004
        sz.Design_Specification_Outdoor_Air_Object_Name = ""
        sz.Zone_Heating_Sizing_Factor = 1.0
        sz.Zone_Cooling_Sizing_Factor = 1.0
    print(f"✓ Added {len(zone_names)} Sizing:Zone objects.")
    
    

def add_design_days_seoul(idf):
    """
    Overwrite and add two simple Seoul design days for EnergyPlus 8.9.
    Removes any existing SizingPeriod:DesignDay objects first.
    """

    # Remove old design days
    for obj in list(idf.idfobjects["SIZINGPERIOD:DESIGNDAY"]):
        idf.removeidfobject(obj)

    # Heating design day
    htg = idf.newidfobject("SIZINGPERIOD:DESIGNDAY")
    htg.Name = "Seoul Ann Htg 99.6% Condns DB"
    htg.Month = 1
    htg.Day_of_Month = 21
    htg.Day_Type = "WinterDesignDay"
    htg.Maximum_DryBulb_Temperature = -11.3
    htg.Daily_DryBulb_Temperature_Range = 0.0
    htg.Humidity_Condition_Type = "Wetbulb"
    htg.Wetbulb_or_DewPoint_at_Maximum_DryBulb = -11.3
    htg.Barometric_Pressure = 100487
    htg.Wind_Speed = 4.0
    htg.Wind_Direction = 270
    htg.Rain_Indicator = "No"
    htg.Snow_Indicator = "No"
    htg.Daylight_Saving_Time_Indicator = "No"
    htg.Solar_Model_Indicator = "ASHRAEClearSky"
    htg.Sky_Clearness = 0.0

    # Cooling design day
    clg = idf.newidfobject("SIZINGPERIOD:DESIGNDAY")
    clg.Name = "Seoul Ann Clg 0.4% Condns DB=>MWB"
    clg.Month = 8
    clg.Day_of_Month = 21
    clg.Day_Type = "SummerDesignDay"
    clg.Maximum_DryBulb_Temperature = 31.5
    clg.Daily_DryBulb_Temperature_Range = 8.0
    clg.Humidity_Condition_Type = "Wetbulb"
    clg.Wetbulb_or_DewPoint_at_Maximum_DryBulb = 26.0
    clg.Barometric_Pressure = 100487
    clg.Wind_Speed = 3.5
    clg.Wind_Direction = 270
    clg.Rain_Indicator = "No"
    clg.Snow_Indicator = "No"
    clg.Daylight_Saving_Time_Indicator = "No"
    clg.Solar_Model_Indicator = "ASHRAEClearSky"
    clg.Sky_Clearness = 1.0

    print("✓ Added 2 SizingPeriod:DesignDay objects.")
    
def enable_zone_sizing(idf):
    """Enable zone sizing in SimulationControl."""
    for sc in idf.idfobjects["SIMULATIONCONTROL"]:
        sc.Do_Zone_Sizing_Calculation = "Yes"
        sc.Do_System_Sizing_Calculation = "No"
        sc.Do_Plant_Sizing_Calculation = "No"
    print("✓ Enabled Do Zone Sizing Calculation = Yes")

#%%
def force_radiant_heating_only(idf):
    """
    Make all ZoneHVAC:LowTemperatureRadiant:VariableFlow objects valid & heating-only for EP 8.9:
    - CoolingDesignCapacityMethod = 'CoolingDesignCapacity'
    - CoolingDesignCapacity = 0
    - MaximumColdWaterFlow = 0
    - Cooling inlet/outlet node names blank
    """
    objs = idf.idfobjects["ZONEHVAC:LOWTEMPERATURERADIANT:VARIABLEFLOW"]
    for z in objs:
        # valid method (not 'None')
        if hasattr(z, "Cooling_Design_Capacity_Method"):
            z.Cooling_Design_Capacity_Method = "CoolingDesignCapacity"
        # zero-out capacity/flow
        if hasattr(z, "Cooling_Design_Capacity"):
            z.Cooling_Design_Capacity = 0
        if hasattr(z, "Cooling_Design_Capacity_Per_Floor_Area"):
            z.Cooling_Design_Capacity_Per_Floor_Area = ""
        if hasattr(z, "Fraction_of_Autosized_Cooling_Design_Capacity"):
            z.Fraction_of_Autosized_Cooling_Design_Capacity = ""
        if hasattr(z, "Maximum_Cold_Water_Flow"):
            z.Maximum_Cold_Water_Flow = 0
        # blank cooling nodes so E+ won't search for a chilled loop
        if hasattr(z, "Cooling_Water_Inlet_Node_Name"):
            z.Cooling_Water_Inlet_Node_Name = ""
        if hasattr(z, "Cooling_Water_Outlet_Node_Name"):
            z.Cooling_Water_Outlet_Node_Name = ""

#%%
def _set_first_existing(obj, candidates, value):
    """Set the first attribute that exists on obj from candidates; return True if set."""
    for name in candidates:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return True
    return False

#%%
# ---------- RFH Construction & Materials ----------

def _get_or_make_material(idf, name, roughness, thickness, cond, dens, sh, sm=0.9, tm=0.9, vm=0.9):
    m = next((x for x in idf.idfobjects["MATERIAL"] if (x.Name or "").lower() == name.lower()), None)
    if not m:
        m = idf.newidfobject("MATERIAL")
        m.Name = name
        m.Roughness = roughness
        m.Thickness = thickness
        m.Conductivity = cond
        m.Density = dens
        m.Specific_Heat = sh
        m.Thermal_Absorptance = tm
        m.Solar_Absorptance = sm
        m.Visible_Absorptance = vm
    return m.Name

def ensure_sample_layers_exist(idf):
    """
    Ensure the 5 materials used by the sample Construction:InternalSource exist.
    If absent, create with sensible properties (SI).
    """
    # Names exactly as in example IDF
    n1 = _get_or_make_material(idf, "CONCRETE - DRIED SAND AND GRAVEL 4 IN",
                               "MediumRough", 0.1016, 1.311, 2243, 836)
    n2 = _get_or_make_material(idf, "INS - EXPANDED EXT POLYSTYRENE R12 2 IN",
                               "Rough", 0.0508, 0.035, 25, 1400)
    n3 = _get_or_make_material(idf, "GYP1",
                               "MediumSmooth", 0.0127, 0.16, 800, 1090)
    n4 = _get_or_make_material(idf, "GYP2",
                               "MediumSmooth", 0.0127, 0.16, 800, 1090)
    n5 = _get_or_make_material(idf, "FINISH FLOORING - TILE 1 / 16 IN",
                               "Smooth", 0.0015875, 1.0, 2000, 900)
    return [n1, n2, n3, n4, n5]

def ensure_internal_source_construction_from_sample(idf,
                                                    cis_name="Slab Floor with Radiant",
                                                    source_after=3,
                                                    tempcalc_after=4,
                                                    dim_ctf=1,
                                                    tube_spacing=0.1524):
    """
    Build Construction:InternalSource to match the sample exactly (layer names & indices).
    """
    cis = next((c for c in idf.idfobjects["CONSTRUCTION:INTERNALSOURCE"]
                if (c.Name or "").lower() == cis_name.lower()), None)
    if cis:
        return cis

    layers = ensure_sample_layers_exist(idf)
    cis = idf.newidfobject("CONSTRUCTION:INTERNALSOURCE")
    cis.Name = cis_name
    cis.Source_Present_After_Layer_Number = source_after
    cis.Temperature_Calculation_Requested_After_Layer_Number = tempcalc_after
    cis.Dimensions_for_the_CTF_Calculation = dim_ctf
    cis.Tube_Spacing = tube_spacing

    # Write layers as in sample (Outside + Layer_2..)
    cis.Outside_Layer = layers[0]
    cis.Layer_2 = layers[1]
    cis.Layer_3 = layers[2]
    cis.Layer_4 = layers[3]
    cis.Layer_5 = layers[4]
    return cis

def replace_zone_floor_construction(idf, zone_name, new_construction_name):
    """Assign the internal-source construction to all 'Floor' surfaces in the zone."""
    changed = 0
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Zone_Name or "") == zone_name and (s.Surface_Type or "").lower() == "floor":
            s.Construction_Name = new_construction_name
            changed += 1
    if changed == 0:
        print(f"⚠️ No floor surfaces replaced in zone '{zone_name}'.")
    else:
        print(f"✓ Replaced {changed} floor surface(s) in '{zone_name}' with '{new_construction_name}'.")

# ---------- ZoneHVAC linking (prevent orphaned radiant) ----------

def _get_or_make_equipment_list(idf, zone_name):
    name = f"{zone_name} Equipment"
    eq = next((e for e in idf.idfobjects["ZONEHVAC:EQUIPMENTLIST"]
               if (e.Name or "").lower() == name.lower()), None)
    if not eq:
        eq = idf.newidfobject("ZONEHVAC:EQUIPMENTLIST")
        eq.Name = name
        if hasattr(eq, "Load_Distribution_Scheme"):
            eq.Load_Distribution_Scheme = "SequentialLoad"
    return eq

def _append_zone_equipment(eqlist, obj_type, obj_name, cool_seq=1, heat_seq=1):
    i = 1
    while getattr(eqlist, f"Zone_Equipment_{i}_Object_Type", ""):
        i += 1
    setattr(eqlist, f"Zone_Equipment_{i}_Object_Type", obj_type)
    setattr(eqlist, f"Zone_Equipment_{i}_Name", obj_name)
    setattr(eqlist, f"Zone_Equipment_{i}_Cooling_Sequence", cool_seq)
    setattr(eqlist, f"Zone_Equipment_{i}_Heating_or_NoLoad_Sequence", heat_seq)

def _ensure_equipment_connections(idf, zone_name, eqlist_name):
    ec = next((c for c in idf.idfobjects["ZONEHVAC:EQUIPMENTCONNECTIONS"]
               if (getattr(c, "Zone_Name", "") or "").lower() == zone_name.lower()), None)
    if not ec:
        ec = idf.newidfobject("ZONEHVAC:EQUIPMENTCONNECTIONS")
        ec.Zone_Name = zone_name

        # Minimal air nodes; use safe setter for IDD variants
        _set_first_existing(ec, ["Zone_Air_Node_Name", "Zone_Node_Name"],
                            f"{zone_name} Air Node")
        _set_first_existing(ec,
            ["Zone_Return_Air_Node_Name", "Zone_Return_Air_Node_or_NodeList_Name"],
            f"{zone_name} Return Air Node"
        )

        # These two are optional; leave blank if don't have explicit node lists
        _set_first_existing(ec,
            ["Zone_Air_Inlet_Node_or_NodeList_Name", "Zone_Air_Inlet_Node_Name"],
            ""
        )
        _set_first_existing(ec,
            ["Zone_Air_Exhaust_Node_or_NodeList_Name", "Zone_Air_Exhaust_Node_Name"],
            ""
        )

    # Always point to the EquipmentList (field name is stable)
    ec.Zone_Conditioning_Equipment_List_Name = eqlist_name
    return ec


def link_radiant_to_zone_equipment(idf, zone_name, radiant_obj_name):
    """Add radiant object to the zone’s EquipmentList and ensure EquipmentConnections points to it."""
    eql = _get_or_make_equipment_list(idf, zone_name)
    # Skip if already present
    i = 1
    present = False
    while getattr(eql, f"Zone_Equipment_{i}_Object_Type", ""):
        if (getattr(eql, f"Zone_Equipment_{i}_Object_Type") == "ZoneHVAC:LowTemperatureRadiant:VariableFlow" and
            getattr(eql, f"Zone_Equipment_{i}_Name") == radiant_obj_name):
            present = True
            break
        i += 1
    if not present:
        _append_zone_equipment(eql,
                               "ZoneHVAC:LowTemperatureRadiant:VariableFlow",
                               radiant_obj_name,
                               cool_seq=1, heat_seq=1)
    _ensure_equipment_connections(idf, zone_name, eql.Name)

#%%
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

#%%
def reset_runperiod(
    idf,
    begin_month=1, begin_day=1,
    end_month=1, end_day=14,
    start_dow="Tuesday",  # set "" to follow EPW’s weekday alignment
    use_holidays=True,
    use_dst=True,
    weekend_rule=False,
    use_rain=True,
    use_snow=True,
):
    # Remove all existing RunPeriod objects
    for rp in list(idf.idfobjects["RUNPERIOD"]):
        idf.removeidfobject(rp)

    # Create the single desired RunPeriod
    rp = idf.newidfobject("RUNPERIOD")
    rp.Name = ""
    rp.Begin_Month = begin_month
    rp.Begin_Day_of_Month = begin_day
    rp.End_Month = end_month
    rp.End_Day_of_Month = end_day
    rp.Day_of_Week_for_Start_Day = start_dow or ""  # blank -> use EPW’s weekday
    rp.Use_Weather_File_Holidays_and_Special_Days = "Yes" if use_holidays else "No"
    rp.Use_Weather_File_Daylight_Saving_Period = "Yes" if use_dst else "No"
    rp.Apply_Weekend_Holiday_Rule = "Yes" if weekend_rule else "No"
    rp.Use_Weather_File_Rain_Indicators = "Yes" if use_rain else "No"
    rp.Use_Weather_File_Snow_Indicators = "Yes" if use_snow else "No"
    return rp

def disable_sizing_periods(idf):
    """Remove all sizing-related objects so EnergyPlus runs only RunPeriods."""
    for key in ["SIZING:ZONE", "SIZING:SYSTEM", "SIZING:PLANT",
                "SIZINGPERIOD:DESIGNDAY", "SIZINGPERIOD:WEATHERFILEDAYS",
                "SIZINGPERIOD:WEATHERFILENIGHTS"]:
        for obj in list(idf.idfobjects.get(key, [])):
            idf.removeidfobject(obj)


def reset_output_variables(idf,
                           keep_vars=("Site Outdoor Air Drybulb Temperature",
                                      "Zone Mean Air Temperature"),
                           freq="Hourly"):
    """Remove all Output:Variable objects, then add only the ones in keep_vars.
       Uses 'Environment' for Site variables and '*' for Zone variables."""
    # 1) remove all current Output:Variable entries
    for obj in list(idf.idfobjects["OUTPUT:VARIABLE"]):
        idf.removeidfobject(obj)

    # 2) add back the two want
    for var in keep_vars:
        ov = idf.newidfobject("OUTPUT:VARIABLE")
        # Key logic: Site variables -> Environment; Zone variables -> all zones (*)
        if var.strip().lower().startswith("site "):
            ov.Key_Value = "Environment"
        elif var.strip().lower().startswith("zone "):
            ov.Key_Value = "*"
        else:
            # fallback: try wildcard
            ov.Key_Value = "*"
        ov.Variable_Name = var
        ov.Reporting_Frequency = freq
        # ov.Schedule_Name can be left blank for always-on logging


#%%
def ensure_sizing_plant(idf,
                        loop_name="Hot Water Loop",
                        loop_type="Heating",
                        design_exit_c=60.0,
                        delta_t=10.0):
    sp = next((o for o in idf.idfobjects["SIZING:PLANT"]
               if (o.Plant_or_Condenser_Loop_Name or "").lower() == loop_name.lower()), None)
    if not sp:
        sp = idf.newidfobject("SIZING:PLANT")
        sp.Plant_or_Condenser_Loop_Name = loop_name
        sp.Loop_Type = loop_type
        sp.Design_Loop_Exit_Temperature = design_exit_c
        sp.Loop_Design_Temperature_Difference = delta_t
    return sp

#%%
def append_leg_to_splitter_mixer(split_dem, mix_dem, branch_name):
    # splitter outlets
    i = 1
    while getattr(split_dem, f"Outlet_Branch_{i}_Name", ""):
        i += 1
    setattr(split_dem, f"Outlet_Branch_{i}_Name", branch_name)
    # mixer inlets
    j = 1
    while getattr(mix_dem, f"Inlet_Branch_{j}_Name", ""):
        j += 1
    setattr(mix_dem, f"Inlet_Branch_{j}_Name", branch_name)


def attach_demand_side_to_plantloop(idf,
                                    plant_name="Hot Water Loop",
                                    dem_branchlist="Heating Demand Branches",
                                    dem_connectorlist="HW Demand Connectors"):
    pls = [p for p in idf.idfobjects["PLANTLOOP"] if (p.Name or "").lower()==plant_name.lower()]
    if not pls:
        raise RuntimeError(f"PlantLoop '{plant_name}' not found")
    pl = pls[0]
    pl.Demand_Side_Branch_List_Name   = dem_branchlist
    pl.Demand_Side_Connector_List_Name= dem_connectorlist
    return pl


def ensure_demand_connector_list(idf,
                                 splitter_name="HW Demand Splitter",
                                 mixer_name="HW Demand Mixer",
                                 cl_name="HW Demand Connectors"):
    # Connector:Splitter (plant)
    spls = [o for o in idf.idfobjects["CONNECTOR:SPLITTER"] if (o.Name or "").lower()==splitter_name.lower()]
    split_dem = spls[0] if spls else idf.newidfobject("CONNECTOR:SPLITTER", Name=splitter_name)

    # Connector:Mixer (plant)
    mixs = [o for o in idf.idfobjects["CONNECTOR:MIXER"] if (o.Name or "").lower()==mixer_name.lower()]
    mix_dem = mixs[0] if mixs else idf.newidfobject("CONNECTOR:MIXER", Name=mixer_name)

    # ConnectorList (plant)
    cls = [o for o in idf.idfobjects["CONNECTORLIST"] if (o.Name or "").lower()==cl_name.lower()]
    cl = cls[0] if cls else idf.newidfobject("CONNECTORLIST", Name=cl_name)

    # Write entries (order: splitter then mixer)
    cl.Connector_1_Object_Type = "Connector:Splitter"
    cl.Connector_1_Name = split_dem.Name
    cl.Connector_2_Object_Type = "Connector:Mixer"
    cl.Connector_2_Name = mix_dem.Name

    return split_dem, mix_dem, cl

#%%
def make_zone_branch_names(target_rooms):
    """Map room/zone names to the Branch object names used in the loop."""
    return [f"{name} Radiant Branch" for name in target_rooms]

def _branchlist_read_names(bl):
    names = []
    i = 1
    while True:
        fn = f"Branch_{i}_Name"
        val = getattr(bl, fn, "")
        if not val:
            break
        names.append(val)
        i += 1
    return names

def _branchlist_write_compact(bl, names):
    """Write names back compactly and clear any stale trailing entries."""
    # how many were there before?
    prev = _branchlist_read_names(bl)
    # write new sequence
    for i, nm in enumerate(names, start=1):
        setattr(bl, f"Branch_{i}_Name", nm)
    # clear any leftover old fields
    for j in range(len(names) + 1, len(prev) + 1):
        setattr(bl, f"Branch_{j}_Name", "")

def build_demand_branchlist(
    idf,
    target_rooms,  # list of zone display names e.g., ["Block Room_1_F1 Storey 0", ...]
    bl_name="Heating Demand Branches",
    inlet="Reheat Inlet Branch",
    bypass="Reheat Bypass Branch",
    outlet="Reheat Outlet Branch",
):
    """
    Create/overwrite the demand-side BranchList to:
      BranchList,
        Heating Demand Branches,         !- Name
        Reheat Inlet Branch,             !- Branch 1 Name
        <Room_1> Radiant Branch,         !- Branch 2 Name
        <Room_2> Radiant Branch,         !- Branch 3 Name
        ...
        Reheat Bypass Branch,            !- Branch N-1 Name
        Reheat Outlet Branch;            !- Branch N Name
    """
    # find or create BranchList
    bls = [b for b in idf.idfobjects["BRANCHLIST"] if (b.Name or "").lower() == bl_name.lower()]
    bl = bls[0] if bls else idf.newidfobject("BRANCHLIST", Name=bl_name)

    # de-duplicate room branches and keep order
    zone_branches = []
    for nm in make_zone_branch_names(target_rooms):
        if nm and nm not in zone_branches and nm not in (inlet, bypass, outlet):
            zone_branches.append(nm)

    ordered = [inlet] + zone_branches + [bypass, outlet]
    _branchlist_write_compact(bl, ordered)

    return bl

#%%
def _collect_zone_radiant_branches(idf):
    """Return names of BRANCH objects whose 1st component is the ZoneHVAC radiant."""
    names = []
    for br in idf.idfobjects["BRANCH"]:
        ctype = (getattr(br, "Component_1_Object_Type", "") or "").lower()
        if ctype == "zonehvac:lowtemperatureradiant:variableflow":
            names.append(br.Name)
    return names

def finalize_demand_manifold(idf, split_dem, mix_dem, bl_dem):
    """
    Make splitter/mixer and BranchList consistent for demand side:
    - Splitter/Mixer: zone branches + 'Reheat Bypass Branch'
    - BranchList: Inlet, zone branches, Bypass, Outlet (Outlet last!)
    """
    # Find canonical names created earlier
    inlet_name  = "Reheat Inlet Branch"
    bypass_name = "Reheat Bypass Branch"
    outlet_name = "Reheat Outlet Branch"

    zone_branches = _collect_zone_radiant_branches(idf)

    # ---- Wire connectors (clear then repopulate) ----
    # Clear prior lists
    i = 1
    while hasattr(split_dem, f"Outlet_Branch_{i}_Name"):
        setattr(split_dem, f"Outlet_Branch_{i}_Name", "")
        i += 1
    i = 1
    while hasattr(mix_dem, f"Inlet_Branch_{i}_Name"):
        setattr(mix_dem, f"Inlet_Branch_{i}_Name", "")
        i += 1

    # Add all zone branches
    for i, name in enumerate(zone_branches, start=1):
        setattr(split_dem, f"Outlet_Branch_{i}_Name", name)
        setattr(mix_dem,   f"Inlet_Branch_{i}_Name",  name)

    # Add the bypass as the final parallel leg
    setattr(split_dem, f"Outlet_Branch_{len(zone_branches)+1}_Name", bypass_name)
    setattr(mix_dem,   f"Inlet_Branch_{len(zone_branches)+1}_Name",  bypass_name)

    # ---- Rebuild BranchList in required order ----
    # Clear existing Branch_* fields
    j = 1
    while hasattr(bl_dem, f"Branch_{j}_Name"):
        setattr(bl_dem, f"Branch_{j}_Name", "")
        j += 1

    order = [inlet_name] + zone_branches + [bypass_name, outlet_name]
    for j, name in enumerate(order, start=1):
        setattr(bl_dem, f"Branch_{j}_Name", name)

    # Optional sanity check against PlantLoop demand outlet node
    try:
        pl = next(p for p in idf.idfobjects["PLANTLOOP"] if p.Name == "Hot Water Loop")
        last_branch = next(b for b in idf.idfobjects["BRANCH"] if b.Name == outlet_name)
        last_out = getattr(last_branch, "Component_1_Outlet_Node_Name", "")
        if pl.Demand_Side_Outlet_Node_Name != last_out:
            print("⚠️ Demand outlet mismatch:",
                  pl.Demand_Side_Outlet_Node_Name, "!=", last_out)
    except StopIteration:
        pass

#%%
def set_first_existing(obj, candidates, value):
    """Set the first attribute in `candidates` that exists on `obj`."""
    for fname in candidates:
        try:
            setattr(obj, fname, value)
            return fname  # success
        except Exception:
            pass
    # Optional: log which ones failed
    print(f"⚠️ None of {candidates} exist on {getattr(obj, 'key', getattr(obj, 'Name', type(obj)))}")
    return None

#%%
def set_idd(idd_path):
    if not os.path.isfile(idd_path):
        raise FileNotFoundError("IDD not found: " + idd_path)
    IDF.setiddname(idd_path)

def load_idf(path):
    if not os.path.isfile(path):
        raise FileNotFoundError("IDF not found: " + path)
    return IDF(path)

def get_zone_name_variants(idf):
    """
    Build a mapping from friendly label (e.g., 'Room_1') to actual Zone.Name.
    In geometry pipeline, zones look like 'Block Room_1_F1 Storey 0'.
    We normalize by stripping 'Block ' prefix and ' Storey 0' suffix.
    """
    mapping = {}
    for z in idf.idfobjects["ZONE"]:
        base = z.Name.replace("Block ", "").replace(" Storey 0", "")
        mapping[base.lower()] = z.Name
    return mapping

def find_floor_surfaces_for_zone(idf, zone_name):
    """Return a list of BuildingSurface:Detailed names that are floors of this zone."""
    floors = []
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Zone_Name or "") == zone_name:
            st = (s.Surface_Type or "").strip().lower()
            if st == "floor":
                floors.append(s.Name)
    return floors

def ensure_schedule_always_on(idf, sched_name=AVAIL_SCHED_NAME):
    schs = [s for s in idf.idfobjects["SCHEDULE:COMPACT"] if (s.Name or "").lower() == sched_name.lower()]
    if schs:
        return schs[0]
    sch = idf.newidfobject("SCHEDULE:COMPACT")
    sch.Name = sched_name
    sch.Schedule_Type_Limits_Name = AVAIL_SCHED_TYPE
    # Simple always-on discrete schedule
    sch.Field_1 = "Through: 12/31"
    sch.Field_2 = "For: AllDays"
    sch.Field_3 = "Until: 24:00"
    sch.Field_4 = "1"
    # Also ensure the type limits exist
    stl = [t for t in idf.idfobjects["SCHEDULETYPELIMITS"] if (t.Name or "").lower() == AVAIL_SCHED_TYPE.lower()]
    if not stl:
        t = idf.newidfobject("SCHEDULETYPELIMITS")
        t.Name = AVAIL_SCHED_TYPE
        t.Lower_Limit_Value = 0
        t.Upper_Limit_Value = 1
        t.Numeric_Type = "Discrete"
        t.Unit_Type = ""
    return sch

def ensure_hw_setpoint_schedule(idf, sched_name=HW_SETPOINT_SCHED_NAME, setpoint_c=HW_SETPOINT_C):
    s = [x for x in idf.idfobjects["SCHEDULE:COMPACT"] if (x.Name or "").lower() == sched_name.lower()]
    if s:
        return s[0]
    sch = idf.newidfobject("SCHEDULE:COMPACT")
    sch.Name = sched_name
    sch.Schedule_Type_Limits_Name = "Temperature"
    sch.Field_1 = "Through: 12/31"
    sch.Field_2 = "For: AllDays"
    sch.Field_3 = "Until: 24:00"
    sch.Field_4 = str(setpoint_c)
    # Ensure Temperature limit
    stl = [t for t in idf.idfobjects["SCHEDULETYPELIMITS"] if (t.Name or "").lower() == "temperature"]
    if not stl:
        t = idf.newidfobject("SCHEDULETYPELIMITS")
        t.Name = "Temperature"
        t.Numeric_Type = "Continuous"
        t.Unit_Type = "Temperature"
    return sch

def ensure_setpoint_manager_scheduled(idf, loop_outlet_node, sched_name):
    # Create or reuse a NodeList for the loop outlet
    nl_name = f"{PLANT_LOOP_NAME} Setpoint Node List"
    nls = [n for n in idf.idfobjects["NODELIST"] if (n.Name or "").lower() == nl_name.lower()]
    nl = nls[0] if nls else idf.newidfobject("NODELIST", Name=nl_name)
    nl.Node_1_Name = loop_outlet_node

    # Reuse existing SPM if present
    spm_name = f"{PLANT_LOOP_NAME} Setpoint Manager"
    spms = [s for s in idf.idfobjects["SETPOINTMANAGER:SCHEDULED"]
            if (s.Name or "").lower() == spm_name.lower()]
    if spms:
        return spms[0]

    spm = idf.newidfobject("SETPOINTMANAGER:SCHEDULED")
    spm.Name = spm_name
    spm.Control_Variable = "Temperature"
    spm.Schedule_Name = sched_name

    
    chosen = set_first_existing(
        spm,
        ["Setpoint_Node_or_NodeList_Name", "Setpoint_Node_or_Nodelist_Name", "Setpoint_Node_Name"],
        nl_name
    )

    if not chosen:
        # Helpful debug: show available fields if everything failed
        avail = getattr(spm, 'fieldnames', None)
        raise RuntimeError(
            "Could not set SetpointManager:Scheduled target node field. "
            f"Tried NodeList/Node variants; available fields are: {avail}"
        )

    
    return spm

def ensure_plant_loop_with_purchased_heat(idf):
    """
    EnergyPlus 8.9-compatible plant loop
    - SUPPLY pump embedded in the first branch (Heating Supply Inlet Branch), not a separate branch
    - DistrictHeating source
    - Supply/Demand splitters & mixers
    - Sizing:Plant added so autosizing works
    """
    existing = [p for p in idf.idfobjects["PLANTLOOP"]
                if (p.Name or "").lower() == PLANT_LOOP_NAME.lower()]
    if existing:
        # Return the loop and connector objects if already present (no rebuild)
        pl = existing[0]
        # Try to find existing supply/demand connectors/branchlists by name
        bl_sup = next((x for x in idf.idfobjects["BRANCHLIST"] if (x.Name or "").lower() == "heating supply branches"), None)
        bl_dem = next((x for x in idf.idfobjects["BRANCHLIST"] if (x.Name or "").lower() == "heating demand branches"), None)
        split_sup = next((x for x in idf.idfobjects["CONNECTOR:SPLITTER"] if (x.Name or "").lower() == SUPPLY_SPLITTER_NAME.lower()), None)
        mix_sup   = next((x for x in idf.idfobjects["CONNECTOR:MIXER"]   if (x.Name or "").lower() == SUPPLY_MIXER_NAME.lower()), None)
        split_dem = next((x for x in idf.idfobjects["CONNECTOR:SPLITTER"] if (x.Name or "").lower() == DEMAND_SPLITTER_NAME.lower()), None)
        mix_dem   = next((x for x in idf.idfobjects["CONNECTOR:MIXER"]   if (x.Name or "").lower() == DEMAND_MIXER_NAME.lower()), None)
        return pl, bl_sup, bl_dem, split_sup, mix_sup, split_dem, mix_dem

    # ---- PlantEquipmentOperationSchemes + HeatingLoad + List ----
    eql = idf.newidfobject("PLANTEQUIPMENTLIST", Name="Heating Plant")
    eql.Equipment_1_Object_Type = "DistrictHeating"
    eql.Equipment_1_Name = PURCHASED_HEAT_NAME

    pehl = idf.newidfobject("PLANTEQUIPMENTOPERATION:HEATINGLOAD",
                            Name="Purchased Only",
                            Load_Range_1_Lower_Limit=0.0,
                            Load_Range_1_Upper_Limit=1_000_000.0,
                            Range_1_Equipment_List_Name=eql.Name)

    peos = idf.newidfobject("PLANTEQUIPMENTOPERATIONSCHEMES", Name="Hot Loop Operation")
    peos.Control_Scheme_1_Object_Type = "PlantEquipmentOperation:HeatingLoad"
    peos.Control_Scheme_1_Name = pehl.Name
    peos.Control_Scheme_1_Schedule_Name = "ON"
    if not any(s.Name == "ON" for s in idf.idfobjects["SCHEDULE:COMPACT"]):
        sch = idf.newidfobject("SCHEDULE:COMPACT")
        sch.Name = "ON"
        sch.Schedule_Type_Limits_Name = "On/Off"
        sch.Field_1, sch.Field_2, sch.Field_3, sch.Field_4 = "Through: 12/31", "For: AllDays", "Until: 24:00", "1"
        if not any(t.Name == "On/Off" for t in idf.idfobjects["SCHEDULETYPELIMITS"]):
            t = idf.newidfobject("SCHEDULETYPELIMITS")
            t.Name, t.Numeric_Type, t.Lower_Limit_Value, t.Upper_Limit_Value = "On/Off", "Discrete", 0, 1

    # ---- PlantLoop ----
    pl = idf.newidfobject("PLANTLOOP")
    pl.Name = PLANT_LOOP_NAME
    pl.Fluid_Type = "Water"
    pl.User_Defined_Fluid_Type = ""
    pl.Plant_Equipment_Operation_Scheme_Name = peos.Name
    pl.Loop_Temperature_Setpoint_Node_Name = HW_SUP_OUTLET_NODE
    pl.Maximum_Loop_Temperature = 100.0
    pl.Minimum_Loop_Temperature = 10.0
    pl.Maximum_Loop_Flow_Rate = "Autosize"
    pl.Minimum_Loop_Flow_Rate = 0.0
    pl.Plant_Loop_Volume = "Autocalculate"

    pl.Plant_Side_Inlet_Node_Name  = HW_SUP_INLET_NODE
    pl.Plant_Side_Outlet_Node_Name = HW_SUP_OUTLET_NODE
    pl.Demand_Side_Inlet_Node_Name  = HW_DEMAND_INLET_NODE
    pl.Demand_Side_Outlet_Node_Name = HW_DEMAND_OUTLET_NODE

    pl.Plant_Side_Branch_List_Name    = "Heating Supply Branches"
    pl.Plant_Side_Connector_List_Name = "Heating Supply Connectors"
    pl.Demand_Side_Branch_List_Name   = "Heating Demand Branches"
    pl.Demand_Side_Connector_List_Name= "Reheat Connectors"

    # Ensure loop sizing (so autosizing is allowed)
    ensure_sizing_plant(idf, loop_name=PLANT_LOOP_NAME, loop_type="Heating", design_exit_c=60.0, delta_t=10.0)

    # ---- Branch lists ----
    bl_sup = idf.newidfobject("BRANCHLIST", Name="Heating Supply Branches")
    bl_dem = idf.newidfobject("BRANCHLIST", Name="Heating Demand Branches")

    # Supply branches (NO separate pump branch)
    br_in_sup   = idf.newidfobject("BRANCH", Name="Heating Supply Inlet Branch")
    br_phw      = idf.newidfobject("BRANCH", Name="Heating Purchased Hot Water Branch")
    br_bypass_s = idf.newidfobject("BRANCH", Name="Heating Supply Bypass Branch")
    br_out_sup  = idf.newidfobject("BRANCH", Name="Heating Supply Outlet Branch")

    # Order: Inlet → Purchased Heat → Bypass → Outlet
    bl_sup.Branch_1_Name = br_in_sup.Name
    bl_sup.Branch_2_Name = br_phw.Name
    bl_sup.Branch_3_Name = br_bypass_s.Name
    bl_sup.Branch_4_Name = br_out_sup.Name

    # Demand headers (zone branches added later)
    br_in_dem   = idf.newidfobject("BRANCH", Name="Reheat Inlet Branch")
    br_bypass_d = idf.newidfobject("BRANCH", Name="Reheat Bypass Branch")
    br_out_dem  = idf.newidfobject("BRANCH", Name="Reheat Outlet Branch")
    bl_dem.Branch_1_Name, bl_dem.Branch_2_Name, bl_dem.Branch_3_Name = br_in_dem.Name, br_bypass_d.Name, br_out_dem.Name

    # ---- SUPPLY: embed Pump in the first branch ----
    # Create the pump object
    pump = idf.newidfobject(
        "PUMP:VARIABLESPEED",
        Name=SUPPLY_PUMP_NAME,
        Inlet_Node_Name=HW_SUP_INLET_NODE,
        Outlet_Node_Name=HW_PUMP_OUTLET_NODE
    )
    set_first_existing(pump, ["Design_Maximum_Flow_Rate", "Rated_Flow_Rate"], "Autosize")
    set_first_existing(pump, ["Design_Pump_Head", "Rated_Pump_Head"], 179352.0)
    set_first_existing(pump, ["Design_Power_Consumption", "Rated_Power_Consumption", "Design_Electric_Power"], "Autosize")
    set_first_existing(pump, ["Design_Minimum_Flow_Rate", "Minimum_Flow_Rate", "Minimum_Volume_Flow_Rate"], 0.0)
    pump.Motor_Efficiency = 0.9
    pump.Fraction_of_Motor_Inefficiencies_to_Fluid_Stream = 0.0
    for i, v in enumerate([0.0, 1.0, 0.0, 0.0], start=1):
        setattr(pump, f"Coefficient_{i}_of_the_Part_Load_Performance_Curve", v)

    # Put pump directly as Component 1 of the inlet branch
    br_in_sup.Component_1_Object_Type       = "Pump:VariableSpeed"
    br_in_sup.Component_1_Name              = pump.Name
    br_in_sup.Component_1_Inlet_Node_Name   = pump.Inlet_Node_Name        # HW Supply Inlet Node
    br_in_sup.Component_1_Outlet_Node_Name  = pump.Outlet_Node_Name       # HW Pump Outlet Node

    # Purchased heat branch (use unique splitter/mixer nodes on parallel legs)
    purch = idf.newidfobject("DISTRICTHEATING",
                             Name=PURCHASED_HEAT_NAME,
                             Hot_Water_Inlet_Node_Name="HW Supply Splitter Outlet 1 Node",
                             Hot_Water_Outlet_Node_Name="HW Supply Mixer Inlet 1 Node",
                             Nominal_Capacity=1_000_000.0)
    br_phw.Component_1_Object_Type          = "DistrictHeating"
    br_phw.Component_1_Name                 = purch.Name
    br_phw.Component_1_Inlet_Node_Name      = purch.Hot_Water_Inlet_Node_Name
    br_phw.Component_1_Outlet_Node_Name     = purch.Hot_Water_Outlet_Node_Name

    # Bypass branch (unique nodes)
    byps = idf.newidfobject("PIPE:ADIABATIC", Name="Heating Supply Bypass",
                            Inlet_Node_Name="HW Supply Splitter Outlet 2 Node",
                            Outlet_Node_Name="HW Supply Mixer Inlet 2 Node")
    br_bypass_s.Component_1_Object_Type     = "Pipe:Adiabatic"
    br_bypass_s.Component_1_Name            = byps.Name
    br_bypass_s.Component_1_Inlet_Node_Name = byps.Inlet_Node_Name
    br_bypass_s.Component_1_Outlet_Node_Name= byps.Outlet_Node_Name

    # Outlet branch
    out_pipe_s = idf.newidfobject("PIPE:ADIABATIC", Name="Heating Supply Outlet Pipe",
                                  Inlet_Node_Name="HW Supply Mixer Outlet Node",
                                  Outlet_Node_Name=HW_SUP_OUTLET_NODE)
    br_out_sup.Component_1_Object_Type      = "Pipe:Adiabatic"
    br_out_sup.Component_1_Name             = out_pipe_s.Name
    br_out_sup.Component_1_Inlet_Node_Name  = out_pipe_s.Inlet_Node_Name
    br_out_sup.Component_1_Outlet_Node_Name = out_pipe_s.Outlet_Node_Name

    # ---- SUPPLY connectors (split/mix by branch names) ----
    cl_sup = idf.newidfobject("CONNECTORLIST", Name="Heating Supply Connectors")
    cl_sup.Connector_1_Object_Type = "Connector:Splitter"
    cl_sup.Connector_1_Name = SUPPLY_SPLITTER_NAME
    cl_sup.Connector_2_Object_Type = "Connector:Mixer"
    cl_sup.Connector_2_Name = SUPPLY_MIXER_NAME

    split_sup = idf.newidfobject("CONNECTOR:SPLITTER", Name=SUPPLY_SPLITTER_NAME)
    split_sup.Inlet_Branch_Name      = br_in_sup.Name
    split_sup.Outlet_Branch_1_Name   = br_phw.Name
    split_sup.Outlet_Branch_2_Name   = br_bypass_s.Name

    mix_sup = idf.newidfobject("CONNECTOR:MIXER", Name=SUPPLY_MIXER_NAME)
    mix_sup.Outlet_Branch_Name       = br_out_sup.Name
    mix_sup.Inlet_Branch_1_Name      = br_phw.Name
    mix_sup.Inlet_Branch_2_Name      = br_bypass_s.Name

    # ---- DEMAND headers & connectors ----
    in_pipe_d = idf.newidfobject("PIPE:ADIABATIC", Name="Reheat Inlet Pipe",
                                 Inlet_Node_Name=HW_DEMAND_INLET_NODE,
                                 Outlet_Node_Name="Reheat Inlet Pipe Outlet Node")
    br_in_dem.Component_1_Object_Type       = "Pipe:Adiabatic"
    br_in_dem.Component_1_Name              = in_pipe_d.Name
    br_in_dem.Component_1_Inlet_Node_Name   = in_pipe_d.Inlet_Node_Name
    br_in_dem.Component_1_Outlet_Node_Name  = in_pipe_d.Outlet_Node_Name

    bypd = idf.newidfobject("PIPE:ADIABATIC", Name="Reheat Bypass",
                            Inlet_Node_Name="Reheat Splitter Outlet Node",
                            Outlet_Node_Name="Reheat Mixer Inlet Bypass Node")
    br_bypass_d.Component_1_Object_Type     = "Pipe:Adiabatic"
    br_bypass_d.Component_1_Name            = bypd.Name
    br_bypass_d.Component_1_Inlet_Node_Name = bypd.Inlet_Node_Name
    br_bypass_d.Component_1_Outlet_Node_Name= bypd.Outlet_Node_Name

    out_pipe_d = idf.newidfobject("PIPE:ADIABATIC", Name="Reheat Outlet Pipe",
                                  Inlet_Node_Name="Reheat Mixer Outlet Node",
                                  Outlet_Node_Name=HW_DEMAND_OUTLET_NODE)
    br_out_dem.Component_1_Object_Type      = "Pipe:Adiabatic"
    br_out_dem.Component_1_Name             = out_pipe_d.Name
    br_out_dem.Component_1_Inlet_Node_Name  = out_pipe_d.Inlet_Node_Name
    br_out_dem.Component_1_Outlet_Node_Name = out_pipe_d.Outlet_Node_Name

    cl_dem = idf.newidfobject("CONNECTORLIST", Name="Reheat Connectors")
    cl_dem.Connector_1_Object_Type = "Connector:Splitter"
    cl_dem.Connector_1_Name = DEMAND_SPLITTER_NAME
    cl_dem.Connector_2_Object_Type = "Connector:Mixer"
    cl_dem.Connector_2_Name = DEMAND_MIXER_NAME

    split_dem = idf.newidfobject("CONNECTOR:SPLITTER", Name=DEMAND_SPLITTER_NAME)
    split_dem.Inlet_Branch_Name = "Reheat Inlet Branch"

    mix_dem = idf.newidfobject("CONNECTOR:MIXER", Name=DEMAND_MIXER_NAME)
    mix_dem.Outlet_Branch_Name = "Reheat Outlet Branch"

    # Seed bypass as a valid parallel leg
    split_dem.Outlet_Branch_1_Name = "Reheat Bypass Branch"
    mix_dem. Inlet_Branch_1_Name   = "Reheat Bypass Branch"

    # Setpoint manager on supply outlet
    ensure_hw_setpoint_schedule(idf, HW_SETPOINT_SCHED_NAME, HW_SETPOINT_C)
    ensure_setpoint_manager_scheduled(idf, HW_SUP_OUTLET_NODE, HW_SETPOINT_SCHED_NAME)

    return pl, bl_sup, bl_dem, split_sup, mix_sup, split_dem, mix_dem


def ensure_zone_radiant_variableflow(idf, zone_name, floor_surface_names,
                                     avail_sched=AVAIL_SCHED_NAME,
                                     ctrl_type="MeanAirTemperature",
                                     heat_sp_sched="Radiant Heating Setpoints",
                                     cool_sp_sched="Radiant Cooling Setpoints"):
    """
    Create ZoneHVAC:LowTemperatureRadiant:VariableFlow (heating & cooling fields present,
    may leave cooling nodes unused for now). Fields match E+ 8.9 sample.
    """
    ensure_schedule_always_on(idf, avail_sched)

    # simple setpoint schedules if missing
    if not any(s.Name == heat_sp_sched for s in idf.idfobjects["SCHEDULE:COMPACT"]):
        sch = idf.newidfobject("SCHEDULE:COMPACT")
        sch.Name = heat_sp_sched
        sch.Schedule_Type_Limits_Name = "Temperature"
        sch.Field_1 = "Through: 12/31"
        sch.Field_2 = "For: AllDays"
        sch.Field_3 = "Until: 24:00"
        sch.Field_4 = "22"

    if not any(s.Name == cool_sp_sched for s in idf.idfobjects["SCHEDULE:COMPACT"]):
        sch = idf.newidfobject("SCHEDULE:COMPACT")
        sch.Name = cool_sp_sched
        sch.Schedule_Type_Limits_Name = "Temperature"
        sch.Field_1 = "Through: 12/31"
        sch.Field_2 = "For: AllDays"
        sch.Field_3 = "Until: 24:00"
        sch.Field_4 = "26"

    # Make a Radiant Surface Group from the zone's floors
    # (v8.9 accepts either a single surface name or a group name)
    if not floor_surface_names:
        raise RuntimeError(f"No floor surfaces found for zone {zone_name}")

    obj = idf.newidfobject("ZONEHVAC:LOWTEMPERATURERADIANT:VARIABLEFLOW")
    obj.Name = f"Radiant Floor - {zone_name}"
    obj.Availability_Schedule_Name = avail_sched
    obj.Zone_Name = zone_name
    
    # v8.9: pass a single surface name (no SURFACELIST object)
    obj.Surface_Name_or_Radiant_Surface_Group_Name = floor_surface_names[0]
    
    obj.Hydronic_Tubing_Inside_Diameter = 0.013
    obj.Hydronic_Tubing_Length = "Autosize"
    obj.Temperature_Control_Type = ctrl_type  # e.g., "MeanAirTemperature"

    # Heating side
    obj.Heating_Design_Capacity_Method = "HeatingDesignCapacity"
    obj.Heating_Design_Capacity = "Autosize"
    obj.Heating_Design_Capacity_Per_Floor_Area = ""
    obj.Fraction_of_Autosized_Heating_Design_Capacity = ""
    obj.Maximum_Hot_Water_Flow = "Autosize"
    obj.Heating_Water_Inlet_Node_Name = f"{zone_name} Radiant Water Inlet Node"
    obj.Heating_Water_Outlet_Node_Name = f"{zone_name} Radiant Water Outlet Node"
    obj.Heating_Control_Throttling_Range = 2.0
    obj.Heating_Control_Temperature_Schedule_Name = heat_sp_sched

    # Cooling side (not used now, but fields must exist)
    obj.Cooling_Design_Capacity_Method = "CoolingDesignCapacity"
    obj.Cooling_Design_Capacity = "Autosize"
    obj.Cooling_Design_Capacity_Per_Floor_Area = ""
    obj.Fraction_of_Autosized_Cooling_Design_Capacity = ""
    obj.Maximum_Cold_Water_Flow = "Autosize"
    obj.Cooling_Water_Inlet_Node_Name = f"{zone_name} Cooling Water Inlet Node"
    obj.Cooling_Water_Outlet_Node_Name = f"{zone_name} Cooling Water Outlet Node"
    obj.Cooling_Control_Throttling_Range = 2.0
    obj.Cooling_Control_Temperature_Schedule_Name = cool_sp_sched

    obj.Condensation_Control_Type = "Off"
    obj.Condensation_Control_Dewpoint_Offset = 1.0
    obj.Number_of_Circuits = "OnePerSurface"
    obj.Circuit_Length = ""

    return obj

def add_zone_branch_to_demand_manifold(idf, zone_name, split_dem, mix_dem, bl_dem):
    """
    Create a demand branch that *directly* references the ZoneHVAC:LowTemperatureRadiant:VariableFlow
    as Component_1 (matching the sample IDF pattern).
    """
    br_name = f"{zone_name} Radiant Branch"
    br = idf.newidfobject("BRANCH", Name=br_name)

    in_node  = f"{zone_name} Radiant Water Inlet Node"
    out_node = f"{zone_name} Radiant Water Outlet Node"

    br.Component_1_Object_Type = "ZoneHVAC:LowTemperatureRadiant:VariableFlow"
    br.Component_1_Name = f"Radiant Floor - {zone_name}"
    br.Component_1_Inlet_Node_Name = in_node
    br.Component_1_Outlet_Node_Name = out_node

    # Connect to splitter (as additional outlet)
    i = 1
    while True:
        fn = f"Outlet_Branch_{i}_Name"
        if not getattr(split_dem, fn, ""):
            setattr(split_dem, fn, br.Name)
            break
        i += 1

    # Connect to mixer (as additional inlet)
    i = 1
    while True:
        fn = f"Inlet_Branch_{i}_Name"
        if not getattr(mix_dem, fn, ""):
            setattr(mix_dem, fn, br.Name)
            break
        i += 1

    
    return br


#%%
def apply_rfh(idf_in: str, idf_out: str, target_rooms: list,
              idd_path: str | None = None) -> tuple[str, dict]:
    """Add hydronic RFH to the requested rooms.
 
    Resolution (R1..R4) happens exactly once, before any model edit. Every
    downstream step - branch list, sizing objects, thermostats - is keyed on
    the SAME list of zones that actually received a radiant device.
 
    Returns
    -------
    (absolute_output_path, report)
        report keys: requested, applied, unresolved, ambiguous,
        no_floor_surfaces, thermostat_skipped
    """
    set_idd(idd_path or IDD_PATH)
    idf = load_idf(idf_in)
 
    # ---- single resolution pass (R1..R4) --------------------------------
    zone_map = build_canonical_zone_map(z.Name for z in idf.idfobjects["ZONE"])
    resolutions = resolve_all(target_rooms or [], zone_map)
 
    report = {
        "requested": list(target_rooms or []),
        "applied": [],
        "unresolved": [r.label for r in resolutions if r.status == "unresolved"],
        "ambiguous": [
            {"label": r.label, "candidates": r.candidates}
            for r in resolutions if r.status == "ambiguous"
        ],
        "no_floor_surfaces": [],
        "thermostat_skipped": [],
    }
 
    # ---- plant loop & setpoints -----------------------------------------
    pl, bl_sup, bl_dem, split_sup, mix_sup, split_dem, mix_dem = \
        ensure_plant_loop_with_purchased_heat(idf)
    ensure_hw_setpoint_schedule(idf, HW_SETPOINT_SCHED_NAME, HW_SETPOINT_C)
    ensure_setpoint_manager_scheduled(idf, HW_SUP_OUTLET_NODE, HW_SETPOINT_SCHED_NAME)
 
    cis_name = "Slab Floor with Radiant"
    ensure_internal_source_construction_from_sample(idf, cis_name=cis_name)
 
    # ---- per-zone RFH ----------------------------------------------------
    applied_zone_names = []
 
    for r in resolutions:
        if r.status != "resolved":
            continue
        zname = r.zone_name
 
        floors = find_floor_surfaces_for_zone(idf, zname)
        if not floors:
            report["no_floor_surfaces"].append(zname)
            continue
 
        replace_zone_floor_construction(idf, zname, cis_name)
 
        zrfh = ensure_zone_radiant_variableflow(
            idf, zname, floors,
            avail_sched=AVAIL_SCHED_NAME,
            ctrl_type="MeanAirTemperature",
            heat_sp_sched="Radiant Heating Setpoints",
            cool_sp_sched="Radiant Cooling Setpoints",
        )
 
        add_zone_branch_to_demand_manifold(idf, zname, split_dem, mix_dem, bl_dem)
        link_radiant_to_zone_equipment(idf, zname, zrfh.Name)
 
        applied_zone_names.append(zname)
        report["applied"].append({"label": r.label, "zone": zname, "rule": r.rule})
 
    if not applied_zone_names:
        raise RuntimeError(
            "No valid target zones were matched; nothing to modify. "
            f"Requested: {report['requested']}. "
            f"Unresolved: {report['unresolved']}. "
            f"Ambiguous: {[a['label'] for a in report['ambiguous']]}."
        )
 
    # ---- everything downstream uses applied_zone_names only --------------
    build_demand_branchlist(idf, applied_zone_names)
 
    reset_output_variables(
        idf,
        keep_vars=("Site Outdoor Air Drybulb Temperature", "Zone Mean Air Temperature"),
        freq="Hourly",
    )
    reset_runperiod(
        idf,
        begin_month=1, begin_day=1,
        end_month=12, end_day=31,
        start_dow="Tuesday",
        use_holidays=True, use_dst=True,
        weekend_rule=False, use_rain=True, use_snow=True,
    )
    set_simulation_control_to_runperiod_only(idf)
    Add_output_Heating_Demand(
        idf,
        key_value="*",
        variable_name="Plant Supply Side Heating Demand Rate",
        reporting_frequency="Hourly",
    )
 
    force_radiant_heating_only(idf)
    add_zone_sizing_objects(idf, applied_zone_names)
    enable_zone_sizing(idf)
    add_design_days_seoul(idf)
    force_add_fixed_fragments(idf)
 
    report["thermostat_skipped"] = fast_add_zone_thermostats_for_zones(
        idf, applied_zone_names
    )
 
    os.makedirs(os.path.dirname(os.path.abspath(idf_out)), exist_ok=True)
    idf.saveas(idf_out)
    return os.path.abspath(idf_out), report
 



