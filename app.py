"""
Geographic Profiling — Streamlit front end.

Run with: streamlit run app.py

Sequential/wizard-style layout: each step only appears once the step
before it is complete, so the user isn't shown every option at once.
"""

import streamlit.components.v1 as components
import streamlit as st

import geo_analysis
from geo_analysis import (
    analyze_precinct,
    build_overview_map,
    build_precinct_map,
    filter_cases,
    get_offense_types,
    get_years,
    load_data,
    top_precincts,
)

st.set_page_config(page_title="Geographic Profiling", layout="wide", page_icon="🗺️")

# ---------------------------------------------------------------
# Dark theme polish (beyond what .streamlit/config.toml covers)
# ---------------------------------------------------------------
st.markdown(
    """
    <style>
    .step-label {
        color: #8a8f98;
        font-size: 0.8rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: -0.5rem;
    }
    .metric-box {
        background-color: #1a1d24;
        border: 1px solid #2a2e37;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🗺️ Geographic Profiling")
st.caption("Estimate a likely anchor point for a cluster of crimes using density-weighted geographic profiling.")


def render_map(folium_map, height=480):
    components.html(folium_map._repr_html_(), height=height)


def step_label(n, text):
    st.markdown(f'<div class="step-label">Step {n}</div>', unsafe_allow_html=True)
    st.subheader(text)


# ---------------------------------------------------------------
# Step 1 — Upload
# ---------------------------------------------------------------

step_label(1, "Upload your NYPD case data")
uploaded_file = st.file_uploader("CSV file", type=["csv"], label_visibility="collapsed")

if uploaded_file is None:
    st.info("Upload a CSV to get started.")
    st.stop()

if "df" not in st.session_state or st.session_state.get("_uploaded_name") != uploaded_file.name:
    with st.spinner("Loading data..."):
        st.session_state.df = load_data(uploaded_file)
        st.session_state._uploaded_name = uploaded_file.name

df = st.session_state.df
st.success(f"Loaded {len(df):,} records.")

with st.expander("Preview overview map (all cases with valid coordinates)"):
    with st.spinner("Building map..."):
        overview_map, was_sampled, total_valid = build_overview_map(df)
    if was_sampled:
        st.caption(
            f"Showing a random sample of {geo_analysis.OVERVIEW_MAP_MAX_POINTS:,} points "
            f"out of {total_valid:,} total — plotting all of them would freeze the map."
        )
    render_map(overview_map, height=400)

st.divider()

# ---------------------------------------------------------------
# Step 2 — Offense type
# ---------------------------------------------------------------

step_label(2, "Choose an offense type")
offense_options = get_offense_types(df)
offense_choice = st.selectbox("Offense type", offense_options, label_visibility="collapsed")

st.divider()

# ---------------------------------------------------------------
# Step 3 — Year
# ---------------------------------------------------------------

step_label(3, "Choose a year")
year_options = get_years(df)
if not year_options:
    st.error("No parseable years found in CMPLNT_FR_DT.")
    st.stop()
year_choice = st.selectbox("Year", year_options, label_visibility="collapsed")

st.divider()

# ---------------------------------------------------------------
# Step 4 — Clustering radius
# ---------------------------------------------------------------

step_label(4, "Set the clustering radius")
radius_km = st.slider(
    "Clustering radius (km)", min_value=0.1, max_value=3.0, value=0.5, step=0.1,
    label_visibility="collapsed",
)
st.caption(f"Cases within {radius_km} km of each other count toward each other's density weight.")

st.divider()

# ---------------------------------------------------------------
# Step 5 — Precinct
# ---------------------------------------------------------------

step_label(5, "Choose a precinct")

filtered = filter_cases(df, offense_choice, year_choice)
precinct_counts = top_precincts(filtered, top_n=15)

if not precinct_counts:
    st.warning(f"No cases found for {offense_choice} in {year_choice}. Try a different offense/year.")
    st.stop()

st.caption("Top precincts by case count, for this offense type and year:")
precinct_labels = [f"{pid} — {count} cases" for pid, count in precinct_counts]
precinct_ids = [pid for pid, _ in precinct_counts]

selected_index = st.radio(
    "Precinct", options=range(len(precinct_labels)),
    format_func=lambda i: precinct_labels[i], label_visibility="collapsed",
)
precinct_id = precinct_ids[selected_index]

st.divider()

# ---------------------------------------------------------------
# Step 6 — Run
# ---------------------------------------------------------------

step_label(6, "Run the analysis")
run = st.button("Run Analysis", type="primary")

if run:
    with st.spinner("Analyzing precinct..."):
        result = analyze_precinct(
            df, precinct_id, offense_type=offense_choice, year=year_choice, radius_km=radius_km
        )

    if result is None:
        st.error("No cases found for that combination. Try a different precinct.")
        st.stop()

    st.divider()
    st.subheader(f"Results — Precinct {precinct_id}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Cases analyzed", result["case_count"])
    col2.metric("Simple mean", f"{result['mean_lat']:.5f}, {result['mean_lon']:.5f}")
    col3.metric("Density-weighted center", f"{result['weighted_lat']:.5f}, {result['weighted_lon']:.5f}")

    st.metric("Shift between methods", f"{result['shift_meters']:.1f} m")

    render_map(build_precinct_map(result), height=520)
