# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:03:50 2025

@author: user
"""

# user_query_parser.py

from typing import Dict
from state_schema import SimulationState

def parse_user_query(state: SimulationState) -> SimulationState:
    """
    Capture and store the user's original input in the simulation state.
    This is a simple pass-through node to record the user prompt for future steps.
    """
    if "user_input" not in state or not state["user_input"]:
        return {
            "errors": ["No user input found. Please provide a building description."]
        }

    # Log the user input (optional: print or write to a log file)
    print(f"[LangGraph] User input received: {state['user_input']}")

    # Pass the input forward
    return {"user_input": state["user_input"]}
