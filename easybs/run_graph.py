# -*- coding: utf-8 -*-
"""
Created on Sat Oct 25 03:03:30 2025

@author: user
"""

# ./run_graph.py
import os
from langgraph.graph import StateGraph
from state_schema import SimulationState
from graph_config import define_graph
from datetime import datetime
from pathlib import Path
import re
import time

# --- Keep your current chdir if you rely on relative paths ---
os.chdir(os.path.dirname(__file__))

# ---------- NEW: singleton graph loader ----------
_app = None
def get_app() -> StateGraph:
    global _app
    if _app is None:
        _app = define_graph()   # your compiled LangGraph
    return _app

# ---------- NEW: build IDF from a single prompt ----------
def build_idf_from_prompt(user_input: str):
    """
    Runs your graph once using a single natural-language building description.
    Returns (final_state, idf_path). Make sure your graph produces idf_path in state.
    """
    app = get_app()
    payload = {"user_input": user_input}
    # Optional: stream if you want logs here
    # for step in app.stream(payload): print(step)
    final_state = app.invoke(payload)
    # IMPORTANT: ensure your graph writes 'idf_path' in the state
    idf_path = final_state.get("idf_path") or final_state.get("output_idf")  # adapt to your keys
    return final_state, idf_path

# ---------- Optional: keep your old file-based multi-input runner ----------
INPUT_FILENAME = "Input_by_Users.txt"
def parse_user_inputs(text: str):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text: return []
    return [p.strip() for p in re.split(r'\n\s*(?:-{3,}|={3,}|#{3,})\s*\n|\n{2,}', text) if p.strip()]

def build_idfs_from_file(path: str):
    app = get_app()
    with open(path, "r", encoding="utf-8-sig") as f:
        raw_text = f.read()
    entries = parse_user_inputs(raw_text)
    results = []
    for user_input in entries:
        payload = {"user_input": user_input}
        final_state = app.invoke(payload)
        idf_path = final_state.get("idf_path") or final_state.get("output_idf")
        results.append({"input": user_input, "state": final_state, "idf_path": idf_path})
    return results

# ---------- Keep your previous CLI runner, but make it optional ----------
if __name__ == "__main__":
    input_path = Path(__file__).with_name(INPUT_FILENAME)
    if not input_path.exists():
        raise FileNotFoundError(f"'{INPUT_FILENAME}' not found in {input_path.parent}")
    results = build_idfs_from_file(str(input_path))
    print(f"📄 Processed {len(results)} input(s). Last IDF:", results[-1]["idf_path"] if results else None)
