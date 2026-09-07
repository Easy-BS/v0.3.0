# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 12:28:38 2025

@author: user
"""

# langgraph_flow/state_schema.py

from typing import TypedDict, Optional, Dict, List


class SimulationState(TypedDict, total=False):
    user_input: str                          # Original user question
    parsed_building_data: Dict               # Geometry and features parsed from user input
    simulation_params: Dict                  # Derived values: schedules, HVAC, weather
    idf_path: str                            # Path to generated IDF file
    epw_path: str                            # Path to weather file
    simulation_output: Dict                  # EnergyPlus output results
    summary: str                             # Natural language summary of results
    errors: Optional[List[str]]              # Any error messages to surface


