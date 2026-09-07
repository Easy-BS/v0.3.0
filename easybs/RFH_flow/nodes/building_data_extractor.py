# -*- coding: utf-8 -*-
"""
Created on Mon Nov 10 08:25:55 2025

@author: user
"""

# ./RFH_flow/nodes/building_data_extractor.py
import os, re
from typing import List
from state_schema import SimulationState

RX_QUOTED = re.compile(r'"([^"]+)"')
RX_AFTER_COLON = re.compile(r":\s*(.+)$", re.S | re.I)

def _parse_targets(text: str) -> List[str]:
    # 1) if quoted list present: "Room_1", "Living_1", ...
    quoted = RX_QUOTED.findall(text)
    if quoted:
        return [q.strip() for q in quoted if q.strip()]
    # 2) else everything after the colon → split by comma
    m = RX_AFTER_COLON.search(text)
    if m:
        raw = m.group(1)
        parts = [p.strip(" \t\r\n,.;’\"“”") for p in raw.split(",")]
        parts = [p for p in parts if p]
        return parts
    return []

def extract_rfh_targets(state: SimulationState) -> SimulationState:    
    #  If LLM already provided targets, keep them
    if state.get("rfh_targets"):
        targets = state["rfh_targets"]
    else:
        txt = state.get("user_input") or ""
        targets = _parse_targets(txt)
    
    if not targets:
        return {"errors": ["No RFH target rooms found. Provide names like \"Room_1\", \"Living_1\"."]}


    # Choose the input IDF to modify
    idf_in = state.get("idf_path") or state.get("source_idf")
    if not idf_in:
        # DEFAULT: last multi-zone geometry result
        idf_in = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                               "generated_idfs", "geom_multizone_modified.idf"))

    if not os.path.isfile(idf_in):
        return {"errors": [f"Input IDF not found: {idf_in}. Build a multi-zone model first."]}

    state["rfh_targets"] = targets
    state["idf_path"] = idf_in
    return state
