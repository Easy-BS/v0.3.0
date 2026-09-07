# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 08:30:09 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/state_schema.py

from typing import TypedDict, Optional, List, Dict, Any, Literal

class SimulationState(TypedDict, total=False):
    user_input: str

    # file IO
    idf_path: str
    epw_path: str
    output_dir: str
    idd_path: str

    # RFH targets
    rfh_targets: List[str]

    # --- Agent fields (NEW) ---
    intent: Literal["add_rfh", "unknown", "ask_clarification"]
    clarification_question: str
    agent_json: Dict[str, Any]
    model: str  # optional: LLM model name

    # misc + errors
    message: str
    errors: Optional[List[str]]


