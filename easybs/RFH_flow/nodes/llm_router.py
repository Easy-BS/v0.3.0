# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 08:28:09 2025

@author: Xiguan Liang @SKKU
"""

# ./RFH_flow/nodes/llm_router.py

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from state_schema import SimulationState

try:
    from .text_normalize import (
        build_canonical_zone_map,
        clean_targets,
        resolve_all,
        validate_agent_json,
    )
except ImportError:  # standalone execution
    from text_normalize import (
        build_canonical_zone_map,
        clean_targets,
        resolve_all,
        validate_agent_json,
    )

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 2

SYSTEM_PROMPT = """You are an AI agent for building simulation.
Your job: read the user's text and output STRICT JSON only (no markdown).

Schema:
{
  "intent": "add_rfh" | "ask_clarification" | "unknown",
  "rfh_targets": string[],
  "clarification_question": string
}

Rules:
- If user asks to add/install radiant floor heating in specific rooms -> intent="add_rfh" and list rfh_targets.
- If rooms are missing or ambiguous -> intent="ask_clarification" and write a short question asking for room names exactly as in the building.
- If unrelated -> intent="unknown".
Return JSON only.
"""


def _strip_code_fence(text: str) -> str:
    """Defensive: remove ```json fences if the model emits them despite
    the instruction. Counts as validation rule V3a."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _openai_chat_json(user_text: str, model: str) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment.")

    payload = {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},   # server-side JSON enforcement
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    }

    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    parsed = json.loads(_strip_code_fence(content))
    if isinstance(parsed, dict):
        parsed["_usage"] = usage
    return parsed


def _load_zone_map(idf_path: Optional[str], idd_path: Optional[str]) -> Dict[str, str]:
    """Read only the ZONE objects from the source IDF to build the
    canonical label map. Returns {} if the IDF cannot be read, in which
    case semantic pre-validation is skipped and reported as such."""
    if not idf_path or not os.path.isfile(idf_path):
        return {}
    try:
        from .rfh_lib import load_idf, set_idd, IDD_PATH  # type: ignore
    except ImportError:
        try:
            from rfh_lib import load_idf, set_idd, IDD_PATH  # type: ignore
        except ImportError:
            return {}
    try:
        set_idd(idd_path or IDD_PATH)
        idf = load_idf(idf_path)
        return build_canonical_zone_map(z.Name for z in idf.idfobjects["ZONE"])
    except Exception:
        return {}


def llm_router(state: SimulationState) -> SimulationState:
    text = (state.get("user_input") or "").strip()
    if not text:
        return {"errors": ["[V1] No user input received."]}

    model = state.get("model") or "gpt-4o-mini"

    # ---- LLM inference with bounded retry -------------------------------
    agent: Optional[Dict[str, Any]] = None
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            agent = _openai_chat_json(text, model=model)
            break
        except (json.JSONDecodeError, KeyError) as e:
            last_error = f"malformed model output ({e})"
        except urllib.error.URLError as e:
            last_error = f"network error ({e})"
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
    if agent is None:
        return {"errors": [f"LLM router failed after {MAX_RETRIES} attempts: {last_error}"]}

    usage = agent.pop("_usage", {})

    # ---- V3 / V4 structural validation ----------------------------------
    outcome, code, message = validate_agent_json(agent)
    if outcome == "reject":
        return {
            "errors": [f"[{code}] {message}"],
            "agent_json": agent,
            "routing_report": {"outcome": "reject", "reason": code, "usage": usage},
        }

    intent = agent.get("intent", "unknown")
    out: SimulationState = {"agent_json": agent, "intent": intent}
    report: Dict[str, Any] = {"usage": usage, "intent_from_llm": intent}

    if intent != "add_rfh":
        if intent == "ask_clarification":
            out["clarification_question"] = (
                agent.get("clarification_question")
                or 'Which rooms should receive RFH? Please list them (e.g., "Room_1", "Living_2").'
            )
        report["outcome"] = intent
        out["routing_report"] = report
        return out

    # ---- V5 target cleaning ---------------------------------------------
    targets, dropped = clean_targets(agent.get("rfh_targets"))
    report["dropped_targets"] = dropped

    if not targets:
        out["intent"] = "ask_clarification"
        out["clarification_question"] = (
            'Which rooms should receive RFH? Please list them (e.g., "Room_1", "Living_2").'
        )
        report["outcome"] = "clarify"
        report["reason"] = "V5_no_valid_targets"
        out["routing_report"] = report
        return out

    # ---- R1..R4 semantic pre-validation against the source IDF ----------
    zone_map = _load_zone_map(state.get("idf_path"), state.get("idd_path"))
    if not zone_map:
        report["outcome"] = "accept"
        report["zone_prevalidation"] = "skipped_no_idf"
        out["rfh_targets"] = targets
        out["routing_report"] = report
        return out

    resolutions = resolve_all(targets, zone_map)
    report["zone_prevalidation"] = [r.as_dict() for r in resolutions]

    unresolved = [r for r in resolutions if r.status == "unresolved"]
    ambiguous = [r for r in resolutions if r.status == "ambiguous"]

    if unresolved or ambiguous:
        parts: List[str] = []
        if unresolved:
            parts.append(
                "These room names were not found in the model: "
                + ", ".join(f'"{r.label}"' for r in unresolved)
            )
        if ambiguous:
            for r in ambiguous:
                parts.append(
                    f'"{r.label}" matches more than one zone ('
                    + ", ".join(r.candidates) + ")"
                )
        available = sorted({v for v in zone_map.values()})
        parts.append("Available zones are: " + ", ".join(available) + ".")

        out["intent"] = "ask_clarification"
        out["clarification_question"] = " ".join(parts) + " Please restate the room names."
        report["outcome"] = "clarify"
        report["reason"] = "R3_ambiguous" if ambiguous else "R4_unresolved"
        out["routing_report"] = report
        return out

    out["rfh_targets"] = targets
    out["resolved_zones"] = [r.zone_name for r in resolutions]
    report["outcome"] = "accept"
    out["routing_report"] = report
    return out