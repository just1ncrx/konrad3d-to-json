#!/usr/bin/env python3
"""KONRAD3D-XML (+ optional Meso-XML) -> JSON.

Aufruf:
  python scripts/konrad3d_to_json.py \
      --konrad data/konrad3d/KONRAD3D_20261104T140000.xml \
      --meso   data/konrad3d/meso_20261104_1400.xml \
      --out    output/konrad3d.json

Nur Standardbibliothek, keine pip-Pakete noetig.
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime

NOFILL = -1000000000
WS = (">", " ", "\t", "\n", "\r")
BOUNDARY = (">", " ", "\t", "\n", "\r", "/")


# ── Schneller String-Parser (Port der JS-Variante) ───────────────────────────
def text_fast(xml, tag):
    if xml is None:
        return None
    open_ = f"<{tag}"
    close = f"</{tag}>"
    pos = 0
    while True:
        s = xml.find(open_, pos)
        if s == -1:
            return None
        i = s + len(open_)
        c = xml[i] if i < len(xml) else ""
        if c == "/":
            return None
        if c not in WS:  # auch "" (Ende des Strings)
            pos = s + 1
            continue
        body = xml.find(">", s)
        if body == -1:
            return None
        if xml[body - 1] == "/":
            return None
        e = xml.find(close, body)
        return None if e == -1 else xml[body + 1:e].strip()


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return None if math.isnan(f) else f


def num_fast(xml, tag):
    return _to_float(text_fast(xml, tag))


def _parse_int(v):
    if v is None or v == "":
        return None
    m = re.match(r"\s*[+-]?\d+", v)
    if not m:
        return None
    n = int(m.group(0))
    return None if n == NOFILL else n


def int_fast(xml, tag):
    return _parse_int(text_fast(xml, tag))


def text_attr(xml, tag):
    m = re.search(rf"<{tag}(?:[^>]*)>([^<]*)</{tag}>", xml)
    return m.group(1).strip() if m else None


def num_attr(xml, tag):
    return _to_float(text_attr(xml, tag))


def block_fast(xml, tag):
    if xml is None:
        return None
    open_ = f"<{tag}"
    close = f"</{tag}>"
    pos = 0
    while True:
        s = xml.find(open_, pos)
        if s == -1:
            return None
        i = s + len(open_)
        c = xml[i] if i < len(xml) else ""
        if c not in BOUNDARY:
            pos = s + 1
            continue
        body = xml.find(">", s)
        if body == -1:
            return None
        if xml[body - 1] == "/":
            return None
        e = xml.find(close, body)
        return None if e == -1 else xml[body + 1:e]


def all_blocks_fast(xml, tag):
    open_ = f"<{tag}"
    close = f"</{tag}>"
    results = []
    pos = 0
    while True:
        s = -1
        search = pos
        while True:
            idx = xml.find(open_, search)
            if idx == -1:
                return results
            i = idx + len(open_)
            c = xml[i] if i < len(xml) else ""
            if c in BOUNDARY:
                s = idx
                break
            search = idx + 1
        body = xml.find(">", s)
        if body == -1:
            break
        if xml[body - 1] == "/":
            pos = body + 1
            continue
        e = xml.find(close, body)
        if e == -1:
            break
        results.append({"full": xml[s:e + len(close)], "inner": xml[body + 1:e]})
        pos = e + len(close)
    return results


def attr_fast(tag_str, name):
    m = re.search(rf'{name}="([^"]*)"', tag_str)
    return m.group(1) if m else None


def no_fill(v):
    return None if (v is None or v == NOFILL) else v


def round4(n):
    return n if n is None else round(n, 4)


def js_round(x):
    return math.floor(x + 0.5)


# ── Geo ──────────────────────────────────────────────────────────────────────
R_EARTH = 6371.0


def bearing(lat1, lon1, lat2, lon2):
    d_lon = math.radians(lon2 - lon1)
    y = math.sin(d_lon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(d_lon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def dest_point(lat, lon, brng, dist):
    b = math.radians(brng)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(dist / R_EARTH)
                     + math.cos(lat1) * math.sin(dist / R_EARTH) * math.cos(b))
    lon2 = lon1 + math.atan2(
        math.sin(b) * math.sin(dist / R_EARTH) * math.cos(lat1),
        math.cos(dist / R_EARTH) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def haversine(lat1, lon1, lat2, lon2):
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return R_EARTH * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Meso ─────────────────────────────────────────────────────────────────────
def parse_elevations(np_):
    elev_block = block_fast(np_, "elevations") or ""
    by_site = {}
    units = None
    for el in all_blocks_fast(elev_block, "elevation"):
        m = re.search(r"<elevation([^>]*)>", el["full"])
        open_tag = m.group(0) if m else ""
        site = attr_fast(open_tag, "site")
        u = attr_fast(open_tag, "units")
        if u:
            units = u
        if not site:
            continue
        vals = []
        for v in el["inner"].split(","):
            f = _to_float(v.strip())
            if f is not None:
                vals.append(f)
        by_site[site] = vals
    return by_site, units


def parse_meso_events(xml):
    events = []
    for ev in all_blocks_fast(xml, "event"):
        np_ = block_fast(ev["inner"], "nowcast-parameters")
        if not np_:
            continue
        events.append({
            "np": np_,
            "diameter_equivalent_km": num_fast(np_, "mesocyclone_diameter_equivalent"),
            "velocity_rotational_max_ms": num_fast(np_, "mesocyclone_velocity_rotational_max"),
            "matched": False,
        })
    return events


def parse_meso_details(np_):
    by_site, units = parse_elevations(np_)
    return {
        "shear_mean": num_fast(np_, "mesocyclone_shear_mean"),
        "shear_max": num_fast(np_, "mesocyclone_shear_max"),
        "momentum_mean": num_fast(np_, "mesocyclone_momentum_mean"),
        "momentum_max": num_fast(np_, "mesocyclone_momentum_max"),
        "diameter_km": num_fast(np_, "mesocyclone_diameter"),
        "echotop_km": num_fast(np_, "mesocyclone_echotop"),
        "vil": num_fast(np_, "mesocyclone_vil"),
        "shear_vectors": int_fast(np_, "mesocyclone_shear_vectors"),
        "shear_features": int_fast(np_, "mesocyclone_shear_features"),
        "mean_dbz": num_fast(np_, "mean_dbz"),
        "max_dbz": num_fast(np_, "max_dbz"),
        "velocity_max_ms": num_fast(np_, "mesocyclone_velocity_max"),
        "velocity_rotational_mean_ms": num_fast(np_, "mesocyclone_velocity_rotational_mean"),
        "velocity_rotational_max_closest_to_ground_ms":
            num_fast(np_, "mesocyclone_velocity_rotational_max_closest_to_ground"),
        "meso_intensity": int_fast(np_, "meso_intensity"),
        "elevations_by_site": by_site,
        "elevations_units": units,
    }


def match_meso_event(cell, meso_events):
    meso = cell.get("mesocyclone")
    if not meso or meso["diameter_equiv_km"] is None or meso["rotational_max_ms"] is None:
        return None
    best = None
    best_score = math.inf
    for ev in meso_events:
        if ev["matched"]:
            continue
        if ev["diameter_equivalent_km"] is None or ev["velocity_rotational_max_ms"] is None:
            continue
        d_diff = abs(ev["diameter_equivalent_km"] - meso["diameter_equiv_km"])
        v_diff = abs(ev["velocity_rotational_max_ms"] - meso["rotational_max_ms"])
        if d_diff < 0.01 and v_diff < 0.01:
            score = d_diff + v_diff
            if score < best_score:
                best_score = score
                best = ev
    if best:
        best["matched"] = True
    return best


# ── Feature ──────────────────────────────────────────────────────────────────
def parse_feature(feature_full, ref_time_default):
    m = re.search(r"<feature([^>]*)>", feature_full)
    feature_tag = m.group(0) if m else ""
    inner = block_fast(feature_full, "feature") or feature_full

    meta = block_fast(inner, "metadata") or ""
    identifier = text_fast(meta, "identifier") or attr_fast(feature_tag, "identifier") or "0"
    ref_time = (text_fast(meta, "reference_time") or ref_time_default or "").strip()

    dt = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):?(\d{2})", ref_time)
    date_str = f"{dt.group(1)}{dt.group(2)}{dt.group(3)}" if dt else ""
    time_str = f"{dt.group(4)}{dt.group(5)}" if dt else ""

    geo = block_fast(inner, "geometry") or ""
    covered_area = num_fast(geo, "covered_area")
    area_growth_rate = num_fast(geo, "area_growth_rate")
    echo_top_msl = no_fill(num_fast(geo, "echo_top_msl"))
    echo_bottom_msl = no_fill(num_fast(geo, "echo_bottom_msl"))

    centroid3d = block_fast(geo, "centroid_3d") or block_fast(geo, "centroid3d") or ""
    geodetic = block_fast(centroid3d, "geodetic_coordinate") or centroid3d
    lat = round4(num_fast(geodetic, "latitude"))
    lon = round4(num_fast(geodetic, "longitude"))

    intens = block_fast(inner, "intensity") or ""
    sev = int_fast(intens, "severity")
    severity = sev if sev is not None else 0
    vil_density = num_fast(intens, "cell_based_VIL_density")
    max_dbz = num_fast(intens, "max_value")
    max_wind_gust = num_fast(intens, "maximum_estimated_wind_gust")
    heavy_rain_pot = num_fast(intens, "heavy_rain_potential")

    light = block_fast(inner, "lightning") or ""
    lr = int_fast(light, "lightning_rate")
    lightning_rate = lr if lr is not None else 0

    hymec = block_fast(inner, "hymec") or ""

    def nz(v):
        return v if v is not None else 0

    area_hail = nz(num_fast(hymec, "area_hail"))
    area_large_hail = nz(num_fast(hymec, "area_large_hail"))
    echo_top_hail = no_fill(num_fast(hymec, "echo_top_hail"))
    echo_top_large_hail = no_fill(num_fast(hymec, "echo_top_large_hail"))
    echo_bottom_hail = no_fill(num_fast(hymec, "echo_bottom_hail"))
    echo_bottom_large_hail = no_fill(num_fast(hymec, "echo_bottom_large_hail"))
    hail_flag = int_fast(inner, "hail_flag")

    meso_block = block_fast(inner, "mesocyclone") or ""
    ms = int_fast(meso_block, "mesocyclone_severity_index")
    meso_severity = ms if ms is not None else 0
    has_meso = meso_severity >= 1
    konrad_meso = {
        "severity_index": meso_severity,
        "diameter_equiv_km": no_fill(num_fast(meso_block, "mesocyclone_diameter_equivalent")),
        "height_top_m": no_fill(num_fast(meso_block, "mesocyclone_height_top")),
        "height_base_m": no_fill(num_fast(meso_block, "mesocyclone_height_base")),
        "rotational_max_ms": no_fill(num_fast(meso_block, "mesocyclone_velocity_rotational_max")),
    } if has_meso else None

    nwp_block = block_fast(inner, "nwp_model") or ""
    mu_cape = num_fast(nwp_block, "nwp_mu_cape")
    mu_cin = num_fast(nwp_block, "nwp_mu_cin")
    mu_lcl = num_fast(nwp_block, "nwp_mu_lcl_hgt")
    mu_lfc = num_fast(nwp_block, "nwp_mu_lfc_hgt")
    mu_el = num_fast(nwp_block, "nwp_mu_el_hgt")
    bs_01 = num_fast(nwp_block, "nwp_bs_01km")
    bs_06 = num_fast(nwp_block, "nwp_bs_06km")
    bs_eff = num_fast(nwp_block, "nwp_bs_eff_mu")
    srh_1 = num_fast(nwp_block, "nwp_srh_1km_rm")
    srh_3 = num_fast(nwp_block, "nwp_srh_3km_rm")
    lr_500800 = num_attr(nwp_block, "nwp_lr_500800hPa")
    prcp_water = num_fast(nwp_block, "nwp_prcp_water")
    dcape = num_fast(nwp_block, "nwp_dcape")

    nwp_stp = None
    if None not in (mu_cape, mu_lcl, srh_1, bs_06):
        cape_term = mu_cape / 1500
        lcl_term = 1.0 if mu_lcl < 1000 else 0.0 if mu_lcl > 2000 else (2000 - mu_lcl) / 1000
        srh_term = max(0, srh_1) / 150
        shr_term = 0.0 if bs_06 < 12.5 else 1.5 if bs_06 > 30 else bs_06 / 20
        cin_term = 1.0
        if mu_cin is not None:
            cin_term = 1.0 if mu_cin > -50 else 0.0 if mu_cin < -200 else (200 + mu_cin) / 150
        nwp_stp = js_round(cape_term * lcl_term * srh_term * shr_term * cin_term * 100) / 100

    nwp_scp = None
    if None not in (mu_cape, srh_3, bs_06):
        cape_term = mu_cape / 1000
        srh_term = max(0, srh_3) / 50
        ebs_term = 0.0 if bs_06 < 10 else 1.0 if bs_06 > 20 else bs_06 / 20
        nwp_scp = js_round(cape_term * srh_term * ebs_term * 100) / 100

    hail_cm = None
    if hail_flag is not None and hail_flag >= 1:
        def thick(top, bot):
            return (top - bot) / 1000 if (top is not None and bot is not None) else 0

        if hail_flag == 1:
            t = thick(echo_top_hail, echo_bottom_hail)
            hail_cm = min(0.5 + t * 0.1 + area_hail * 0.001, 1.9)
        elif hail_flag == 2:
            t = thick(echo_top_hail, echo_bottom_hail)
            hail_cm = max(2.0, min(2.0 + t * 0.2 + area_hail * 0.003, 4.9))
        elif hail_flag == 3:
            if echo_top_large_hail is not None and echo_bottom_large_hail is not None:
                t = (echo_top_large_hail - echo_bottom_large_hail) / 1000
            else:
                t = thick(echo_top_hail, echo_bottom_hail)
            area = area_large_hail if area_large_hail > 0 else area_hail
            hail_cm = max(5.0, 5.0 + t * 0.3 + area * 0.005)
        if hail_cm is not None:
            hail_cm = js_round(hail_cm * 10) / 10

    track_block = block_fast(inner, "tracking") or ""
    cell_speed = num_fast(track_block, "cell_speed")
    severity_trend = num_fast(track_block, "severity_trend")
    if severity_trend is None:
        severity_trend = num_fast(inner, "severity_trend")
    mass_trend = num_fast(track_block, "mass_trend")
    if mass_trend is None:
        mass_trend = num_fast(inner, "mass_trend")

    development = None
    if area_growth_rate is not None or severity_trend is not None or mass_trend is not None:
        pos = neg = 0
        for v in (severity_trend, area_growth_rate, mass_trend):
            if v is None:
                continue
            if v > 0.5:
                pos += 1
            elif v < -0.5:
                neg += 1
        if pos >= 2:
            development = {"status": "wachsend", "color": "red"}
        elif neg >= 2:
            development = {"status": "schrumpfend", "color": "green"}
        else:
            development = {"status": "gleichbleibend", "color": "orange"}

    forecast_block = block_fast(inner, "forecast") or ""
    centroid_forecasts = block_fast(forecast_block, "centroid_forecasts") or ""
    forecast_lat = forecast_lon = None
    for cf in all_blocks_fast(centroid_forecasts, "centroid_forecast"):
        fg = block_fast(cf["inner"], "geodetic_coordinate") or cf["inner"]
        forecast_lat = num_fast(fg, "latitude")
        forecast_lon = num_fast(fg, "longitude")

    lat3 = lon3 = None
    p1_lat = p1_lon = p2_lat = p2_lon = None
    if lat and lon and forecast_lat and forecast_lon:
        lat3 = forecast_lat - lat
        lon3 = forecast_lon - lon
        track_bearing = bearing(lat, lon, forecast_lat, forecast_lon)
        track_dist = haversine(lat, lon, forecast_lat, forecast_lon)
        cone_width = max(25, track_dist * 0.5)
        p1_lat, p1_lon = dest_point(forecast_lat, forecast_lon, (track_bearing + 90) % 360, cone_width)
        p2_lat, p2_lon = dest_point(forecast_lat, forecast_lon, (track_bearing + 270) % 360, cone_width)

    ref_ms = None
    try:
        ref_ms = int(datetime.fromisoformat(ref_time.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        pass

    return {
        "dateStr": date_str,
        "timeStr": time_str,
        "cell_id": identifier,
        "latitude": lat,
        "longitude": lon,
        "_ref_ms": ref_ms,
        "cell_speed": cell_speed,
        "cell_based_vil_density": vil_density,
        "dbz_max": max_dbz,
        "hail_flag": hail_flag,
        "hail_cm": hail_cm,
        "lightning_rate": lightning_rate,
        "wind_gust": max_wind_gust,
        "heavy_rain_rate": heavy_rain_pot,
        "severity": severity,
        "severity_trend": severity_trend,
        "mass_trend": mass_trend,
        "area_growth_rate": area_growth_rate,
        "development": development,
        "forecast_latitude": forecast_lat,
        "forecast_longitude": forecast_lon,
        "perp_point1_lat": p1_lat, "perp_point1_lon": p1_lon,
        "perp_point2_lat": p2_lat, "perp_point2_lon": p2_lon,
        "lon3": lon3, "lat3": lat3,
        "echo_top_msl": echo_top_msl,
        "echo_bottom_msl": echo_bottom_msl,
        "covered_area": covered_area,
        "has_mesocyclone": has_meso,
        "konrad_mesocyclone": konrad_meso,
        "nwp": {
            "mu_cape": mu_cape, "mu_cin": mu_cin, "mu_lcl_hgt": mu_lcl,
            "mu_lfc_hgt": mu_lfc, "mu_el_hgt": mu_el,
            "bs_01km": bs_01, "bs_06km": bs_06, "bs_eff_mu": bs_eff,
            "srh_1km_rm": srh_1, "srh_3km_rm": srh_3,
            "srh_1km_lm": num_fast(nwp_block, "nwp_srh_1km_lm"),
            "srh_3km_lm": num_fast(nwp_block, "nwp_srh_3km_lm"),
            "lr_500800hPa": lr_500800,
            "prcp_water": prcp_water,
            "dcape": dcape,
        },
        "nwp_indices": {"stp": nwp_stp, "scp": nwp_scp},
    }


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--konrad", required=True, help="KONRAD3D-XML")
    ap.add_argument("--meso", help="Meso-XML zum selben Zeitstempel (optional)")
    ap.add_argument("--out", required=True, help="Ziel-JSON")
    args = ap.parse_args()

    xml = read(args.konrad)
    meso_events = []
    if args.meso and os.path.isfile(args.meso) and os.path.getsize(args.meso) > 0:
        meso_events = parse_meso_events(read(args.meso))

    m = re.search(r"<reference_time[^>]*>([^<]+)</reference_time>", xml) \
        or re.search(r'<cells[^>]+reference_time="([^"]+)"', xml)
    reference_time = m.group(1).strip() if m else None
    m = re.search(r"<creation-date[^>]*>([^<]+)</creation-date>", xml)
    creation_date = m.group(1).strip() if m else None

    cells = [parse_feature(f["full"], reference_time) for f in all_blocks_fast(xml, "feature")]

    for cell in cells:
        if not cell["has_mesocyclone"] or not cell["latitude"] or not cell["longitude"]:
            cell["mesocyclone"] = None
            cell["is_supercell"] = False
            cell.pop("konrad_mesocyclone", None)
            continue

        cell["mesocyclone"] = {
            **cell["konrad_mesocyclone"],
            "latitude": cell["latitude"],
            "longitude": cell["longitude"],
            "dist_km": None,
            "source": "konrad3d_only",
        }
        match = match_meso_event(cell, meso_events)
        if match:
            d = parse_meso_details(match["np"])
            cell["mesocyclone"].update({
                "shear_mean_ms_km": d["shear_mean"],
                "shear_max_ms_km": d["shear_max"],
                "momentum_mean_ms_km": d["momentum_mean"],
                "momentum_max_ms_km": d["momentum_max"],
                "diameter_km": d["diameter_km"],
                "echotop_km": d["echotop_km"],
                "vil_kg_m2": d["vil"],
                "shear_vectors": d["shear_vectors"],
                "shear_features": d["shear_features"],
                "mean_dbz": d["mean_dbz"],
                "max_dbz": d["max_dbz"],
                "velocity_max_ms": d["velocity_max_ms"],
                "velocity_rotational_mean_ms": d["velocity_rotational_mean_ms"],
                "velocity_rotational_max_closest_to_ground_ms":
                    d["velocity_rotational_max_closest_to_ground_ms"],
                "meso_intensity": d["meso_intensity"],
                "elevations": d["elevations_by_site"],
                "elevations_units": d["elevations_units"],
                "source": "konrad3d+meso",
            })
        cell["is_supercell"] = True
        cell.pop("konrad_mesocyclone", None)

    result = {
        "reference_time": reference_time,
        "creation_date": creation_date,
        "file": os.path.basename(args.konrad),
        "stormtracking_cells": cells,
    }

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    print(f"{len(cells)} Zellen, {sum(1 for c in cells if c['is_supercell'])} Superzellen -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())