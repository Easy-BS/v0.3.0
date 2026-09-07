# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 17:24:36 2025

@author: Xiguan Liang @SKKU
"""

# ./Multi_flow/run_graph.py
#
# CHANGES vs. the previous version
# --------------------------------
# 1. The graph is executed ONCE. The old code ran app.stream(...) and then
#    app.invoke(...), which called the LLM twice and ran EnergyPlus twice.
# 2. stdout carries ONLY the final JSON. All logging goes to stderr, because
#    server.js does JSON.parse(stdout) and any log line makes that throw.
# 3. --idf-out defaults to a UNIQUE filename per run. With a fixed filename,
#    a failed run left the previous run's IDF on disk and it was simulated
#    again, so the output looked like the previous building.
# 4. A non-zero exit code is returned when the run produced errors, so
#    server.js can report the failure instead of reporting success.

import os
import sys
import json
import uuid
import argparse
import contextlib
from datetime import datetime

from graph_config import define_graph  # multi-zone graph
from state_schema import SimulationState


# Output directory. Anchored to the PROJECT ROOT (the parent of Multi_flow),
# not to this file's directory, so that generated IDFs land where the RFH,
# CONVHP and CALI flows already expect them. Override with EASYBS_IDF_DIR.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IDF_DIR = os.environ.get(
    "EASYBS_IDF_DIR", os.path.join(PROJECT_ROOT, "generated_idfs"))


def log(msg: str) -> None:
    """Log to stderr. Never to stdout."""
    print(msg, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--idf-out", default=None,
                        help="Output IDF path. Defaults to a unique name per run.")
    parser.add_argument("--epw",
                        default=r"C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw")
    parser.add_argument("--outdir", default="eplusout")
    args = parser.parse_args()

    # Ensure project root
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # A unique output path per run. Never reuse one filename: a stale file
    # from an earlier run would otherwise be picked up downstream.
    if args.idf_out:
        idf_out = os.path.abspath(args.idf_out)
    else:
        run_id = uuid.uuid4().hex[:8]
        idf_out = os.path.abspath(
            os.path.join(DEFAULT_IDF_DIR, "geom_multizone_%s.idf" % run_id))
    os.makedirs(os.path.dirname(idf_out), exist_ok=True)

    log("[run_graph] idf_out = %s" % idf_out)

    app = define_graph()

    initial_state: SimulationState = {
        "user_input": args.prompt,
        "epw_path": args.epw,
        "output_dir": args.outdir,
        "idf_path": idf_out,
    }

    # Execute the graph exactly once.
    #
    # Several nodes (user_query_parser, geomeppy_generator) print progress with
    # a bare print(), which goes to stdout. server.js does JSON.parse(stdout),
    # so ANY such line breaks the response. Rather than editing every node,
    # redirect stdout to stderr for the duration of the graph run. After the
    # context exits, sys.stdout is the real stdout again and carries only JSON.
    with contextlib.redirect_stdout(sys.stderr):
        final_state = app.invoke(initial_state)

    errors = final_state.get("errors") or []
    diagnostics = final_state.get("layout_diagnostics") or []
    blocking = [d for d in diagnostics if d.get("code") != "D6_OPENING"]

    parsed = final_state.get("parsed_building_data") or {}
    log("[run_graph] geometry_source = %s | rooms = %d" % (
        parsed.get("geometry_source"), len(parsed.get("rooms") or {})))

    payload = {
        "ok": not (errors or blocking),
        "idf_path": final_state.get("idf_path"),
        "geometry_source": parsed.get("geometry_source"),
        "room_names": sorted((parsed.get("rooms") or {}).keys()),
        "layout_diagnostics": diagnostics,
        "errors": errors,
        "message": final_state.get("message"),
        "simulation_result": final_state.get("simulation_result"),
    }

    # stdout: JSON and nothing else.
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()

    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    log("[%s] Multi_flow/run_graph.py starting" %
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    sys.exit(main())
