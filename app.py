import streamlit as st
import pandas as pd

st.set_page_config(page_title="DraftKings Slate Intelligence", layout="wide")
st.title("🏈 DraftKings Slate Scanner & 17,500-Run Simulator")

tab1, tab2 = st.tabs(["⚡ Simulated Player Exposures", "🎯 Target Contests ($0.25 - $30.00)"])

with tab1:
    st.header("17,500 Optimal Appearance Rates")
    sim_file = "today_lineup_targets.csv"
    try:
        df_sim = pd.read_csv(sim_file)
        opt_col = "optimal_%" if "optimal_%" in df_sim.columns else "optimal_rate_%"

        min_opt = st.slider("Minimum Optimal %", 0.0, 30.0, 2.0, step=0.5)
        pos_filter = st.multiselect("Filter Positions", ["QB", "RB", "WR", "TE", "DST"], default=["QB", "RB", "WR", "TE", "DST"])
        
        filtered = df_sim[(df_sim[opt_col] >= min_opt) & (df_sim["position"].isin(pos_filter))]
        st.dataframe(filtered, use_container_width=True)
    except FileNotFoundError:
        st.warning("No simulation data found. Run a simulation first!")

with tab2:
    st.header("Top Prize Pools with Lowest Entries")
    try:
        df_contests = pd.read_csv("top_contests.csv")
        st.dataframe(df_contests[["name", "entry_fee", "prize_pool", "multiplier"]], use_container_width=True)
    except FileNotFoundError:
        st.info("No contest data saved yet. Run python3 run_pipeline.py to fetch live lobby contests.")
