# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 13:43:47 2025

@author: Xiguan Liang @SKKU
"""

# langgraph_flow/graph_config.py

from langgraph.graph import StateGraph
from state_schema import SimulationState

# Node functions will be imported here
from nodes.user_query_parser import parse_user_query
from nodes.building_data_extractor import extract_building_geometry
from nodes.layout_solver import solve_layout
from nodes.layout_repair import repair_layout                      
from nodes.geomeppy_generator import generate_idf_file
from nodes.energyplus_defi_output import idf_defi_output
from nodes.energyplus_runner import energyplus_runner
#from nodes.simulation_summarizer import summarize_simulation_output


def define_graph():
    # Create the LangGraph
    builder = StateGraph(SimulationState)

    # Add nodes
    builder.add_node("user_query_parser", parse_user_query)
    builder.add_node("building_data_extractor", extract_building_geometry)
    builder.add_node("layout_solver", solve_layout)
    builder.add_node("layout_repair", repair_layout)               # <-- added
    builder.add_node("geomeppy_generator", generate_idf_file)
    builder.add_node("energyplus_defi_output", idf_defi_output)
    builder.add_node("energyplus_runner", energyplus_runner)

    # Define flow. layout_repair is a no-op when the layout already resolved,
    # so the graph stays linear and no conditional routing is needed.
    builder.set_entry_point("user_query_parser")
    builder.add_edge("user_query_parser", "building_data_extractor")
    builder.add_edge("building_data_extractor", "layout_solver")
    builder.add_edge("layout_solver", "layout_repair")             # <-- changed
    builder.add_edge("layout_repair", "geomeppy_generator")        # <-- added
    builder.add_edge("geomeppy_generator", "energyplus_defi_output")
    builder.add_edge("energyplus_defi_output", "energyplus_runner")

    # Final output
    builder.set_finish_point("energyplus_runner")

    # Compile the graph
    return builder.compile()
