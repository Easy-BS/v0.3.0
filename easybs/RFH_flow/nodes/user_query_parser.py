# -*- coding: utf-8 -*-
"""
Created on Mon Nov 10 07:09:03 2025

@author: Xiguan Liang @SKKU
"""
#./RFH_flow/nodes/user_query_parser.py

from __future__ import annotations

from state_schema import SimulationState

try:
    from .text_normalize import normalize_input, validate_input_text
except ImportError:  # standalone execution
    from text_normalize import normalize_input, validate_input_text


def user_query_parser(state: SimulationState) -> SimulationState:
    raw = state.get("user_input")

    normalized, applied_rules = normalize_input(raw)
    outcome, code, message = validate_input_text(normalized)

    if outcome == "reject":
        return {
            "errors": [f"[{code}] {message}"],
            "normalization_report": {
                "raw_length": len(raw) if isinstance(raw, str) else 0,
                "normalized_length": len(normalized),
                "rules_applied": applied_rules,
                "outcome": "reject",
                "reason": code,
            },
        }

    return {
        "user_input": normalized,
        "normalization_report": {
            "raw_length": len(raw) if isinstance(raw, str) else 0,
            "normalized_length": len(normalized),
            "rules_applied": applied_rules,
            "outcome": "accept",
            "reason": None,
        },
    }


# Backward-compatible alias used by Multi_flow / nodes
parse_user_query = user_query_parser