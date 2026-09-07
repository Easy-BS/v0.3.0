# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 08:28:09 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/nodes/rfh_adder.py

import os

from state_schema import SimulationState
from .rfh_lib import apply_rfh


def rfh_adder(state: SimulationState) -> SimulationState:
    idf_in = state.get("idf_path")
    targets = state.get("rfh_targets") or []

    if not idf_in or not os.path.isfile(idf_in):
        return {"errors": [f"Input IDF not found: {idf_in}"]}

    if not targets:
        return {"errors": ["No RFH targets provided."]}

    out_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "generated_idfs")
    )
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(idf_in))[0]
    idf_out = os.path.join(out_dir, f"{base}_RFH.idf")

    strict = bool(state.get("strict_targets", True))

    try:
        final_path, report = apply_rfh(idf_in, idf_out, targets, state.get("idd_path"))
    except RuntimeError as e:
        return {"errors": [str(e)]}

    final_path = os.path.abspath(final_path or "")
    if not os.path.isfile(final_path):
        return {
            "errors": [
                "RFH pipeline finished but output file was NOT created.",
                f"Expected output: {final_path}",
                f"Input IDF: {os.path.abspath(idf_in)}",
                f"Output folder: {out_dir}",
            ],
            "rfh_report": report,
        }

    n_req = len(report["requested"])
    n_app = len(report["applied"])

    unresolved = report["unresolved"]
    ambiguous = [a["label"] for a in report["ambiguous"]]
    no_floor = report["no_floor_surfaces"]
    tstat_skipped = report.get("thermostat_skipped") or []

    problems = bool(unresolved or ambiguous or no_floor or tstat_skipped)

    if strict and problems:
        errors = [f"RFH applied to {n_app} of {n_req} requested zones."]
        if unresolved:
            errors.append("Unresolved room names: " + ", ".join(unresolved))
        if ambiguous:
            errors.append("Ambiguous room names: " + ", ".join(ambiguous))
        if no_floor:
            errors.append("Zones without floor surfaces: " + ", ".join(no_floor))
        if tstat_skipped:
            errors.append("Thermostats skipped: " + ", ".join(tstat_skipped))
        return {
            "errors": errors,
            "rfh_report": report,
            "idf_path": final_path,
        }

    state["idf_path"] = final_path
    state["rfh_report"] = report
    state["message"] = (
        f"RFH added to {n_app} of {n_req} requested zones. Output: {final_path}"
    )
    return state