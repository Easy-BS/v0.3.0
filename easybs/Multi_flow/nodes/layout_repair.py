# -*- coding: utf-8 -*-
"""
./Multi_flow/nodes/layout_repair.py

Diagnostic-driven extraction repair.

When the layout solver reports blocking diagnostics, the cause is almost always
a semantic extraction fault rather than a genuinely ambiguous description: the
language model omitted a relation, mislabelled one, or converted a phrase of the
wrong type. Observed faults include omitted alignment relations, alignment
relations emitted on an axis incompatible with the stated adjacency, and a
"sharing X's entire east wall" phrase converted into an alignment while the
adjacency it actually stated was dropped.

This node feeds the diagnostics back to the model together with the original
user text and the layout it produced, and asks for a corrected extraction. The
diagnostics name the specific rooms and relations at fault, so the model
receives a targeted instruction rather than a repeat of the original prompt.

Design notes
------------
* The solver itself stays free of any LLM dependency. This node calls the
  solver; the solver never calls a model.
* The loop runs inside this node, so the LangGraph edges stay linear and no
  conditional routing is required.
* Attempts are bounded. On failure the ORIGINAL diagnostics are preserved, so
  the user sees the real problem rather than an artefact of the last retry.
* D6_OPENING is non-blocking, matching the convention used elsewhere.

@author: Xiguan Liang @SKKU
"""

import os
import sys
import json
import copy
from typing import Any, Dict, List

from state_schema import SimulationState
from nodes.layout_solver import solve_layout
from nodes.building_data_extractor import _call_openai_chat_json, _norm_layout


MAX_ATTEMPTS = int(os.environ.get("EASYBS_REPAIR_ATTEMPTS", "2"))


REPAIR_PROMPT = """You previously converted a building description into a
relational layout, but the deterministic layout solver could not resolve it.
Correct the layout and return it.

ORIGINAL USER DESCRIPTION:
\"\"\"{user_text}\"\"\"

THE LAYOUT YOU PRODUCED:
{layout}

WHAT THE SOLVER REPORTED:
{diagnostics}

How to fix it:
- Re-read the original description sentence by sentence. Every sentence that
  positions a room must produce exactly one "adjacent" relation.
- Every phrase of the form "with their X walls flush" must produce exactly one
  "align" relation on edge X. Count these phrases in the text and make sure you
  produced the same number of align relations.
- A phrase of the form "sharing X's entire north wall" describes the CONTACT
  wall between two rooms. It produces an "adjacent" relation and NO align.
  Do not let such a phrase replace the adjacency it belongs to.
- An "align" or "offset" edge must be PERPENDICULAR to the adjacency direction
  of the same room pair. If B is north or south of A, the edge can only be E or
  W. If B is east or west of A, the edge can only be N or S.
- If a diagnostic says two rooms have no stated alignment, add the align
  relation the original sentence describes. Do not invent one that the text
  does not state.
- Do not compute or invent any x/y coordinates.

Return the corrected layout as STRICT JSON with exactly this shape, and nothing
else (no markdown, no commentary, no code fences):

{{"footprint": {{"width": float, "depth": float}},
  "zones": {{"RoomName": {{"width": float, "depth": float}}}},
  "relations": [ ... ]}}"""


def _blocking(diags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [d for d in (diags or []) if d.get("code") != "D6_OPENING"]


def _log(msg: str) -> None:
    print("[REPAIR] %s" % msg, file=sys.stderr, flush=True)


def repair_layout(state: SimulationState) -> SimulationState:
    """Retry extraction using solver diagnostics as feedback."""

    parsed = state.get("parsed_building_data") or {}

    # Nothing to repair: coordinate input, or the layout already resolved.
    if parsed.get("geometry_source") == "coordinates":
        return state
    if not _blocking(state.get("layout_diagnostics")):
        return state

    user_text = state.get("user_input") or ""
    if not user_text.strip():
        return state

    original_diags = copy.deepcopy(state.get("layout_diagnostics") or [])
    original_layout = copy.deepcopy(parsed.get("layout") or {})
    current_layout = original_layout
    model = state.get("model") or "gpt-4o-mini"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        diags = _blocking(state.get("layout_diagnostics"))
        _log("attempt %d/%d, %d blocking diagnostic(s): %s" % (
            attempt, MAX_ATTEMPTS, len(diags), [d["code"] for d in diags]))

        prompt = REPAIR_PROMPT.format(
            user_text=user_text,
            layout=json.dumps(current_layout, ensure_ascii=False, indent=2),
            diagnostics="\n".join("- " + d.get("message", "") for d in diags),
        )

        try:
            raw = _call_openai_chat_json(
                messages=[{"role": "user", "content": prompt}],
                model=model, temperature=0.0)
        except Exception as e:                       # network, JSON, API error
            _log("call failed (%s); keeping original diagnostics" % e)
            break

        repaired = _norm_layout(raw if isinstance(raw, dict) else {})
        if not repaired.get("zones"):
            _log("returned no usable zones; keeping original diagnostics")
            break

        _log("returned %d relations (was %d)" % (
            len(repaired.get("relations") or []),
            len(current_layout.get("relations") or [])))

        trial = dict(parsed)
        trial["layout"] = repaired
        trial.pop("rooms", None)
        trial_state: SimulationState = dict(state)
        trial_state["parsed_building_data"] = trial
        trial_state["errors"] = [e for e in (state.get("errors") or [])
                                 if e not in [d.get("message")
                                              for d in original_diags]]

        trial_state = solve_layout(trial_state)
        new_diags = _blocking(trial_state.get("layout_diagnostics"))

        if not new_diags:
            rooms = (trial_state.get("parsed_building_data") or {}).get("rooms") or {}
            _log("resolved on attempt %d: %d rooms" % (attempt, len(rooms)))
            trial_state["layout_repair_attempts"] = attempt
            return trial_state

        _log("attempt %d still unresolved: %s" % (
            attempt, [d["code"] for d in new_diags]))
        current_layout = repaired
        state = trial_state

    # Exhausted. Report the ORIGINAL diagnostics, which describe the user's
    # actual description, rather than whatever the last retry produced.
    _log("giving up after %d attempt(s); reporting original diagnostics"
         % MAX_ATTEMPTS)
    parsed["layout"] = original_layout
    state["parsed_building_data"] = parsed
    state["layout_diagnostics"] = original_diags
    state["layout_repair_attempts"] = MAX_ATTEMPTS
    blocking = _blocking(original_diags)
    state["errors"] = (state.get("errors") or []) + \
        [d["message"] for d in blocking
         if d["message"] not in (state.get("errors") or [])]
    return state
