# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 10:31:18 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/graph_config.py

from langgraph.graph import StateGraph, END
from state_schema import SimulationState

from nodes.user_query_parser import user_query_parser
from nodes.llm_router import llm_router
from nodes.building_data_extractor import extract_rfh_targets
from nodes.rfh_adder import rfh_adder
from nodes.energyplus_runner import energyplus_runner

def _route(state: SimulationState) -> str:
    intent = state.get("intent", "unknown")
    if intent == "add_rfh":
        # we already have rfh_targets from the LLM router, but we keep extractor as a guard
        return "building_data_extractor"
    if intent == "ask_clarification":
        return "clarify"
    return "unknown"

def clarify_node(state: SimulationState) -> SimulationState:
    q = state.get("clarification_question") or \
        "Which rooms should receive RFH? Please list them (e.g., \"Room_1\", \"Living_2\")."
    state["message"] = q
    return state

def unknown_node(state: SimulationState) -> SimulationState:
    state["message"] = "I can add RFH if you tell me the target rooms. Example: Add RFH to \"Room_1\", \"Living_2\"."
    return state

def define_graph():
    g = StateGraph(SimulationState)

    g.add_node("user_query_parser", user_query_parser)
    g.add_node("llm_router", llm_router)
    g.add_node("building_data_extractor", extract_rfh_targets)
    g.add_node("rfh_adder", rfh_adder)
    g.add_node("energyplus_runner", energyplus_runner)
    g.add_node("clarify", clarify_node)
    g.add_node("unknown", unknown_node)

    g.set_entry_point("user_query_parser")
    g.add_edge("user_query_parser", "llm_router")

    g.add_conditional_edges(
        "llm_router",
        _route,
        {
            "building_data_extractor": "building_data_extractor",
            "clarify": "clarify",
            "unknown": "unknown",
        },
    )

    g.add_edge("building_data_extractor", "rfh_adder")
    g.add_edge("rfh_adder", "energyplus_runner")
    g.add_edge("energyplus_runner", END)

    g.add_edge("clarify", END)
    g.add_edge("unknown", END)

    return g.compile()

