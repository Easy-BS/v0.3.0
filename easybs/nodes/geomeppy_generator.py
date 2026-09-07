# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:44:52 2025

@author: Xiguan Liang  @SKKU
"""

# geomeppy_generator.py

import os
import uuid
from geomeppy import IDF
from state_schema import SimulationState
import math
import time
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")        # crucial: non-GUI backend for uvicorn/FastAPI

# Paths
IDD_PATH = r"C:/EnergyPlusV8-9-0/Energy+.idd"
SEED_IDF = r"C:/EnergyPlusV8-9-0/ExampleFiles/Minimal.idf"
OUT_DIR = "./generated_idfs"
#EPW_PATH = r"C:/EnergyPlusV8-9-0/WeatherData/KOR_INCH'ON_IWEC.epw"
os.makedirs(OUT_DIR, exist_ok=True)


ROTATE_DEG = -45.0
SILL_H = 1.0

# ---------- Helpers ----------
def _getobjects(idf, key):
    if hasattr(idf, "idfobjects"):
        return idf.idfobjects.get(key, [])
    return idf.getobjects(key)

def _remove_all(idf, key):
    try:
        for o in list(_getobjects(idf, key)):
            idf.removeidfobject(o)
    except Exception:
        pass

def clear_geometry(idf):
    for k in [
        "FENESTRATIONSURFACE:DETAILED",
        "BUILDINGSURFACE:DETAILED",
        "SHADING:ZONE:DETAILED",
        "SHADING:BUILDING:DETAILED",
        "ZONE",
        "SURFACEPROPERTY:EXPOSEDFOUNDATIONPERIMETER",
    ]:
        _remove_all(idf, k)

def get_vertices(surf):
    """Extract up to 4 vertices from a surface via raw IDF fields."""
    verts = []
    for i in range(1, 11):  # be generous; walls/roofs are typically 4
        x_name = f"Vertex_{i}_Xcoordinate"
        y_name = f"Vertex_{i}_Ycoordinate"
        z_name = f"Vertex_{i}_Zcoordinate"
        if not hasattr(surf, x_name):
            break
        try:
            x = float(getattr(surf, x_name))
            y = float(getattr(surf, y_name))
            z = float(getattr(surf, z_name))
        except (AttributeError, ValueError, TypeError):
            break
        verts.append((x, y, z))
    return verts

def map_orientation(azimuth_deg, tol=12):
    """Map wall azimuth to cardinal orientation (pre-rotation)."""
    a = int(round(float(azimuth_deg))) % 360
    if min(abs(a - 0),   360 - abs(a - 0))   <= tol: return "north"
    if min(abs(a - 90),  360 - abs(a - 90))  <= tol: return "east"
    if min(abs(a - 180), 360 - abs(a - 180)) <= tol: return "south"
    if min(abs(a - 270), 360 - abs(a - 270)) <= tol: return "west"
    return None

def wall_base_edge(verts):
    """Return two lowest-Z vertices (bottom edge) for a vertical rectangular wall."""
    vs = sorted(verts, key=lambda v: (v[2], v[0], v[1]))
    return vs[0], vs[1] if len(vs) > 1 else vs[0]

def wall_span_info(verts):
    """
    Compute along-wall unit vector u (in XY), base point p0 (the 'left' end),
    and length L along the wall base edge.
    """
    a, b = wall_base_edge(verts)
    vx, vy = (b[0]-a[0], b[1]-a[1])
    L = math.hypot(vx, vy)
    if L == 0:
        # fallback using axis spans
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if (x_max - x_min) >= (y_max - y_min):
            a = min(verts, key=lambda v: v[0]); b = max(verts, key=lambda v: v[0])
            vx, vy = (b[0]-a[0], b[1]-a[1]); L = abs(x_max - x_min)
        else:
            a = min(verts, key=lambda v: v[1]); b = max(verts, key=lambda v: v[1])
            vx, vy = (b[0]-a[0], b[1]-a[1]); L = abs(y_max - y_min)
    ulen = math.hypot(vx, vy) or 1.0
    ux, uy = (vx/ulen, vy/ulen)
    # "left" endpoint along u
    proj_a = a[0]*ux + a[1]*uy
    proj_b = b[0]*ux + b[1]*uy
    p0 = a if proj_a <= proj_b else b
    if p0 is b:
        ux, uy = -ux, -uy
    return (ux, uy), p0, L

def centers_equal_gaps_along_length(L, n, item_w):
    """
    Equal-gaps layout along length L:
      gap g = (L - n*item_w) / (n+1)
    Returns list of center offsets s (from 0) and g.
    """
    g = (L - n * item_w) / (n + 1)
    if g < 0:
        raise ValueError(f"Windows do not fit along length={L:.3f} m: n={n}, W={item_w:.3f}, n*W={n*item_w:.3f}")
    centers = []
    left = g
    for i in range(n):
        left_i = left + i * (item_w + g)
        centers.append(left_i + item_w / 2.0)
    return centers, g

def group_walls_by_floor_and_orientation(idf, N_STORIES, STOREY_H):
    """
    Return dict: floor_idx -> orientation -> list[(wall, verts, L, (ux,uy), p0, z_floor)].
    Uses current (pre-rotation) azimuths to detect N/E/S/W.
    """
    groups = {}
    for wall in idf.getsurfaces(surface_type="Wall"):
        verts = get_vertices(wall)
        if len(verts) < 2:
            continue
        ori = map_orientation(getattr(wall, "azimuth", 0.0))
        if ori is None:
            continue
        (ux, uy), p0, L = wall_span_info(verts)
        z_floor = min(v[2] for v in verts)
        floor_idx = max(0, min(N_STORIES - 1, int(round(z_floor / STOREY_H))))
        groups.setdefault(floor_idx, {}).setdefault(ori, []).append((wall, verts, L, (ux, uy), p0, z_floor))
    return groups

def add_windows_all_sides(idf, N_STORIES, STOREY_H,
                          WINDOW_COUNTS, WINDOW_WIDTHS, WINDOW_HEIGHTS):
    groups = group_walls_by_floor_and_orientation(idf, N_STORIES, STOREY_H)

    for f in range(N_STORIES):
        for ori in ("north", "east", "south", "west"):
            target_n = WINDOW_COUNTS[ori]
            if target_n <= 0:
                continue

            cand = groups.get(f, {}).get(ori, [])
            if not cand:
                print(f"⚠️  No {ori} wall found for floor {f+1}")
                continue

            wall, verts, L, (ux, uy), p0, z_floor = max(cand, key=lambda t: t[2])

            WIN_W = WINDOW_WIDTHS[ori]
            WIN_H = WINDOW_HEIGHTS[ori]

            try:
                centers, g = centers_equal_gaps_along_length(L, target_n, WIN_W)
            except ValueError as e:
                print(f"⚠️  Floor {f+1} {ori}: {e}  (skipping)")
                continue

            sill = z_floor + SILL_H
            head = sill + WIN_H

            # ... (rest identical)


            for i, s_c in enumerate(centers, 1):
                s1 = s_c - WIN_W/2.0
                s2 = s_c + WIN_W/2.0

                # Generate along the wall vector
                x1 = p0[0] + ux * s1; y1 = p0[1] + uy * s1
                x2 = p0[0] + ux * s2; y2 = p0[1] + uy * s2

                # Force CCW order (same as parent wall orientation)
                coords = [
                    (x1, y1, sill),
                    (x2, y2, sill),
                    (x2, y2, head),
                    (x1, y1, head),
                ]

                # Ensure consistent CCW winding
                if hasattr(idf, "check_subsurfaces"):
                    # geomeppy will auto-flip if inconsistent
                    pass

                win = idf.newidfobject(
                    "FENESTRATIONSURFACE:DETAILED",
                    Name=f"{ori.capitalize()}Win_F{f+1}_{i}",
                    Surface_Type="Window",
                    Building_Surface_Name=wall.Name,
                    Vertex_1_Xcoordinate=coords[0][0], Vertex_1_Ycoordinate=coords[0][1], Vertex_1_Zcoordinate=coords[0][2],
                    Vertex_2_Xcoordinate=coords[1][0], Vertex_2_Ycoordinate=coords[1][1], Vertex_2_Zcoordinate=coords[1][2],
                    Vertex_3_Xcoordinate=coords[2][0], Vertex_3_Ycoordinate=coords[2][1], Vertex_3_Zcoordinate=coords[2][2],
                    Vertex_4_Xcoordinate=coords[3][0], Vertex_4_Ycoordinate=coords[3][1], Vertex_4_Zcoordinate=coords[3][2],
                )

            print(f"√ Floor {f+1} {ori}: {target_n} windows | gaps g={g:.3f} m | wall L={L:.3f} m")

    # After all windows added → enforce orientation check
    try:
        if hasattr(idf, "check_subsurfaces"):
            idf.check_subsurfaces()
    except Exception as e:
        print(f"⚠️ check_subsurfaces failed: {e}")


'''
def add_windows_all_sides(idf, N_STORIES, STOREY_H,WINDOW_COUNTS, WIN_W, WIN_H):
    """Add windows per floor/orientation with equal gaps using each wall's local axis."""
    groups = group_walls_by_floor_and_orientation(idf, N_STORIES, STOREY_H)

    for f in range(N_STORIES):
        for ori in ("north", "east", "south", "west"):
            target_n = WINDOW_COUNTS[ori]
            if target_n <= 0:
                continue
            cand = groups.get(f, {}).get(ori, [])
            if not cand:
                print(f"⚠️  No {ori} wall found for floor {f+1}")
                continue

            # choose the wall with the longest along-wall length
            wall, verts, L, (ux, uy), p0, z_floor = max(cand, key=lambda t: t[2])

            try:
                centers, g = centers_equal_gaps_along_length(L, target_n, WIN_W)
            except ValueError as e:
                print(f"⚠️  Floor {f+1} {ori}: {e}  (skipping)")
                continue

            sill = z_floor + SILL_H
            head = sill + WIN_H

            for i, s_c in enumerate(centers, 1):
                # window endpoints along wall
                s1 = s_c - WIN_W/2.0
                s2 = s_c + WIN_W/2.0
                x1 = p0[0] + ux * s1; y1 = p0[1] + uy * s1
                x2 = p0[0] + ux * s2; y2 = p0[1] + uy * s2

                coords = [
                    (x1, y1, sill),
                    (x2, y2, sill),
                    (x2, y2, head),
                    (x1, y1, head),
                ]
                idf.newidfobject(
                    "FENESTRATIONSURFACE:DETAILED",
                    Name=f"{ori.capitalize()}Win_F{f+1}_{i}",
                    Surface_Type="Window",
                    Building_Surface_Name=wall.Name,
                    Vertex_1_Xcoordinate=coords[0][0], Vertex_1_Ycoordinate=coords[0][1], Vertex_1_Zcoordinate=coords[0][2],
                    Vertex_2_Xcoordinate=coords[1][0], Vertex_2_Ycoordinate=coords[1][1], Vertex_2_Zcoordinate=coords[1][2],
                    Vertex_3_Xcoordinate=coords[2][0], Vertex_3_Ycoordinate=coords[2][1], Vertex_3_Zcoordinate=coords[2][2],
                    Vertex_4_Xcoordinate=coords[3][0], Vertex_4_Ycoordinate=coords[3][1], Vertex_4_Zcoordinate=coords[3][2],
                )
            print(f"✅ Floor {f+1} {ori}: {target_n} windows | gaps g={g:.3f} m | wall L={L:.3f} m")
'''
def _centroid_xy_of_walls(idf):
    xs, ys, n = 0.0, 0.0, 0
    for s in idf.getsurfaces(surface_type="Wall"):
        verts = get_vertices(s)
        for (x, y, _z) in verts:
            xs += x; ys += y; n += 1
    return (xs/n, ys/n) if n else (0.0, 0.0)

def _rotate_xy_vertices(idf, angle_deg, origin_xy):
    """Manual geometry rotation for versions without idf.rotate()."""
    cx, cy = origin_xy
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)

    def _rot(x, y):
        x0, y0 = x - cx, y - cy
        return (x0*c - y0*s + cx, x0*s + y0*c + cy)

    for key in ["BUILDINGSURFACE:DETAILED", "FENESTRATIONSURFACE:DETAILED", "SHADING:ZONE:DETAILED", "SHADING:BUILDING:DETAILED"]:
        for obj in _getobjects(idf, key):
            for i in range(1, 11):  # up to 10 vertices
                x_name = f"Vertex_{i}_Xcoordinate"
                y_name = f"Vertex_{i}_Ycoordinate"
                z_name = f"Vertex_{i}_Zcoordinate"
                if not hasattr(obj, x_name):
                    break
                try:
                    x = float(getattr(obj, x_name))
                    y = float(getattr(obj, y_name))
                except (AttributeError, ValueError, TypeError):
                    break
                xr, yr = _rot(x, y)
                setattr(obj, x_name, xr)
                setattr(obj, y_name, yr)

def rotate_point(x, y, angle_deg, cx=0.0, cy=0.0):
    """Rotate point (x,y) around (cx,cy) by angle_deg (degrees)."""
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    x0, y0 = x - cx, y - cy
    xr = x0 * c - y0 * s + cx
    yr = x0 * s + y0 * c + cy
    return xr, yr

def rotated_footprint(length_x, width_y, angle_deg):
    """Return rotated rectangle footprint centered on origin (0,0)."""
    base_coords = [(0, 0), (length_x, 0), (length_x, width_y), (0, width_y)]
    # Rotate about building centroid
    cx = length_x / 2.0
    cy = width_y / 2.0
    return [rotate_point(x, y, angle_deg, cx, cy) for (x, y) in base_coords]

def _normal_xy_from_verts(verts):
    """Return the outward normal projected on XY using first three vertices."""
    if len(verts) < 3:
        return (0.0, 0.0)
    x1,y1,z1 = verts[0]; x2,y2,z2 = verts[1]; x3,y3,z3 = verts[2]
    v1 = (x2-x1, y2-y1, z2-z1)
    v2 = (x3-x1, y3-y1, z3-z1)
    # 3D cross product n = v1 x v2
    nx = v1[1]*v2[2] - v1[2]*v2[1]
    ny = v1[2]*v2[0] - v1[0]*v2[2]
    # nz = v1[0]*v2[1] - v1[1]*v2[0]  # not used; outward for walls is horizontal
    return (nx, ny)

def fix_subsurface_normals_xy(idf):
    """Flip fenestration vertex order when XY normal opposes parent wall."""
    # Build a quick index from surface name to object
    walls = {s.Name: s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]}
    fixed = 0
    for win in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = walls.get(win.Building_Surface_Name)
        if not parent:
            continue
        pv = get_vertices(parent)   # you already have this helper
        cv = get_vertices(win)
        pn = _normal_xy_from_verts(pv)
        cn = _normal_xy_from_verts(cv)
        # dot product in XY
        dot = pn[0]*cn[0] + pn[1]*cn[1]
        if dot < 0:
            # reverse fenestration vertex order in-place
            rev = list(reversed(cv))
            for i, (x,y,z) in enumerate(rev, start=1):
                setattr(win, f"Vertex_{i}_Xcoordinate", x)
                setattr(win, f"Vertex_{i}_Ycoordinate", y)
                setattr(win, f"Vertex_{i}_Zcoordinate", z)
            fixed += 1
    if fixed:
        print(f"√ Fixed XY normals on {fixed} fenestration surfaces")
    else:
        print("✅ No XY normal mismatches detected")
#%%
def _zone_min_z(idf, zone_name):
    zmin = None
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name != zone_name:
            continue
        for i in range(1, 11):
            xn = f"Vertex_{i}_Xcoordinate"; yn = f"Vertex_{i}_Ycoordinate"; zn = f"Vertex_{i}_Zcoordinate"
            if not hasattr(s, xn): break
            try:
                z = float(getattr(s, zn))
            except (TypeError, ValueError): break
            zmin = z if zmin is None else min(zmin, z)
    return 0.0 if zmin is None else zmin

def _zones_sorted_by_height(idf):
    zones = [z.Name for z in idf.idfobjects["ZONE"]]
    zones.sort(key=lambda zn: _zone_min_z(idf, zn))
    return zones

def _collect_horiz(idf, zone_name, types=("Floor","Ceiling","Roof"), ztol=1e-4):
    wants = {t.lower() for t in types}
    out = []
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name != zone_name or s.Surface_Type.lower() not in wants:
            continue
        # horizontal test
        zs = []
        for i in range(1, 11):
            zn = f"Vertex_{i}_Zcoordinate"
            if not hasattr(s, zn): break
            try: zs.append(float(getattr(s, zn)))
            except (TypeError, ValueError): break
        if not zs: continue
        if max(zs) - min(zs) <= ztol:
            # area in XY for ranking
            xs, ys = [], []
            for i in range(1, 11):
                xn = f"Vertex_{i}_Xcoordinate"; yn = f"Vertex_{i}_Ycoordinate"
                if not hasattr(s, xn): break
                try:
                    xs.append(float(getattr(s, xn))); ys.append(float(getattr(s, yn)))
                except (TypeError, ValueError): break
            area = 0.0
            n = len(xs)
            for i in range(n):
                j = (i+1) % n
                area += xs[i]*ys[j] - xs[j]*ys[i]
            out.append((s, abs(area)*0.5))
    # largest first
    out.sort(key=lambda t: t[1], reverse=True)
    return out

def ensure_interzone_pairs(idf, shared_const="FLOOR_Const", verbose=True):
    """
    For each adjacent storey:
      - pick the largest Ceiling (or Roof if mis-labeled) in lower zone
      - pick the largest Floor in upper zone
      - set Surface↔Surface linking, NoSun/NoWind
      - set BOTH to the SAME construction (shared_const)
    Leaves ground Floor (Ground) and top Roof (Outdoors) untouched.
    """
    zones = _zones_sorted_by_height(idf)
    pairs = 0
    for i in range(len(zones) - 1):
        zl, zu = zones[i], zones[i+1]
        ceil_low = _collect_horiz(idf, zl, ("Ceiling","Roof"))
        floor_up = _collect_horiz(idf, zu, ("Floor",))
        if not ceil_low or not floor_up:
            if verbose:
                print(f"⚠️ No candidates to pair between {zl} and {zu}")
            continue
        s_ceil = ceil_low[0][0]
        s_floor = floor_up[0][0]

        # Interzone link (both directions)
        s_ceil.Outside_Boundary_Condition = "Surface"
        s_ceil.Outside_Boundary_Condition_Object = s_floor.Name
        s_ceil.Sun_Exposure = "NoSun"; s_ceil.Wind_Exposure = "NoWind"

        s_floor.Outside_Boundary_Condition = "Surface"
        s_floor.Outside_Boundary_Condition_Object = s_ceil.Name
        s_floor.Sun_Exposure = "NoSun"; s_floor.Wind_Exposure = "NoWind"

        # SAME construction on both sides (your manual choice: FLOOR_Const)
        s_ceil.Construction_Name = shared_const
        s_floor.Construction_Name = shared_const

        pairs += 1
        if verbose:
            print(f"√ Linked {zl} [{s_ceil.Name}] ↔ {zu} [{s_floor.Name}] | Const={shared_const}")
    if verbose:
        print(f"√ Interzone pairs created: {pairs}")


#%%
def generate_idf_file(state: SimulationState) -> SimulationState:
    try:
        parsed = state["parsed_building_data"]
        LENGTH_X = parsed["length"]
        WIDTH_Y = parsed["width"]
        STOREY_H = parsed["floor_height"]
        N_STORIES = parsed["floors"]
        TOTAL_H   = STOREY_H * N_STORIES  # IMPORTANT: pass TOTAL height to add_block
        
        
        window_data = parsed.get("windows", {})
        
        WINDOW_COUNTS = {
            orient: window_data.get(orient, {}).get("count", 0)
            for orient in ["north", "east", "south", "west"]
        }

        
        WIN_W = window_data.get("width",1.5)
        WIN_H = window_data.get("height",1.5)
        
        ROTATE_EP = parsed['orientation']
        ROTATE_DEG = parsed['orientation'] * -1
        SILL_H = 1.0
        
        ###########################
        IDF.setiddname(IDD_PATH)
        idf = IDF(SEED_IDF)
        
        

        # ...
        # Extract counts, widths, heights per orientation
        WINDOW_COUNTS = {}
        WINDOW_WIDTHS = {}
        WINDOW_HEIGHTS = {}
        for orient in ["north", "east", "south", "west"]:
            wdat = window_data.get(orient, {})
            WINDOW_COUNTS[orient] = wdat.get("count", 0)
            WINDOW_WIDTHS[orient] = wdat.get("width", 1.5)
            WINDOW_HEIGHTS[orient] = wdat.get("height", 1.5)
        
        # ...

        # Fresh start
        clear_geometry(idf)
        
        
        # Add one multi-storey rectangular block (no windows yet)
        idf.add_block(
            name="TestBlock",
            coordinates=[(0, 0), (LENGTH_X, 0), (LENGTH_X, WIDTH_Y), (0, WIDTH_Y)],
            height=TOTAL_H,            # total height (ensures 3.3 m per floor)
            num_stories=N_STORIES,     # equal split
            below_ground_stories=0,
            zoning="by_storey",
        )
        
        # --- Modify the North Axis ---
        idf.idfobjects["BUILDING"][0].North_Axis = ROTATE_EP
        
        '''# Add IdealLoadsAirSystem
        for zone in idf.idfobjects["ZONE"]:
            idf.newidfobject(
                "ZONEHVAC:IDEALLOADSAIRSYSTEM",
                Name=f"ILS_{zone.Name}",
                Zone_Supply_Air_Node_Name=f"{zone.Name}_Supply",
                Zone_Exhaust_Air_Node_Name=f"{zone.Name}_Exhaust"
            )
        '''   
            

        for obj in list(idf.idfobjects["SIMULATIONCONTROL"]):
            idf.removeidfobject(obj)
        
        # Simulation control object
        idf.newidfobject(
            "SIMULATIONCONTROL",
            Do_Zone_Sizing_Calculation="No",
            Do_System_Sizing_Calculation="No",
            Do_Plant_Sizing_Calculation="No",
            Run_Simulation_for_Sizing_Periods="No",
            Run_Simulation_for_Weather_File_Run_Periods="Yes"
        )

        # Optional: normalize / clean geometry (guarded)
        for fn in ("translate_to_origin", "intersect_match"):
            if hasattr(idf, fn):
                try:
                    getattr(idf, fn)()
                except Exception:
                    pass

        
        #%%
        # Add windows with equal-gaps placement (pre-rotation)
        #add_windows_all_sides(idf, N_STORIES, STOREY_H, WINDOW_COUNTS, WIN_W, WIN_H)
        
        # Add windows with equal-gaps placement
        add_windows_all_sides(
            idf,
            N_STORIES,
            STOREY_H,
            WINDOW_COUNTS,
            WINDOW_WIDTHS,
            WINDOW_HEIGHTS
        )
        
        ###########################################################
        # Define glazing material + construction
        idf.newidfobject(
            "WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
            Name="GLZ_Simple_3Wm2K_SHGC60_VT70",
            UFactor=3.0,
            Solar_Heat_Gain_Coefficient=0.60,
            Visible_Transmittance=0.70
        )
        idf.newidfobject(
            "CONSTRUCTION",
            Name="WIN_Simple_Clear",
            Outside_Layer="GLZ_Simple_3Wm2K_SHGC60_VT70"
        )
        # Assign to all fenestration surfaces
        for win in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
            win.Construction_Name = "WIN_Simple_Clear"
        ###########################################################
        # --- Define opaque materials & constructions ---
        idf.newidfobject(
            "MATERIAL",
            Name="OpaqueWall_Mat",
            Roughness="MediumRough",
            Thickness=0.2,          # m
            Conductivity=0.1,       # W/m-K
            Density=1800,           # kg/m3
            Specific_Heat=900       # J/kg-K
        )
        
        idf.newidfobject(
            "MATERIAL",
            Name="OpaqueRoof_Mat",
            Roughness="MediumRough",
            Thickness=0.25,
            Conductivity=0.8,
            Density=2000,
            Specific_Heat=900
        )
        
        idf.newidfobject(
            "MATERIAL",
            Name="OpaqueFloor_Mat",
            Roughness="MediumRough",
            Thickness=0.25,
            Conductivity=1.4,
            Density=2200,
            Specific_Heat=900
        )
        
        idf.newidfobject(
            "CONSTRUCTION",
            Name="WALL_Const",
            Outside_Layer="OpaqueWall_Mat"
        )
        idf.newidfobject(
            "CONSTRUCTION",
            Name="ROOF_Const",
            Outside_Layer="OpaqueRoof_Mat"
        )
        idf.newidfobject(
            "CONSTRUCTION",
            Name="FLOOR_Const",
            Outside_Layer="OpaqueFloor_Mat"
        )
        
        # --- Assign them to all opaque surfaces ---
        for srf in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
            if not srf.Construction_Name:
                stype = srf.Surface_Type.upper()
                if stype == "WALL":
                    srf.Construction_Name = "WALL_Const"
                elif stype in ("ROOF", "CEILING"):
                    srf.Construction_Name = "ROOF_Const"
                elif stype == "FLOOR":
                    srf.Construction_Name = "FLOOR_Const"
        ###########################################################                
        # --- Fix subsurface orientation mismatches ---
        try:
            if hasattr(idf, "match"):
                idf.match()
            if hasattr(idf, "check_subsurfaces"):
                idf.check_subsurfaces()
            print("√ Geometry orientation checked and corrected")
        except Exception as e:
            print(f"⚠️ Geometry check failed: {e}")

        fix_subsurface_normals_xy(idf)##################
        #%%
        # After geometry normalization & before saving / rotating
        ensure_interzone_pairs(idf, shared_const="FLOOR_Const")

        #%%
        ggr = idf.idfobjects["GLOBALGEOMETRYRULES"][0]
        ggr.Starting_Vertex_Position = "UpperLeftCorner"
        ggr.Vertex_Entry_Direction   = "CounterClockWise"
        ggr.Coordinate_System        = "Relative"

        
        #%%
        ########Not rotated##########
        
        #obj_path = os.path.join(OUT_DIR, "3D.obj")
        #idf.to_obj(obj_path)
        #idf.view_model()
        out_idf = os.path.join(OUT_DIR, "geom_bui_E+.idf")
        idf.saveas(out_idf)
        state["idf_path"] = out_idf
        
        #%%
        #   
        
        # --- Rotate the entire geometry by ROTATE_DEG at the end ---
        cx, cy = _centroid_xy_of_walls(idf)
        rotated = False
        if hasattr(idf, "rotate"):
            try:
                # If translate is available, rotate about the centroid
                if hasattr(idf, "translate"):
                    idf.translate((-cx, -cy, 0.0))
                    idf.rotate(ROTATE_DEG)
                    idf.translate((cx, cy, 0.0))
                else:
                    # rotate about origin (fine if your footprint starts at (0,0))
                    idf.rotate(ROTATE_DEG)
                rotated = True
                print(f"↻ Rotated geometry by {ROTATE_DEG}°")
            except Exception as e:
                print(f"⚠️ idf.rotate failed: {e}")
        if not rotated:
            try:
                _rotate_xy_vertices(idf, ROTATE_DEG, origin_xy=(cx, cy))
                rotated = True
                print(f"↻ Rotated geometry manually by {ROTATE_DEG}° about centroid ({cx:.3f}, {cy:.3f})")
            except Exception as e:
                print(f"⚠️ Manual rotation failed: {e}")      

        
        ################################
        
        print(f"⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] <IDF finished.>")
        
        ##########3D Preview##########
        #obj_path = os.path.join(OUT_DIR, "3D_rotated.obj")
        #idf.to_obj(obj_path)
        #idf.view_model()
        

        ###############
        #idf.intersect_match()
        # Save
        out_idf = os.path.join(OUT_DIR, "geom_bui_SketchUp.idf")
        idf.saveas(out_idf)
        
        #==================================================================#
        out_dir = './preview_3d'        #'./3D_png'
        #  Draw model (Geomeppy creates/uses its own current figure)
        plt.ioff()                # turn off interactive mode
        idf.view_model()          # DO NOT create a new plt.figure() here

        #  Grab the figure Geomeppy drew on and save it
        fig = plt.gcf()           # get the current figure that was just drawn
        os.makedirs(out_dir, exist_ok=True)
        png_path = os.path.join(out_dir, f"idf_{uuid.uuid4().hex[:8]}.png")

        # Force a draw before save; then save and close
        fig.canvas.draw()
        fig.set_size_inches(8, 6)     # optional: control size
        fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)           
        #==================================================================#
        
        print(f"√ IDF saved → {out_idf}")
        #state["idf_path"] = out_idf
        return state


    except Exception as e:
        return {"errors": [f"Error generating IDF: {str(e)}"]}

