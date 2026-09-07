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
    """Extract vertices from a surface via raw IDF fields."""
    verts = []
    for i in range(1, 11):
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
            xs += x
            ys += y
            n += 1
    return (xs / n, ys / n) if n else (0.0, 0.0)


def _rotate_xy_vertices(idf, angle_deg, origin_xy):
    """Manual geometry rotation for versions without idf.rotate()."""
    cx, cy = origin_xy
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)

    def _rot(x, y):
        x0, y0 = x - cx, y - cy
        return (x0 * c - y0 * s + cx, x0 * s + y0 * c + cy)

    for key in [
        "BUILDINGSURFACE:DETAILED",
        "FENESTRATIONSURFACE:DETAILED",
        "SHADING:ZONE:DETAILED",
        "SHADING:BUILDING:DETAILED",
    ]:
        for obj in _getobjects(idf, key):
            for i in range(1, 11):
                x_name = f"Vertex_{i}_Xcoordinate"
                y_name = f"Vertex_{i}_Ycoordinate"
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
    for sc in list(idf.idfobjects["SIMULATIONCONTROL"]):
        idf.removeidfobject(sc)

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
    for i in range(1, 11):
        x = getattr(s, f"Vertex_{i}_Xcoordinate", None)
        y = getattr(s, f"Vertex_{i}_Ycoordinate", None)
        z = getattr(s, f"Vertex_{i}_Zcoordinate", None)
        if x is None or y is None or z is None:
            break
        try:
            vs.append((float(x), float(y), float(z)))
        except (TypeError, ValueError):
            break
    return vs


def _surface_centroid(vs):
    n = max(len(vs), 1)
    sx = sum(v[0] for v in vs)
    sy = sum(v[1] for v in vs)
    sz = sum(v[2] for v in vs)
    return (sx / n, sy / n, sz / n)


def _polygon_normal(vs):
    nx = ny = nz = 0.0
    n = len(vs)
    for i in range(n):
        x1, y1, z1 = vs[i]
        x2, y2, z2 = vs[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return (nx, ny, nz)


def _polygon_normal_z(vs):
    return _polygon_normal(vs)[2]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vec(a, b):
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def reverse_surface_winding(surface):
    verts = _surface_vertices(surface)
    if not verts:
        return
    _set_surface_vertices(surface, list(reversed(verts)))


def _set_surface_vertices(surface, verts):
    for idx, (x, y, z) in enumerate(verts, start=1):
        setattr(surface, f"Vertex_{idx}_Xcoordinate", x)
        setattr(surface, f"Vertex_{idx}_Ycoordinate", y)
        setattr(surface, f"Vertex_{idx}_Zcoordinate", z)
    for j in range(len(verts) + 1, 11):
        x_attr = f"Vertex_{j}_Xcoordinate"
        y_attr = f"Vertex_{j}_Ycoordinate"
        z_attr = f"Vertex_{j}_Zcoordinate"
        if not hasattr(surface, x_attr):
            break
        try:
            setattr(surface, x_attr, "")
            setattr(surface, y_attr, "")
            setattr(surface, z_attr, "")
        except Exception:
            break


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


def _zone_surfaces(idf, zone_name):
    return [s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"] if (s.Zone_Name or "") == zone_name]


def _zone_centroid(idf, zone_name):
    verts = []
    for s in _zone_surfaces(idf, zone_name):
        verts.extend(_surface_vertices(s))
    if not verts:
        return (0.0, 0.0, 0.0)
    return _surface_centroid(verts)


def orient_zone_surfaces_outward(idf, tol=1e-9):
    """
    Re-orient every zone surface so that the polygon normal points outward from the zone.
    This is the key fix for negative-zone-volume issues caused by inward interior walls.
    """
    fixed = 0
    for z in idf.idfobjects["ZONE"]:
        zname = z.Name
        zc = _zone_centroid(idf, zname)
        for s in _zone_surfaces(idf, zname):
            vs = _surface_vertices(s)
            if len(vs) < 3:
                continue
            n = _polygon_normal(vs)
            sc = _surface_centroid(vs)
            outward_test = _dot(n, _vec(zc, sc))
            if outward_test < -tol:
                reverse_surface_winding(s)
                fixed += 1
    print(f"Re-oriented {fixed} zone surfaces to outward normals.")
    return fixed


def _rounded_vertex_key(v, tol=1e-6):
    scale = max(int(round(1.0 / tol)), 1)
    return tuple(int(round(coord * scale)) for coord in v)


def _same_vertex_set(vs_a, vs_b, tol=1e-6):
    if len(vs_a) != len(vs_b):
        return False
    aa = sorted(_rounded_vertex_key(v, tol) for v in vs_a)
    bb = sorted(_rounded_vertex_key(v, tol) for v in vs_b)
    return aa == bb


def fix_interzone_surface_pairs(idf, tol=1e-6):
    """
    For each matched interzone wall pair:
    1) ensure the two normals oppose each other,
    2) if they represent the same geometric polygon, force one side to be the exact reverse
       of the other side.
    """
    by_name = {s.Name: s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]}
    visited = set()
    fixed = 0

    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        if (s.Outside_Boundary_Condition or "").strip().lower() != "surface":
            continue

        mate_name = getattr(s, "Outside_Boundary_Condition_Object", "")
        if not mate_name or mate_name not in by_name:
            continue
        pair_key = tuple(sorted([s.Name, mate_name]))
        if pair_key in visited:
            continue
        visited.add(pair_key)

        mate = by_name[mate_name]
        if getattr(mate, "Outside_Boundary_Condition_Object", "") != s.Name:
            continue

        vs_a = _surface_vertices(s)
        vs_b = _surface_vertices(mate)
        if len(vs_a) < 3 or len(vs_b) < 3:
            continue

        na = _polygon_normal(vs_a)
        nb = _polygon_normal(vs_b)
        if _dot(na, nb) > 0:
            reverse_surface_winding(mate)
            vs_b = _surface_vertices(mate)
            fixed += 1

        if _same_vertex_set(vs_a, vs_b, tol=tol):
            _set_surface_vertices(mate, list(reversed(vs_a)))
            fixed += 1

    print(f"Fixed {fixed} interzone wall-pair winding issues.")
    return fixed


def surface_outward_normal_xy(surface):
    vs = _surface_vertices(surface)
    if len(vs) < 3:
        return (0.0, 0.0)
    nx, ny, _nz = _polygon_normal(vs)
    return (nx, ny)


def azimuth_deg_from_xy_normal(nx, ny):
    """EnergyPlus azimuth convention: 0=N(+Y), clockwise positive."""
    ang = math.degrees(math.atan2(nx, ny))
    if ang < 0:
        ang += 360.0
    return ang


def cardinal_of_azimuth(az):
    bins = [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (360, "N")]
    diffs = [(abs((az - b + 180) % 360 - 180), c) for b, c in bins]
    return min(diffs, key=lambda t: t[0])[1]


def pick_exterior_wall(idf, zone_name, target_cardinal="S"):
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
        target_map = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
        diff = abs((az - target_map[target_cardinal.upper()] + 180) % 360 - 180)
        candidates.append((diff, az, s))
    if not candidates:
        raise ValueError(f"No exterior wall in zone '{zone_name}' for {target_cardinal}.")
    candidates.sort(key=lambda t: t[0])
    return candidates[0][2]


def _orient_subsurface_like_parent(subsurface, parent_surface):
    pvs = _surface_vertices(parent_surface)
    svs = _surface_vertices(subsurface)
    if len(pvs) < 3 or len(svs) < 3:
        return
    pn = _polygon_normal(pvs)
    sn = _polygon_normal(svs)
    if _dot(pn, sn) < 0:
        reverse_surface_winding(subsurface)


def reorient_fenestrations_to_parents(idf):
    parent_by_name = {s.Name: s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]}
    fixed = 0
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent_name = getattr(f, "Building_Surface_Name", "")
        parent = parent_by_name.get(parent_name)
        if not parent:
            continue
        before = _surface_vertices(f)
        _orient_subsurface_like_parent(f, parent)
        after = _surface_vertices(f)
        if before != after:
            fixed += 1
    print(f"Re-oriented {fixed} fenestration surfaces to match parent winding.")
    return fixed


def add_centered_window_on_wall(idf, wall_surface, win_w, win_h, name=None, construction=""):
    vs = _surface_vertices(wall_surface)
    if len(vs) < 4:
        raise ValueError("Wall surface is not quadrilateral.")

    vs_sorted = sorted(vs, key=lambda p: p[2])
    pA, pB = vs_sorted[0], vs_sorted[1]
    pC, pD = vs_sorted[2], vs_sorted[3]
    A = np.array(pA)
    B = np.array(pB)
    C = np.array(pC)
    D = np.array(pD)

    u = B - A
    u[2] = 0.0
    ulen = np.linalg.norm(u) or 1.0
    u = u / ulen

    v = ((C + D) / 2.0 - (A + B) / 2.0)
    v[0] = 0.0
    v[1] = 0.0
    vlen = np.linalg.norm(v) or 1.0
    v = v / vlen

    center = (A + B + C + D) / 4.0
    hw, hh = win_w / 2.0, win_h / 2.0
    p1 = center - hw * u - hh * v
    p2 = center + hw * u - hh * v
    p3 = center + hw * u + hh * v
    p4 = center - hw * u + hh * v

    wname = name or (wall_surface.Name + "_Win")
    win = idf.newidfobject("FENESTRATIONSURFACE:DETAILED")
    win.Name = wname
    win.Surface_Type = "Window"
    if construction:
        win.Construction_Name = construction
    win.Building_Surface_Name = wall_surface.Name

    _set_surface_vertices(win, [tuple(p1), tuple(p2), tuple(p3), tuple(p4)])
    _orient_subsurface_like_parent(win, wall_surface)
    return win


def ensure_interior_glazing_construction(idf, cons_name="Interior_Glass_Door"):
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower() == cons_name.lower()]
    if cons:
        return cons[0]
    glz = idf.newidfobject(
        "WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
        Name="INT_SG_2p2",
        UFactor=2.2,
        Solar_Heat_Gain_Coefficient=0.65,
        Visible_Transmittance=0.75,
    )
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
        out.add(re.sub(r"(\D)(\d+)$", r"\1_\2", s0))
        return {v + "_" + floor_suffix if "_F" not in v.upper() else v for v in out}

    want = {v.lower() for v in variants(user_label)}
    for z in idf.idfobjects["ZONE"]:
        base = z.Name.replace("Block ", "").replace(" Storey 0", "")
        if base.lower() in want:
            return z.Name
    raise ValueError(
        f"Could not resolve zone for label '{user_label}'. Available: {[z.Name for z in idf.idfobjects['ZONE']]}"
    )


def add_exterior_window_by_orientation(idf, user_room_label, cardinal, width_m, height_m,
                                       cons_name="Simple_DoublePane"):
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower() == cons_name.lower()]
    if not cons:
        glz = idf.newidfobject(
            "WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM",
            Name="SG_2p0",
            UFactor=2.0,
            Solar_Heat_Gain_Coefficient=0.6,
            Visible_Transmittance=0.7,
        )
        cc = idf.newidfobject("CONSTRUCTION", Name=cons_name)
        cc.Outside_Layer = glz.Name

    zone_name = resolve_zone_label(idf, user_room_label, floor_suffix="F1")
    wall = pick_exterior_wall(idf, zone_name, target_cardinal=cardinal.upper())
    win = add_centered_window_on_wall(
        idf,
        wall,
        width_m,
        height_m,
        name=f"{zone_name}_{cardinal}_Win",
        construction=cons_name,
    )
    return wall, win


def find_interior_wall_pair_between_zones(idf, zone_a_name, zone_b_name):
    walls = [
        s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]
        if (s.Surface_Type or "").strip().lower() == "wall"
        and (s.Outside_Boundary_Condition or "").strip().lower() == "surface"
    ]
    walls_a = [s for s in walls if (s.Zone_Name or "") == zone_a_name]
    by_name = {s.Name: s for s in walls if (s.Zone_Name or "") == zone_b_name}
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
        if len(vs) < 4:
            return 0.0
        a, b, c, d = map(np.array, vs[:4])
        u = b - a
        v = d - a
        return float(np.linalg.norm(np.cross(u, v)))

    pairs.sort(key=lambda p: area4(p[0]), reverse=True)
    return pairs[0]


def add_interior_window_between_rooms(idf, room_a_label, room_b_label, width_m, height_m,
                                      cons_name="Interior_Glass_Door"):
    zone_a = resolve_zone_label(idf, room_a_label, floor_suffix="F1")
    zone_b = resolve_zone_label(idf, room_b_label, floor_suffix="F1")
    wall_a, wall_b = find_interior_wall_pair_between_zones(idf, zone_a, zone_b)
    cons = ensure_interior_glazing_construction(idf, cons_name=cons_name)

    win_a = add_centered_window_on_wall(
        idf,
        wall_a,
        width_m,
        height_m,
        name=f"{wall_a.Name}_INT_GLZ",
        construction=cons.Name,
    )

    win_b = idf.newidfobject("FENESTRATIONSURFACE:DETAILED")
    win_b.Name = f"{wall_b.Name}_INT_GLZ"
    win_b.Surface_Type = "Window"
    win_b.Construction_Name = cons.Name
    win_b.Building_Surface_Name = wall_b.Name

    _set_surface_vertices(win_b, list(reversed(_surface_vertices(win_a))))
    _orient_subsurface_like_parent(win_a, wall_a)
    _orient_subsurface_like_parent(win_b, wall_b)

    try:
        win_a.Outside_Boundary_Condition_Object = win_b.Name
        win_b.Outside_Boundary_Condition_Object = win_a.Name
        for w in (win_a, win_b):
            try:
                w.Sun_Exposure = "NoSun"
            except Exception:
                pass
            try:
                w.Wind_Exposure = "NoWind"
            except Exception:
                pass
    except Exception:
        pass
    return wall_a, wall_b, win_a, win_b


def add_rect_zone_at_ground(idf, zone_label, xy_vertices_cw, height):
    idf.add_block(zone_label, xy_vertices_cw, height, 1, 0.0, 0.0, 0.0)
    return f"Block {zone_label} Storey 0"


def lift_zone_surfaces_z(idf, zone_name_exact, dz):
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Zone_Name == zone_name_exact:
            for i in range(1, 11):
                attr = f"Vertex_{i}_Zcoordinate"
                zval = getattr(s, attr, None)
                if zval is None:
                    break
                try:
                    setattr(s, attr, float(zval) + dz)
                except (TypeError, ValueError):
                    break

    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = getattr(f, "Building_Surface_Name", "")
        if not parent:
            continue
        ps = next((s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"] if s.Name == parent), None)
        if ps and ps.Zone_Name == zone_name_exact:
            for i in range(1, 11):
                attr = f"Vertex_{i}_Zcoordinate"
                zval = getattr(f, attr, None)
                if zval is None:
                    break
                try:
                    setattr(f, attr, float(zval) + dz)
                except (TypeError, ValueError):
                    break


def get_or_create_material(idf, name, rough, thick, k, rho, cp):
    mats = [m for m in idf.idfobjects["MATERIAL"] if (m.Name or "").lower() == name.lower()]
    if mats:
        return mats[0]
    return idf.newidfobject(
        "MATERIAL",
        Name=name,
        Roughness=rough,
        Thickness=thick,
        Conductivity=k,
        Density=rho,
        Specific_Heat=cp,
    )


def get_or_create_construction(idf, cons_name, layer_defs):
    cons = [c for c in idf.idfobjects["CONSTRUCTION"] if (c.Name or "").lower() == cons_name.lower()]
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
        ("Ext_Brick", "Rough", 0.200, 0.77, 1700.0, 840.0),
        ("Ext_Insul", "MediumRough", 0.080, 0.035, 30.0, 1400.0),
        ("Int_Gypsum", "Smooth", 0.012, 0.16, 800.0, 1090.0),
    ]
    int_layers = [
        ("Gypsum_12mm", "Smooth", 0.012, 0.16, 800.0, 1090.0),
        ("PartitionCore", "MediumRough", 0.100, 0.20, 600.0, 1000.0),
        ("Gypsum_12mm_2", "Smooth", 0.012, 0.16, 800.0, 1090.0),
    ]
    ext_cons = get_or_create_construction(idf, EXT, ext_layers)
    int_cons = get_or_create_construction(idf, INT, int_layers)

    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if (s.Surface_Type or "").strip().lower() != "wall":
            continue
        obc = (s.Outside_Boundary_Condition or "").strip().lower()
        if obc == "outdoors":
            s.Construction_Name = ext_cons.Name
            try:
                s.Sun_Exposure = "SunExposed"
            except Exception:
                pass
            try:
                s.Wind_Exposure = "WindExposed"
            except Exception:
                pass
        elif obc == "surface":
            s.Construction_Name = int_cons.Name
            try:
                s.Sun_Exposure = "NoSun"
            except Exception:
                pass
            try:
                s.Wind_Exposure = "NoWind"
            except Exception:
                pass


def flip_exterior_wall_normals(idf):
    """
    Kept for backward compatibility, but no longer called in the main flow.
    The new zone-wise orientation pass handles both exterior and interior walls robustly.
    """
    fen_by_parent = defaultdict(list)
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        parent = getattr(f, "Building_Surface_Name", "")
        fen_by_parent[parent].append(f)
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
    out_idf_final = state.get("idf_path")
    if not out_idf_final:
        out_idf_final = os.path.abspath(os.path.join(OUT_DIR, "geom_multiregion.idf"))
    state["idf_path"] = out_idf_final

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

    IDF.setiddname(IDD_PATH)
    idf = IDF(SEED_IDF)
    bldg = idf.idfobjects["BUILDING"][0]
    bldg.Name = "MultiZone_From_Node"
    bldg.North_Axis = orient

    created_zone_names = []
    for fidx in range(floors):
        base_z = fidx * floor_h
        for base_name, xy in rooms.items():
            label = f"{base_name}_F{fidx + 1}"
            xy_cw = force_cw([(float(x), float(y)) for (x, y) in xy])
            zname = add_rect_zone_at_ground(idf, label, xy_cw, floor_h)
            if base_z > 0.0:
                lift_zone_surfaces_z(idf, zname, base_z)
            created_zone_names.append(zname)

    if hasattr(idf, "intersect_match"):
        idf.intersect_match()
    if hasattr(idf, "set_default_constructions"):
        idf.set_default_constructions()
    if hasattr(idf, "set_wwr"):
        idf.set_wwr(0.0)

    classify_and_assign_walls(idf)

    # Critical geometry fix sequence.
    fix_floor_roof_winding(idf)
    orient_zone_surfaces_outward(idf)
    fix_interzone_surface_pairs(idf)
    fix_floor_roof_winding(idf)

    for room, specs in (windows_ext or {}).items():
        for spec in specs:
            ori = (spec.get("ori") or spec.get("orientation") or "S").upper()
            w = float(spec.get("w") or spec.get("width") or 1.5)
            h = float(spec.get("h") or spec.get("height") or 1.5)
            try:
                add_exterior_window_by_orientation(idf, room, ori, w, h, cons_name="Simple_DoublePane")
            except Exception as e:
                print(f"[Window ext warn] {room} {ori}: {e}")

    for ig in (windows_int or []):
        ra = ig.get("room_a")
        rb = ig.get("room_b")
        w = float(ig.get("w") or ig.get("width") or 1.0)
        h = float(ig.get("h") or ig.get("height") or 2.0)
        if not (ra and rb):
            continue
        try:
            add_interior_window_between_rooms(idf, ra, rb, w, h, cons_name="Interior_Glass_Door")
        except Exception as e:
            print(f"[Window int warn] {ra}↔{rb}: {e}")

    reorient_fenestrations_to_parents(idf)
    fix_interzone_surface_pairs(idf)

    state.setdefault("simulation_params", {})
    state["simulation_params"]["zones_created"] = created_zone_names

    idf.saveas(out_idf_final)

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
        except Exception as e:
            print(f"⚠️ Manual rotation failed: {e}")

    # Keep final orientation clean after rotation.
    reorient_fenestrations_to_parents(idf)
    idf.saveas(out_idf_final)

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

    return state
