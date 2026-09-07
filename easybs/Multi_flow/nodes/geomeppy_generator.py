# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:44:52 2025

@author: user
"""


# .Multi_flow/nodes/geomeppy_generator.py

import os
import math
from typing import Dict, List, Tuple
from collections import defaultdict

from geomeppy import IDF
import numpy as np

from state_schema import SimulationState
import uuid
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")        # crucial: non-GUI backend for uvicorn/FastAPI

# -------------------------------------------------
# Paths (edit if needed)
# -------------------------------------------------
IDD_PATH = r"C:/EnergyPlusV8-9-0/Energy+.idd"
SEED_IDF = r"C:/EnergyPlusV8-9-0/ExampleFiles/Minimal.idf"
OUT_DIR = "./generated_idfs"
os.makedirs(OUT_DIR, exist_ok=True)

# -------------------------------------------------
# Helpers 
# -------------------------------------------------
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
def _getobjects(idf, key):
    if hasattr(idf, "idfobjects"):
        return idf.idfobjects.get(key, [])
    return idf.getobjects(key)
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
def set_simulation_control_to_runperiod_only(idf):
    """Ensure SimulationControl runs only for weather-file run periods, not sizing."""
    # Remove any existing SimulationControl objects
    for sc in list(idf.idfobjects["SIMULATIONCONTROL"]):
        idf.removeidfobject(sc)

    # Create a clean SimulationControl
    sc = idf.newidfobject("SIMULATIONCONTROL")
    sc.Do_Zone_Sizing_Calculation = "No"
    sc.Do_System_Sizing_Calculation = "No"
    sc.Do_Plant_Sizing_Calculation = "No"
    sc.Run_Simulation_for_Sizing_Periods = "No"
    sc.Run_Simulation_for_Weather_File_Run_Periods = "Yes"

    print("✓ SimulationControl updated: RunPeriod only (sizing calculations disabled).")
    return sc
def signed_area(xy: List[Tuple[float, float]]) -> float:
    a = 0.0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a

def force_cw(xy: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Return vertices in CLOCKWISE order (plan view)."""
    return xy if signed_area(xy) < 0 else list(reversed(xy))

def _surface_vertices(s):
    vs = []
    for i in range(1, 5):
        x = getattr(s, f"Vertex_{i}_Xcoordinate", None)
        y = getattr(s, f"Vertex_{i}_Ycoordinate", None)
        z = getattr(s, f"Vertex_{i}_Zcoordinate", None)
        if x is not None and y is not None and z is not None:
            vs.append((float(x), float(y), float(z)))
    return vs

def _polygon_normal_z(vs):
    nx = ny = nz = 0.0
    n = len(vs)
    for i in range(n):
        x1, y1, z1 = vs[i]
        x2, y2, z2 = vs[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return nz

def reverse_surface_winding(surface):
    verts = []
    for i in range(1, 5):
        x = getattr(surface, f"Vertex_{i}_Xcoordinate", None)
        y = getattr(surface, f"Vertex_{i}_Ycoordinate", None)
        z = getattr(surface, f"Vertex_{i}_Zcoordinate", None)
        if x is not None and y is not None and z is not None:
            verts.append((x, y, z))
    if not verts:
        return
    verts.reverse()
    for idx, (x, y, z) in enumerate(verts, start=1):
        setattr(surface, f"Vertex_{idx}_Xcoordinate", x)
        setattr(surface, f"Vertex_{idx}_Ycoordinate", y)
        setattr(surface, f"Vertex_{idx}_Zcoordinate", z)
    for j in range(len(verts) + 1, 5):
        setattr(surface, f"Vertex_{j}_Xcoordinate", "")
        setattr(surface, f"Vertex_{j}_Ycoordinate", "")
        setattr(surface, f"Vertex_{j}_Zcoordinate", "")

def fix_floor_roof_winding(idf, tol=1e-9):
    """Ensure floors point down and roofs/ceilings point up."""
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        stype = (s.Surface_Type or "").upper()
        if stype not in ("FLOOR", "ROOF", "ROOFCEILING", "CEILING"):
            continue
        vs = _surface_vertices(s)
        if not vs:
            continue
        zs = [p[2] for p in vs]
        zavg = sum(zs) / len(zs)
        zmin = min(zs)
        zmax = max(zs)
        nz = _polygon_normal_z(vs)

        if stype == "FLOOR":
            if abs(zavg - zmin) < tol and nz > 0:
                reverse_surface_winding(s)
        else:
            if abs(zavg - zmax) < tol and nz < 0:
                reverse_surface_winding(s)

def surface_outward_normal_xy(surface):
    vs = _surface_vertices(surface)
    if len(vs) < 3:
        return (0.0, 0.0)
    nx = ny = nz = 0.0
    n = len(vs)
    for i in range(n):
        x1, y1, z1 = vs[i]
        x2, y2, z2 = vs[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return (nx, ny)

def azimuth_deg_from_xy_normal(nx, ny):
    """EnergyPlus azimuth convention: 0=N(+Y), clockwise positive."""
    ang = math.degrees(math.atan2(nx, ny))
    if ang < 0:
        ang += 360.0
    return ang

def cardinal_of_azimuth(az):
    bins = [(0, 'N'), (90, 'E'), (180, 'S'), (270, 'W'), (360, 'N')]
    diffs = [(abs((az - b + 180) % 360 - 180), c) for b, c in bins]
    return min(diffs, key=lambda t: t[0])[1]

def pick_exterior_wall(idf, zone_name, target_cardinal='S'):
    candidates = []
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Zone_Name or "") != zone_name:
            continue
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        if (s.Outside_Boundary_Condition or "").strip().lower() != "outdoors":
            continue
        nx, ny = surface_outward_normal_xy(s)
        az = azimuth_deg_from_xy_normal(nx, ny)
        target_map = {'N':0.0,'E':90.0,'S':180.0,'W':270.0}
        diff = abs((az - target_map[target_cardinal.upper()] + 180) % 360 - 180)
        candidates.append((diff, az, s))
    if not candidates:
        raise ValueError(f"No exterior wall in zone '{zone_name}' for {target_cardinal}.")
    candidates.sort(key=lambda t: t[0])
    return candidates[0][2]

def add_centered_window_on_wall(idf, wall_surface, win_w, win_h, name=None, construction=""):
    vs = _surface_vertices(wall_surface)
    if len(vs) < 4:
        raise ValueError("Wall surface is not quadrilateral.")
    vs_sorted = sorted(vs, key=lambda p: p[2])
    pA, pB = vs_sorted[0], vs_sorted[1]
    pC, pD = vs_sorted[2], vs_sorted[3]
    A = np.array(pA); B = np.array(pB); C = np.array(pC); D = np.array(pD)
    u = B - A; u[2] = 0.0; ulen = np.linalg.norm(u) or 1.0; u = u/ulen
    v = ((C + D)/2.0 - (A + B)/2.0); v[0]=v[1]=0.0; vlen = np.linalg.norm(v) or 1.0; v = v/vlen
    center = (A + B + C + D) / 4.0
    hw, hh = win_w/2.0, win_h/2.0
    p1 = center - hw*u - hh*v
    p2 = center + hw*u - hh*v
    p3 = center + hw*u + hh*v
    p4 = center - hw*u + hh*v

    def normal_sign(pts):
        nx = ny = nz = 0.0
        m = len(pts)
        for i in range(m):
            x1,y1,z1 = pts[i]
            x2,y2,z2 = pts[(i+1)%m]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        return 1 if nz >= 0 else -1

    parent_sign = normal_sign(vs)
    win_poly = [tuple(p1), tuple(p2), tuple(p3), tuple(p4)]
    if normal_sign(win_poly) != parent_sign:
        win_poly = [win_poly[0], win_poly[3], win_poly[2], win_poly[1]]

    wname = name or (wall_surface.Name + "_Win")
    win = idf.newidfobject("FENESTRATIONSURFACE:DETAILED")
    win.Name = wname
    win.Surface_Type = "Window"
    if construction: win.Construction_Name = construction
    win.Building_Surface_Name = wall_surface.Name
    for i, (x, y, z) in enumerate(win_poly, start=1):
        setattr(win, f"Vertex_{i}_Xcoordinate", float(x))
        setattr(win, f"Vertex_{i}_Ycoordinate", float(y))
        setattr(win, f"Vertex_{i}_Zcoordinate", float(z))
    for j in range(5, 11):
        try:
            setattr(win, f"Vertex_{j}_Xcoordinate", "")
            setattr(win, f"Vertex_{j}_Ycoordinate", "")
            setattr(win, f"Vertex_{j}_Zcoordinate", "")
        except Exception:
            break
    return win

def ensure_interior_glazing_construction(idf, cons_name="Interior_Glass_Door"):
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower()==cons_name.lower()]
    if cons: return cons[0]
    glz = idf.newidfobject("WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
                           Name="INT_SG_2p2",
                           UFactor=2.2,
                           Solar_Heat_Gain_Coefficient=0.65,
                           Visible_Transmittance=0.75)
    c = idf.newidfobject("CONSTRUCTION", Name=cons_name)
    c.Outside_Layer = glz.Name
    return c

def resolve_zone_label(idf, user_label, floor_suffix="F1"):
    """Map a user room label to the geomeppy zone name created here."""
    def variants(s):
        out = set()
        s0 = s.strip()
        out.add(s0)
        out.add(s0.replace(" ", "_"))
        import re
        out.add(re.sub(r'(\D)(\d+)$', r'\1_\2', s0))
        return {v + "_" + floor_suffix if "_F" not in v.upper() else v for v in out}

    want = {v.lower() for v in variants(user_label)}
    for z in idf.idfobjects["ZONE"]:
        base = z.Name.replace("Block ", "").replace(" Storey 0", "")
        if base.lower() in want:
            return z.Name
    raise ValueError(f"Could not resolve zone for label '{user_label}'. Available: "
                     f"{[z.Name for z in idf.idfobjects['ZONE']]}")

def add_exterior_window_by_orientation(idf, user_room_label, cardinal, width_m, height_m,
                                       cons_name="Simple_DoublePane"):
    # ensure a simple glazing construction exists
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower()==cons_name.lower()]
    if not cons:
        glz = idf.newidfobject("WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
                               Name="SG_2p0", UFactor=2.0,
                               Solar_Heat_Gain_Coefficient=0.6, Visible_Transmittance=0.7)
        cc = idf.newidfobject("CONSTRUCTION", Name=cons_name)
        cc.Outside_Layer = glz.Name

    zone_name = resolve_zone_label(idf, user_room_label, floor_suffix="F1")
    wall = pick_exterior_wall(idf, zone_name, target_cardinal=cardinal.upper())
    win = add_centered_window_on_wall(idf, wall, width_m, height_m,
                                      name=f"{zone_name}_{cardinal}_Win",
                                      construction=cons_name)
    return wall, win

def find_interior_wall_pair_between_zones(idf, zone_a_name, zone_b_name):
    walls = [s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]
             if (s.Surface_Type or "").strip().lower()=="wall"
             and (s.Outside_Boundary_Condition or "").strip().lower()=="surface"]
    walls_a = [s for s in walls if (s.Zone_Name or "")==zone_a_name]
    by_name = {s.Name: s for s in walls if (s.Zone_Name or "")==zone_b_name}
    pairs = []
    for wa in walls_a:
        mate = getattr(wa, "Outside_Boundary_Condition_Object", "")
        if mate and mate in by_name:
            wb = by_name[mate]
            if getattr(wb, "Outside_Boundary_Condition_Object", "") == wa.Name:
                pairs.append((wa, wb))

    if not pairs:
        raise ValueError(f"No interior wall pair between '{zone_a_name}' and '{zone_b_name}'.")
    def area4(s):
        vs = _surface_vertices(s)
        if len(vs) < 4: return 0.0
        a,b,c,d = map(np.array, vs); u = b - a; v = d - a
        return float(np.linalg.norm(np.cross(u, v)))
    pairs.sort(key=lambda p: area4(p[0]), reverse=True)
    return pairs[0]

def add_interior_window_between_rooms(idf, room_a_label, room_b_label, width_m, height_m,
                                      cons_name="Interior_Glass_Door"):
    zone_a = resolve_zone_label(idf, room_a_label, floor_suffix="F1")
    zone_b = resolve_zone_label(idf, room_b_label, floor_suffix="F1")
    wall_a, wall_b = find_interior_wall_pair_between_zones(idf, zone_a, zone_b)
    cons = ensure_interior_glazing_construction(idf, cons_name=cons_name)
    win_a = add_centered_window_on_wall(idf, wall_a, width_m, height_m,
                                        name=f"{wall_a.Name}_INT_GLZ",
                                        construction=cons.Name)
    # align A window winding to parent
    nx_a, ny_a = surface_outward_normal_xy(wall_a)
    az_a = azimuth_deg_from_xy_normal(nx_a, ny_a)
    # clone to B
    win_b = idf.newidfobject("FENESTRATIONSURFACE:DETAILED")
    win_b.Name = f"{wall_b.Name}_INT_GLZ"
    win_b.Surface_Type = "Window"
    win_b.Construction_Name = cons.Name
    win_b.Building_Surface_Name = wall_b.Name
    for i in range(1,5):
        setattr(win_b, f"Vertex_{i}_Xcoordinate", getattr(win_a, f"Vertex_{i}_Xcoordinate"))
        setattr(win_b, f"Vertex_{i}_Ycoordinate", getattr(win_a, f"Vertex_{i}_Ycoordinate"))
        setattr(win_b, f"Vertex_{i}_Zcoordinate", getattr(win_a, f"Vertex_{i}_Zcoordinate"))
    # align B window winding to parent
    nx_b, ny_b = surface_outward_normal_xy(wall_b)
    az_b = azimuth_deg_from_xy_normal(nx_b, ny_b)

    def subsurface_azimuth_deg(sub):
        vs = []
        for i in range(1,5):
            x = getattr(sub, f"Vertex_{i}_Xcoordinate", None)
            y = getattr(sub, f"Vertex_{i}_Ycoordinate", None)
            z = getattr(sub, f"Vertex_{i}_Zcoordinate", None)
            if x is not None and y is not None and z is not None:
                vs.append((float(x), float(y), float(z)))
        if len(vs) < 3:
            return 0.0
        nx = ny = nz = 0.0
        n = len(vs)
        for i in range(n):
            x1,y1,z1 = vs[i]
            x2,y2,z2 = vs[(i+1)%n]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        ang = math.degrees(math.atan2(nx, ny))
        if ang < 0: ang += 360.0
        return ang

    # If window azimuths don't match parent, flip winding
    if abs((subsurface_azimuth_deg(win_a) - az_a + 180) % 360 - 180) > 1e-6:
        reverse_surface_winding(win_a)
    if abs((subsurface_azimuth_deg(win_b) - az_b + 180) % 360 - 180) > 1e-6:
        reverse_surface_winding(win_b)

    # Pair subsurfaces (INTERZONE)
    try:
        win_a.Outside_Boundary_Condition_Object = win_b.Name
        win_b.Outside_Boundary_Condition_Object = win_a.Name
        for w in (win_a, win_b):
            try: w.Sun_Exposure = "NoSun"
            except: pass
            try: w.Wind_Exposure = "NoWind"
            except: pass
    except Exception:
        pass
    return wall_a, wall_b, win_a, win_b

def add_rect_zone_at_ground(idf, zone_label, xy_vertices_cw, height):
    idf.add_block(zone_label, xy_vertices_cw, height, 1, 0.0, 0.0, 0.0)
    return f"Block {zone_label} Storey 0"

def lift_zone_surfaces_z(idf, zone_name_exact, dz):
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name == zone_name_exact:
            for i in range(1, 5):
                attr = f"Vertex_{i}_Zcoordinate"
                zval = getattr(s, attr, None)
                if zval is not None:
                    setattr(s, attr, zval + dz)
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = getattr(f, "Building_Surface_Name", "")
        if not parent:
            continue
        ps = next((s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"] if s.Name == parent), None)
        if ps and ps.Zone_Name == zone_name_exact:
            for i in range(1,5):
                attr = f"Vertex_{i}_Zcoordinate"
                zval = getattr(f, attr, None)
                if zval is not None:
                    setattr(f, attr, zval + dz)

def get_or_create_material(idf, name, rough, thick, k, rho, cp):
    mats = [m for m in idf.idfobjects["MATERIAL"] if (m.Name or "").lower()==name.lower()]
    if mats: return mats[0]
    return idf.newidfobject("MATERIAL", Name=name, Roughness=rough,
                            Thickness=thick, Conductivity=k, Density=rho, Specific_Heat=cp)

def get_or_create_construction(idf, cons_name, layer_defs):
    # layer_defs: [(name, rough, thick, k, rho, cp), ...] outside->inside
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower()==cons_name.lower()]
    if cons:
        return cons[0]
    mats = []
    for (name, rough, thick, k, rho, cp) in layer_defs:
        m = get_or_create_material(idf, name, rough, thick, k, rho, cp)
        mats.append(m.Name)
    c = idf.newidfobject("CONSTRUCTION", Name=cons_name)
    if mats:
        c.Outside_Layer = mats[0]
        for i, nm in enumerate(mats[1:], start=2):
            setattr(c, f"Layer_{i}", nm)
    return c

def classify_and_assign_walls(idf):
    EXT = "Exterior_Wall_Construction"
    INT = "Interior_Wall_Construction"
    ext_layers = [
        ("Ext_Brick",   "Rough",       0.200, 0.77, 1700.0, 840.0),
        ("Ext_Insul",   "MediumRough", 0.080, 0.035,  30.0, 1400.0),
        ("Int_Gypsum",  "Smooth",      0.012, 0.16, 800.0, 1090.0),
    ]
    int_layers = [
        #("Gypsum_12mm",   "Smooth",      0.012, 0.16, 800.0, 1090.0),
        ("Interior_Partition_100mm", "MediumRough", 0.100, 0.20, 600.0, 1000.0),
        #("Gypsum_12mm_2", "Smooth",      0.012, 0.16, 800.0, 1090.0),
    ]
    ext_cons = get_or_create_construction(idf, EXT, ext_layers)
    int_cons = get_or_create_construction(idf, INT, int_layers)

    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        obc = (s.Outside_Boundary_Condition or "").strip().lower()
        if obc == "outdoors":
            s.Construction_Name = ext_cons.Name
            try: s.Sun_Exposure = "SunExposed"
            except: pass
            try: s.Wind_Exposure = "WindExposed"
            except: pass
        elif obc == "surface":
            s.Construction_Name = int_cons.Name
            try: s.Sun_Exposure = "NoSun"
            except: pass
            try: s.Wind_Exposure = "NoWind"
            except: pass
        else:
            # leave others as-is
            pass

def flip_exterior_wall_normals(idf):
    fen_by_parent = {}
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = getattr(f, "Building_Surface_Name", "")
        fen_by_parent.setdefault(parent, []).append(f)
    count = 0
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        if (s.Outside_Boundary_Condition or "").strip().lower() != "outdoors":
            continue
        reverse_surface_winding(s)
        count += 1
        for f in fen_by_parent.get(s.Name, []):
            reverse_surface_winding(f)
    print(f"Flipped normals on {count} exterior walls (and attached fenestrations).")
def flip_wall_normals_by_obc(idf, obc_targets):
    """
    Reverse winding of wall surfaces whose Outside_Boundary_Condition
    matches one of obc_targets, and also reverse any already-attached
    fenestration/subsurfaces on those walls.
    """
    obc_targets = {str(x).strip().lower() for x in obc_targets}

    fen_by_parent = {}
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = getattr(f, "Building_Surface_Name", "")
        fen_by_parent.setdefault(parent, []).append(f)

    count = 0
    fen_count = 0

    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        obc = (s.Outside_Boundary_Condition or "").strip().lower()
        if obc not in obc_targets:
            continue

        reverse_surface_winding(s)
        count += 1

        for f in fen_by_parent.get(s.Name, []):
            reverse_surface_winding(f)
            fen_count += 1

    print(f"Flipped normals on {count} wall(s) with OBC={sorted(obc_targets)} "
          f"and {fen_count} attached fenestration object(s).")


def flip_exterior_wall_normals(idf):
    flip_wall_normals_by_obc(idf, {"outdoors"})


def flip_interior_wall_normals(idf):
    flip_wall_normals_by_obc(idf, {"surface"})
# -------------------------------------------------
# LangGraph node entrypoint
# -------------------------------------------------
def generate_idf_file(state: SimulationState) -> SimulationState:
    """
    Expect state['parsed_building_data'] with:
      floors (int), floor_height (m), orientation (deg)
      rooms: dict { room_name: [(x,y), ...], ... }
      windows_ext: dict { room_name: [ {'ori':'N|E|S|W','w':float,'h':float}, ...], ... }
      windows_int: list [ {'room_a':str,'room_b':str,'w':float,'h':float,'subtype':'Window'|'Door'}, ... ]
      out_idf (optional): write path
    """
    #%%
    out_idf_final = state.get("idf_path")  # comes from --idf-out via run_graph.py
    if not out_idf_final:
        out_idf_final = os.path.abspath(os.path.join(OUT_DIR, "geom_multiregion.idf"))
    state["idf_path"] = out_idf_final  # keep this stable for downstream nodes

    #%%
    parsed = state.get("parsed_building_data") or {}
    rooms: Dict[str, List[Tuple[float, float]]] = parsed.get("rooms", {})
    if not rooms:
        return {"errors": ["geomeppy_generator: 'rooms' missing or empty in parsed_building_data."]}

    floors = int(parsed.get("floors", 1))
    floor_h = float(parsed.get("floor_height", 2.5))
    orient = float(parsed.get("orientation", 0.0))
    windows_ext = parsed.get("windows_ext", {})
    windows_int = parsed.get("windows_int", [])
    out_idf = parsed.get("out_idf") or os.path.abspath(os.path.join(OUT_DIR, "geom_multizone.idf"))

    # Init IDF
    IDF.setiddname(IDD_PATH)
    idf = IDF(SEED_IDF)
    bldg = idf.idfobjects["BUILDING"][0]
    bldg.Name = "MultiZone_From_Node"
    bldg.North_Axis = orient

    # Build zones per floor (stacked) using exact approach
    created_zone_names = []
    for fidx in range(floors):
        base_z = fidx * floor_h
        for base_name, xy in rooms.items():
            label = f"{base_name}_F{fidx+1}"
            xy_cw = force_cw([(float(x), float(y)) for (x, y) in xy])
            zname = add_rect_zone_at_ground(idf, label, xy_cw, floor_h)
            if base_z > 0.0:
                lift_zone_surfaces_z(idf, zname, base_z)
            created_zone_names.append(zname)

    # working post-process order
    if hasattr(idf, "intersect_match"):
        idf.intersect_match()
    if hasattr(idf, "set_default_constructions"):
        idf.set_default_constructions()
    if hasattr(idf, "set_wwr"):
        idf.set_wwr(0.0)
    
    fix_floor_roof_winding(idf)
    
    # Constructions for interior/exterior walls
    classify_and_assign_walls(idf)
    
    # Force wall-facing direction defaults
    flip_interior_wall_normals(idf)   # NEW
    flip_exterior_wall_normals(idf)   # keep existing behavior

    # Exterior windows by room + cardinal
    for room, specs in (windows_ext or {}).items():
        for spec in specs:
            ori = (spec.get("ori") or spec.get("orientation") or "S").upper()
            w = float(spec.get("w") or spec.get("width") or 1.5)
            h = float(spec.get("h") or spec.get("height") or 1.5)
            try:
                add_exterior_window_by_orientation(idf, room, ori, w, h, cons_name="Simple_DoublePane")
            except Exception as e:
                print(f"[Window ext warn] {room} {ori}: {e}")

    # Interior glazing pairs
    for ig in (windows_int or []):
        ra = ig.get("room_a"); rb = ig.get("room_b")
        w = float(ig.get("w") or ig.get("width") or 1.0)
        h = float(ig.get("h") or ig.get("height") or 2.0)
        if not (ra and rb):
            continue
        try:
            add_interior_window_between_rooms(idf, ra, rb, w, h, cons_name="Interior_Glass_Door")
        except Exception as e:
            print(f"[Window int warn] {ra}↔{rb}: {e}")
    #%%
    # Save and return
    #idf.saveas(out_idf)
    #state["idf_path"] = out_idf
    # (Optionally) stash breadcrumbs
    state.setdefault("simulation_params", {})
    state["simulation_params"]["zones_created"] = created_zone_names
    
    idf.saveas(out_idf_final)
    #print("[GEN] Saved IDF →", out_idf_final)
    #%%
    #==================================================================#
    # --- Rotate the entire geometry by ROTATE_DEG at the end ---
    ROTATE_DEG = parsed['orientation'] * -1
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
                # rotate about origin (fine if footprint starts at (0,0))
                idf.rotate(ROTATE_DEG)
            rotated = True
            print(f"↻ Rotated geometry by {ROTATE_DEG}°")
        except Exception as e:
            print(f"⚠️ idf.rotate failed: {e}")
    if not rotated:
        try:
            _rotate_xy_vertices(idf, ROTATE_DEG, origin_xy=(cx, cy))
            rotated = True
            #print(f"↻ Rotated geometry manually by {ROTATE_DEG}° about centroid ({cx:.3f}, {cy:.3f})")
        except Exception as e:
            print(f"⚠️ Manual rotation failed: {e}")      
            
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'preview_3d')) #3D_png


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
    #%%
    
    return state
