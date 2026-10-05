"""
Core analysis logic for the Geographic Profiling tool.

Deliberately UI-agnostic: no input(), no folium display calls that assume
a notebook. Functions take data in, return data (or a folium.Map object
ready to be embedded) out.
"""

import re
from math import radians, sin, cos, sqrt, atan2

import folium
import pandas as pd

NYC_CENTER = [40.7128, -74.0060]
MARKER_COLOR = "#4A0000"

# Only the columns the tool actually uses. The full NYPD complaint export
# has 30+ columns — loading just these five cuts memory usage dramatically
# on large files (e.g. multi-GB exports) since pandas never has to parse,
# store, or infer dtypes for the columns we'd throw away anyway.
USED_COLUMNS = ["OFNS_DESC", "CMPLNT_FR_DT", "ADDR_PCT_CD", "Latitude", "Longitude"]
COLUMN_DTYPES = {
    "OFNS_DESC": "category",
    "CMPLNT_FR_DT": "string",
    "ADDR_PCT_CD": "float32",
    "Latitude": "float32",
    "Longitude": "float32",
}


def load_data(file_like):
    """
    Load the NYPD CSV export into a DataFrame, restricted to the columns
    this tool actually uses, with lighter dtypes to reduce memory usage
    on large files.
    """
    return pd.read_csv(
        file_like,
        usecols=USED_COLUMNS,
        dtype=COLUMN_DTYPES,
    )


def get_offense_types(df):
    """Sorted list of every distinct offense description in the dataset."""
    return sorted(df["OFNS_DESC"].dropna().unique().tolist())


def get_years(df):
    """
    Sorted list of years present in CMPLNT_FR_DT (format like 'MM/DD/YYYY').
    Mirrors the original notebook's approach of matching '/YYYY' at the end
    of the date string, just done once up front instead of per-lookup.
    """
    years = set()
    for date_str in df["CMPLNT_FR_DT"].dropna().astype(str):
        match = re.search(r"/(\d{4})$", date_str)
        if match:
            years.add(match.group(1))
    return sorted(years)


def filter_cases(df, offense_type, year):
    """Cases matching a given offense type and year."""
    return df[
        (df["OFNS_DESC"] == offense_type)
        & (df["CMPLNT_FR_DT"].str.contains(f"/{year}", na=False))
    ]


def top_precincts(filtered_df, top_n=15):
    """Precinct value counts for a filtered set of cases, as a list of (precinct, count) tuples."""
    counts = filtered_df["ADDR_PCT_CD"].value_counts().head(top_n)
    return list(counts.items())


def haversine_distance(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in kilometers."""
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def analyze_precinct(df, precinct_id, offense_type, year, radius_km=0.5):
    """
    Runs the density-weighted geographic profiling analysis for one precinct.

    Returns None if no matching cases are found, otherwise a dict with the
    filtered case subset, both center estimates, and the shift between them.
    """
    subset = df[
        (df["OFNS_DESC"] == offense_type)
        & (df["CMPLNT_FR_DT"].str.contains(f"/{year}", na=False))
        & (df["ADDR_PCT_CD"] == precinct_id)
    ].copy()

    subset = subset.dropna(subset=["Latitude", "Longitude"])

    if len(subset) == 0:
        return None

    mean_lat = subset["Latitude"].mean()
    mean_lon = subset["Longitude"].mean()

    coords = subset[["Latitude", "Longitude"]].values
    weights = []
    for i, (lat1, lon1) in enumerate(coords):
        nearby_count = 0
        for j, (lat2, lon2) in enumerate(coords):
            if i != j and haversine_distance(lat1, lon1, lat2, lon2) <= radius_km:
                nearby_count += 1
        weights.append(nearby_count + 1)
    subset["density_weight"] = weights

    weighted_lat = (subset["Latitude"] * subset["density_weight"]).sum() / subset["density_weight"].sum()
    weighted_lon = (subset["Longitude"] * subset["density_weight"]).sum() / subset["density_weight"].sum()

    shift_km = haversine_distance(mean_lat, mean_lon, weighted_lat, weighted_lon)

    return {
        "subset": subset,
        "case_count": len(subset),
        "mean_lat": mean_lat,
        "mean_lon": mean_lon,
        "weighted_lat": weighted_lat,
        "weighted_lon": weighted_lon,
        "shift_meters": shift_km * 1000,
    }


OVERVIEW_MAP_MAX_POINTS = 5000


def build_overview_map(df):
    """
    Map of cases with valid coordinates.

    NOTE: the original notebook referenced an undefined `df_filtered` here —
    this fixes that by explicitly using rows with non-null Latitude/Longitude,
    which was clearly the intent.

    On large datasets (real NYPD exports run into the millions of rows),
    plotting one marker per row would generate millions of HTML elements
    and freeze the browser. Above OVERVIEW_MAP_MAX_POINTS, this draws a
    random sample instead — still representative of the spatial spread,
    but actually renders. Returns (map, was_sampled, total_valid_count).
    """
    valid = df.dropna(subset=["Latitude", "Longitude"])
    total_valid = len(valid)

    was_sampled = total_valid > OVERVIEW_MAP_MAX_POINTS
    plot_data = valid.sample(OVERVIEW_MAP_MAX_POINTS, random_state=42) if was_sampled else valid

    m = folium.Map(location=NYC_CENTER, zoom_start=11, tiles="CartoDB dark_matter")
    for _, row in plot_data.iterrows():
        folium.CircleMarker(
            location=[row["Latitude"], row["Longitude"]],
            radius=2,
            color=MARKER_COLOR,
            fill=True,
            fill_opacity=0.4,
        ).add_to(m)
    return m, was_sampled, total_valid


def build_precinct_map(result):
    """Build the result map for a single precinct's analyze_precinct() output."""
    subset = result["subset"]
    m = folium.Map(
        location=[result["mean_lat"], result["mean_lon"]],
        zoom_start=13,
        tiles="CartoDB dark_matter",
    )
    for _, row in subset.iterrows():
        folium.CircleMarker(
            location=[row["Latitude"], row["Longitude"]],
            radius=4,
            color=MARKER_COLOR,
            fill=True,
            fill_opacity=0.5,
        ).add_to(m)
    folium.Marker(
        [result["mean_lat"], result["mean_lon"]],
        popup="Simple Mean",
        icon=folium.Icon(color="blue", icon="star"),
    ).add_to(m)
    folium.Marker(
        [result["weighted_lat"], result["weighted_lon"]],
        popup="Density-Weighted",
        icon=folium.Icon(color="green", icon="star"),
    ).add_to(m)
    return m
