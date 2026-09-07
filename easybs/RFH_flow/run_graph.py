# -*- coding: utf-8 -*-
"""
Created on Sun Nov  9 10:41:11 2025

@author: Xiguan Liang @SKKU
"""

'''
# RFH_flow/run_graph.py
import os, json, argparse
from datetime import datetime
from graph_config import define_graph

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--idf-in", dest="idf_in", required=False)
    p.add_argument("--idd", dest="idd_path", required=False)
    args = p.parse_args()

    app = define_graph()
    init = {"user_input": args.prompt}
    if args.idf_in:
        init["idf_path"] = os.path.abspath(args.idf_in)
    if args.idd_path:
        init["idd_path"] = os.path.abspath(args.idd_path)
    
    final = app.invoke(init)
    print(json.dumps(final, ensure_ascii=False))

if __name__ == "__main__":
    main()
'''

# RFH_flow/run_graph.py
import os, json, argparse, sys
from contextlib import redirect_stdout
from graph_config import define_graph

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--idf-in", dest="idf_in", required=False)
    p.add_argument("--idd", dest="idd_path", required=False)
    p.add_argument("--model", required=False)
    args = p.parse_args()

    app = define_graph()
    init = {"user_input": args.prompt}
    if args.idf_in:
        init["idf_path"] = os.path.abspath(args.idf_in)
    if args.idd_path:
        init["idd_path"] = os.path.abspath(args.idd_path)
    if args.model:
        init["model"] = args.model    
    # IMPORTANT:
    # 1) Do NOT stream (it prints)
    # 2) Redirect all intermediate prints to stderr
    with redirect_stdout(sys.stderr):
        final = app.invoke(init)

    # Print ONLY the final JSON to stdout (what Node parses)
    print(json.dumps(final, ensure_ascii=False))

if __name__ == "__main__":
    main()


