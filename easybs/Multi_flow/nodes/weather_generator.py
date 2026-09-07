# -*- coding: utf-8 -*-
"""
Created on Mon Oct 05 11:58:21 2025

@author: user
"""
# nodes/weather_generator.py
from __future__ import annotations

import json
import math
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from urllib.parse import urljoin, urlparse, urlunparse, quote_plus

import pandas as pd
import requests
from tqdm import tqdm

# Optional libs (geocoding / distance / IDF edits)
try:
    from geopy.geocoders import Nominatim
except Exception:
    Nominatim = None

try:
    from haversine import haversine as _haversine
except Exception:
    _haversine = None

try:
    from geomeppy import IDF
except Exception:
    IDF = None

# ----------------------- Config -----------------------
CACHE_DIR = Path(".epw_cache"); CACHE_DIR.mkdir(exist_ok=True)
SPREADSHEET_DIR = CACHE_DIR / "region_xlsx"; SPREADSHEET_DIR.mkdir(exist_ok=True)
DOWNLOAD_TIMEOUT = 60
USER_AGENT = "LangGraph-weather-generator/1.0"
DEFAULT_CITY = "Seoul, South Korea"

# Your default IDF path (user-provided)
DEFAULT_IDF = r"D:\xiguan_liang\Paper_LLMs\langgraph_flow\generated_idfs\geom_bui_E+_modified.idf"

# Your adjusted OneBuilding XLSX links (based on your working list)
ONEBUILDING_TMYX_XLSX = [
    "https://climate.onebuilding.org/sources/Region1_Africa_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region2_Asia_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region3_South_America_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region5_Southwest_Pacific_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region6_Europe_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region7_Antarctica_TMYx_EPW_Processing_locations.xlsx",
    # Adjusted R4 sheet names you mentioned:
    "https://climate.onebuilding.org/sources/Region4_USA_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region4_Canada_TMYx_EPW_Processing_locations.xlsx",
    "https://climate.onebuilding.org/sources/Region4_NA_CA_Caribbean_TMYx_EPW_Processing_locations.xlsx",
]

# Prefer newer windows first for pick
PREFERRED_SUFFIX_ORDER = [
    ".TMYx.2009-2023",
    ".TMYx.2007-2021",
    ".TMYx.2004-2018",
    ".TMYx",
    ".RMY2024",
]

# ----------------------- Utilities -----------------------
def log(msg: str):
    print(f"[weather-generator] {msg}", file=sys.stderr)

def _hv_km(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    if _haversine:
        return float(_haversine(a, b))
    # fallback haversine
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    R = 6371.0
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def sanitize_download_url(u: str) -> str:
    if not u:
        return u
    low = u.lower().rstrip("/")
    if low.endswith(".zip") or low.endswith(".epw"):
        return u.rstrip("/")
    u2 = u.rstrip("/")
    low2 = u2.lower()
    if low2.endswith(".zip") or low2.endswith(".epw"):
        return u2
    parts = list(urlparse(u))
    parts[2] = parts[2].rstrip("/")
    return urlunparse(parts)

def is_file_url(u: str) -> bool:
    if not u: 
        return False
    low = u.lower().rstrip("/")
    return low.endswith(".zip") or low.endswith(".epw")

# ----------------------- Geocode -----------------------
def geocode_city(city: str) -> Tuple[float, float, Dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT}
    if Nominatim:
        geolocator = Nominatim(user_agent=USER_AGENT)
        loc = geolocator.geocode(city, timeout=10)
        if not loc:
            raise RuntimeError(f"Could not geocode city: {city}")
        return float(loc.latitude), float(loc.longitude), loc.raw
    # HTTP fallback
    url = "https://nominatim.openstreetmap.org/search"
    r = requests.get(url, params={"q": city, "format": "json", "limit": 1}, headers=headers, timeout=DOWNLOAD_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise RuntimeError(f"Could not geocode city: {city}")
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0]

# ----------------------- OneBuilding loaders -----------------------
def download_spreadsheet(url: str) -> Optional[Path]:
    name = url.split("/")[-1]
    if not name.endswith(".xlsx"):
        name += ".xlsx"
    local = SPREADSHEET_DIR / name
    if local.exists() and local.stat().st_size > 0:
        return local
    try:
        log(f"Fetching index: {url}")
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=DOWNLOAD_TIMEOUT)
        r.raise_for_status()
        local.write_bytes(r.content)
        return local
    except Exception as e:
        log(f"  Warning: couldn't download {url} ({e})")
        return None

def load_all_regions() -> pd.DataFrame:
    frames = []
    for url in ONEBUILDING_TMYX_XLSX:
        p = download_spreadsheet(url)
        if not p:
            continue
        try:
            df = pd.read_excel(p, engine="openpyxl")
            cols = {c.lower(): c for c in df.columns}
            lat_col = next((cols[k] for k in cols if "lat" in k), None)
            lon_col = next((cols[k] for k in cols if "lon" in k), None)
            url_col = next((cols[k] for k in cols if "url" in k or "link" in k), None)
            name_col = next((cols[k] for k in cols if "name" in k or "location" in k or "city" in k), None)
            country_col = next((cols[k] for k in cols if "country" in k), None)
            file_col = next((cols[k] for k in cols if "file" in k and "epw" in k.lower()), None)
            if not (lat_col and lon_col and url_col):
                log(f"  Missing lat/lon/url in {p.name}; skipping.")
                continue
            df2 = pd.DataFrame({
                "lat": pd.to_numeric(df[lat_col], errors="coerce"),
                "lon": pd.to_numeric(df[lon_col], errors="coerce"),
                "url": df[url_col].astype(str),
                "name": df[name_col].astype(str) if name_col else "",
                "country": df[country_col].astype(str) if country_col else "",
                "epw_file": df[file_col].astype(str) if file_col else "",
            }).dropna(subset=["lat", "lon"])
            frames.append(df2)
        except Exception as e:
            log(f"  Could not parse {p.name} ({e}); skipping.")
    if not frames:
        raise RuntimeError("Failed to load any OneBuilding region spreadsheets.")
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["lat", "lon", "url"])

def choose_best_candidate(all_df: pd.DataFrame, city_lat: float, city_lon: float, max_km: float) -> pd.Series:
    pts = list(zip(all_df["lat"], all_df["lon"]))
    dists = [_hv_km((city_lat, city_lon), (a, b)) for (a, b) in pts]
    df = all_df.copy()
    df["dist_km"] = dists
    df = df.sort_values("dist_km")
    # Prefer explicit epw filename with preferred suffix; else closest
    for _, row in df.iterrows():
        if row["dist_km"] > max_km:
            break
        epw_file = str(row.get("epw_file") or "")
        if epw_file:
            for suf in PREFERRED_SUFFIX_ORDER:
                if suf.lower() in epw_file.lower():
                    return row
            return row
    return df.iloc[0]

# ----------------------- File listing / download -----------------------
def list_directory_files(url: str):
    base = url if url.endswith("/") else url + "/"
    r = requests.get(base, headers={"User-Agent": USER_AGENT}, timeout=DOWNLOAD_TIMEOUT)
    r.raise_for_status()
    html = r.text
    hrefs = re.findall(r'href=[\'"]([^\'"]+)[\'"]', html, flags=re.I)
    outs = []
    for h in hrefs:
        if not h or h.startswith("?") or h.startswith("/"):
            continue
        h = h.split("?")[0].strip()
        if h.lower().endswith((".zip/", ".epw/")):
            h = h[:-1]
        if not (h.lower().endswith(".zip") or h.lower().endswith(".epw")):
            continue
        abs_url = sanitize_download_url(urljoin(base, h))
        outs.append((os.path.basename(h.rstrip("/")), abs_url))
    return outs

def _score_suffix(name: str) -> int:
    for i, suf in enumerate(PREFERRED_SUFFIX_ORDER):
        if suf.lower() in name.lower():
            return i
    return len(PREFERRED_SUFFIX_ORDER) + 1

def pick_best_download(links):
    zips = [(n, u) for n, u in links if n.lower().endswith(".zip")]
    epws = [(n, u) for n, u in links if n.lower().endswith(".epw")]
    if zips:
        zips.sort(key=lambda x: (_score_suffix(x[0]), x[0]))
        return zips[0]
    if epws:
        epws.sort(key=lambda x: (_score_suffix(x[0]), x[0]))
        return epws[0]
    return None

def download_file(url: str, outdir: Path) -> Path:
    u = sanitize_download_url(url)
    outdir.mkdir(parents=True, exist_ok=True)
    local = outdir / u.split("/")[-1]
    if local.exists() and local.stat().st_size > 0:
        return local

    def _stream_to(path: Path, resp: requests.Response):
        total = int(resp.headers.get("content-length", 0))
        with open(path, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=path.name) as pbar:
            for chunk in resp.iter_content(chunk_size=128 * 1024):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))

    try:
        with requests.get(u, stream=True, headers={"User-Agent": USER_AGENT}, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            _stream_to(local, r)
        return local
    except requests.HTTPError:
        candidate = u.rstrip("/")
        if candidate != u:
            log(f"Retrying without trailing slash: {candidate}")
            with requests.get(candidate, stream=True, headers={"User-Agent": USER_AGENT}, timeout=DOWNLOAD_TIMEOUT) as r2:
                r2.raise_for_status()
                _stream_to(local, r2)
            return local
        raise

def extract_epw_from_zip(zip_path: Path, outdir: Path) -> Path:
    with zipfile.ZipFile(zip_path, "r") as z:
        names = [n for n in z.namelist() if n.lower().endswith(".epw")]
        if not names:
            raise RuntimeError("No .epw found inside ZIP.")
        names.sort(key=lambda n: (_score_suffix(n), n))
        pick = names[0]
        target = outdir / Path(pick).name
        with z.open(pick) as src, open(target, "wb") as dst:
            dst.write(src.read())
        return target

# ----------------------- EPW header / IDF update -----------------------
def parse_epw_header(epw_path: Path) -> dict:
    with open(epw_path, "r", encoding="utf-8", errors="ignore") as f:
        first = f.readline().strip()
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 10 or parts[0].upper() != "LOCATION":
        raise RuntimeError("Unexpected EPW header format (no LOCATION line).")
    return {
        "city": parts[1],
        "state": parts[2],
        "country": parts[3],
        "source": parts[4],
        "wmo": parts[5],
        "latitude": float(parts[6]),
        "longitude": float(parts[7]),
        "timezone": float(parts[8]),
        "elevation": float(parts[9]),
        "source_line": first,
    }

def _country_token(country: str) -> str:
    token = (country or "").strip()
    if " " in token:
        token = token.replace(" ", "_").upper()
    return token or "UNK"

def _upper_ascii(s: str) -> str:
    return (s or "").upper().replace(" ", "_")

def _fmt_float(v: float, ndigits: int = 3) -> float:
    try:
        return round(float(v), ndigits)
    except Exception:
        return v

def _fmt_tz(v: float):
    try:
        f = float(v)
        if abs(f - round(f)) < 1e-6:
            return int(round(f))
        return f
    except Exception:
        return v

def _backup_idf(idf_path: Path) -> Optional[Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = idf_path.with_suffix(f".bak_{ts}.idf")
    try:
        bak.write_bytes(idf_path.read_bytes())
        return bak
    except Exception:
        return None

def set_idf_location(idf_path: Path, meta: dict, epw_path: Path) -> Path:
    if IDF is None:
        raise RuntimeError("geomeppy is not installed (pip install geomeppy).")

    _backup_idf(idf_path)
    idf = IDF(str(idf_path))
    try:
        site = idf.idfobjects["SITE:LOCATION"][0]
    except Exception:
        site = idf.newidfobject("SITE:LOCATION")

    city_tok = _upper_ascii(meta.get("city", ""))
    ctry_tok = _country_token(meta.get("country", ""))
    wmo = (meta.get("wmo") or "").strip()
    name = f"{city_tok}_{ctry_tok}_WMO_{wmo}" if wmo else f"{city_tok}_{ctry_tok}"

    site.Name      = name
    site.Latitude  = _fmt_float(meta["latitude"], ndigits=3)
    site.Longitude = _fmt_float(meta["longitude"], ndigits=3)
    site.Time_Zone = _fmt_tz(meta["timezone"])
    site.Elevation = _fmt_float(meta["elevation"], ndigits=0)

    idf.save()

    sidecar = idf_path.with_suffix(".weather.json")
    json.dump({"epw": str(epw_path.resolve())}, open(sidecar, "w"), indent=2)
    log("Updated SITE:LOCATION:")
    log(f"  {site.Name}, {site.Latitude}, {site.Longitude}, {site.Time_Zone}, {site.Elevation}")
    return sidecar

# ----------------------- Orchestration -----------------------
def obtain_epw_for_city(city: str, outdir: Path, max_km: float = 400.0) -> Tuple[Path, dict, dict]:
    log(f"Geocoding: {city}")
    lat, lon, _ = geocode_city(city)
    log(f"  -> {lat:.5f}, {lon:.5f}")

    log("Loading OneBuilding station indexes...")
    all_df = load_all_regions()
    log(f"  Loaded {len(all_df):,} station entries.")

    best = choose_best_candidate(all_df, lat, lon, max_km=max_km)
    url_raw = str(best["url"]).strip()
    epw_file_hint = str(best.get("epw_file", "") or "").strip()

    # Direct file?
    if is_file_url(url_raw):
        file_url = sanitize_download_url(url_raw)
        log(f"Direct file URL: {file_url}")
        dl = download_file(file_url, outdir)
        epw_path = extract_epw_from_zip(dl, outdir) if dl.suffix.lower() == ".zip" else dl
    else:
        base_url = url_raw.rstrip("/") + "/"
        log(f"Station directory: {base_url}")

        if epw_file_hint:
            hinted = sanitize_download_url(urljoin(base_url, epw_file_hint))
            try:
                log(f"Trying hinted file: {hinted}")
                dl = download_file(hinted, outdir)
                epw_path = extract_epw_from_zip(dl, outdir) if dl.suffix.lower() == ".zip" else dl
            except Exception as e:
                log(f"Hinted file failed ({e}); falling back to listing...")
                links = list_directory_files(base_url)
                if not links:
                    raise RuntimeError("No downloadable files at station page.")
                pick = pick_best_download(links)
                if not pick:
                    raise RuntimeError("No .zip or .epw found at station page.")
                _, href = pick
                dl = download_file(href, outdir)
                epw_path = extract_epw_from_zip(dl, outdir) if dl.suffix.lower() == ".zip" else dl
        else:
            links = list_directory_files(base_url)
            if not links:
                raise RuntimeError("No downloadable files at station page.")
            _, href = pick_best_download(links)
            dl = download_file(href, outdir)
            epw_path = extract_epw_from_zip(dl, outdir) if dl.suffix.lower() == ".zip" else dl

    meta = parse_epw_header(epw_path)
    station_info = {
        "name": str(best.get("name", "")),
        "country": str(best.get("country", "")),
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "distance_km": round(float(best["dist_km"]), 2),
        "index_url": url_raw if is_file_url(url_raw) else (url_raw.rstrip("/") + "/"),
    }
    return epw_path, meta, station_info

# ----------------------- LangGraph node -----------------------
def weather_generator(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node:
      - resolves city (from state)
      - downloads EPW
      - updates IDF SITE:LOCATION
      - returns idf_path and epw_path for runner
    """
    try:
        parsed = state.get("parsed_building_data", {}) or {}
        # Resolve city
        city = parsed.get("location") or state.get("city") or DEFAULT_CITY
        # Resolve IDF path (prefer upstream; else default)
        idf_path_str = state.get("idf_path") or DEFAULT_IDF
        idf_path = Path(idf_path_str)
        # Weather output dir
        outdir = Path(state.get("weather_outdir", "weather"))

        epw_path, epw_meta, station = obtain_epw_for_city(city, outdir, max_km=400.0)
        set_idf_location(idf_path, epw_meta, epw_path)

        # Return updated state
        sim_params = dict(state.get("simulation_params", {}))
        sim_params.update({"weather_station": station, "epw_header": epw_meta})

        return {
            **state,
            "idf_path": str(idf_path),
            "epw_path": str(epw_path),
            #"simulation_params": sim_params,
            "message": f"weather-generator: city='{city}', EPW='{Path(epw_path).name}'"
        }

    except Exception as e:
        return {**state, "errors": [f"weather-generator error: {e}"]}











































