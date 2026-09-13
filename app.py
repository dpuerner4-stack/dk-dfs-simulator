import streamlit as st
import pandas as pd
import numpy as np
import requests
from ortools.linear_solver import pywraplp
import time

st.set_page_config(page_title="DraftKings Optimizer", layout="wide")

# --- 1. CORE SIMULATION & OPTIMIZATION FUNCTIONS ---
def get_target_slate(min_fee=0.25, max_fee=30.0, min_pool=25000):
    url = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
    except Exception as e:
        st.error(f"Error connecting to DraftKings Lobby: {e}")
        return pd.DataFrame(), None

    contests = []
    for c in res.get("Contests", []):
        fee = float(c.get("a", 0))
        pool = float(c.get("po", 0))
        dg = c.get("dg")
        if min_fee <= fee <= max_fee and pool >= min_pool and dg:
            contests.append({
                "contest_id": c.get("id"),
                "name": c.get("n"),
                "entry_fee": fee,
                "prize_pool": pool,
                "multiplier": round(pool / fee, 0),
                "draft_group_id": dg
            })

    df = pd.DataFrame(contests)
    if df.empty and min_pool > 5000:
        return get_target_slate(min_fee, max_fee, min_pool=5000)

    if not df.empty:
        df = df.sort_values(by=["prize_pool", "multiplier"], ascending=[False, False])
        return df, df.iloc[0]["draft_group_id"]
    return pd.DataFrame(), None

def fetch_player_pool(draft_group_id):
    url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables?format=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers, timeout=10).json()
    draftables = res.get("draftables", [])

    players = []
    for p in draftables:
        status = p.get("status", "None")
        if status in ["O", "IR", "D", "PUP", "SUS"]:
            continue
        salary = p.get("salary", 0)
        if salary <= 0:
            continue
        pos = p.get("position")
        name = p.get("displayName")
        fppg = 0.0
        for stat in p.get("draftStatAttributes", []):
            if stat.get("id") == 90:
                try:
                    fppg = float(stat.get("value", 0))
                except:
                    pass

        players.append({
            "name": name,
            "position": pos,
            "salary": salary,
            "fppg": fppg,
            "status": status
        })

    df = pd.DataFrame(players).drop_duplicates(subset=["name", "position"])
    df["proj_fpts"] = df["fppg"]

    multipliers = {"QB": (0.35, 3.0), "RB": (0.40, 2.5), "WR": (0.55, 2.0), "TE": (0.45, 1.5), "DST": (0.65, 2.0)}
    def calc_std(r):
        slope, intercept = multipliers.get(r["position"], (0.45, 2.0))
        return round(r["proj_fpts"] * slope + intercept, 2)

    df["std_dev"] = df.apply(calc_std, axis=1)
    return df.reset_index(drop=True)

def solve_optimal_lineup(df, score_column="proj_fpts"):
    solver = pywraplp.Solver.CreateSolver("CBC")
    if not solver:
        return None
    n = len(df)
    x = [solver.BoolVar(f"x_{i}") for i in range(n)]

    obj = solver.Objective()
    for i in range(n):
        obj.SetCoefficient(x[i], float(df.loc[i, score_column]))
    obj.SetMaximization()

    sal_ct = solver.Constraint(0, 50000)
    qb_ct, dst_ct = solver.Constraint(1, 1), solver.Constraint(1, 1)
    rb_ct, wr_ct, te_ct = solver.Constraint(2, 3), solver.Constraint(3, 4), solver.Constraint(1, 2)
    flex_ct, tot_ct = solver.Constraint(7, 7), solver.Constraint(9, 9)

    for i, pos in enumerate(df["position"]):
        sal_ct.SetCoefficient(x[i], int(df.loc[i, "salary"]))
        tot_ct.SetCoefficient(x[i], 1)
        if pos == "QB": qb_ct.SetCoefficient(x[i], 1)
        elif pos == "DST": dst_ct.SetCoefficient(x[i], 1)
        elif pos == "RB": rb_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
        elif pos == "WR": wr_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
        elif pos == "TE": te_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)

    if solver.Solve() == pywraplp.Solver.OPTIMAL:
        selected = [i for i in range(n) if x[i].solution_value() > 0.5]
        lineup = df.loc[selected].copy()
        pos_order = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "DST": 5}
        lineup["order"] = lineup["position"].map(pos_order)
        return lineup.sort_values(by="order").drop(columns=["order"])
    return None

def run_simulation(df, num_simulations=17500):
    n = len(df)
    salaries = df["salary"].to_numpy(dtype=np.int32)
    positions = df["position"].to_numpy()
    
    sim_matrix = np.random.normal(df["proj_fpts"], df["std_dev"], size=(num_simulations, n))
    sim_matrix = np.clip(sim_matrix, 0, None).astype(np.float32)

    counts = np.zeros(n, dtype=np.int32)
    progress_bar = st.progress(0, text="Simulating DraftKings slates...")

    # Batch solve for responsive cloud execution
    batch_size = 500
    for b_start in range(0, num_simulations, batch_size):
        b_end = min(b_start + batch_size, num_simulations)
        for s in range(b_start, b_end):
            scores = sim_matrix[s]
            solver = pywraplp.Solver.CreateSolver("CBC")
            if not solver:
                continue
            x = [solver.BoolVar(f"x_{i}") for i in range(n)]
            obj = solver.Objective()
            for i in range(n):
                obj.SetCoefficient(x[i], float(scores[i]))
            obj.SetMaximization()

            sal_ct = solver.Constraint(0, 50000)
            qb_ct, dst_ct = solver.Constraint(1, 1), solver.Constraint(1, 1)
            rb_ct, wr_ct, te_ct = solver.Constraint(2, 3), solver.Constraint(3, 4), solver.Constraint(1, 2)
            flex_ct, tot_ct = solver.Constraint(7, 7), solver.Constraint(9, 9)

            for i, pos in enumerate(positions):
                sal_ct.SetCoefficient(x[i], int(salaries[i]))
                tot_ct.SetCoefficient(x[i], 1)
                if pos == "QB": qb_ct.SetCoefficient(x[i], 1)
                elif pos == "DST": dst_ct.SetCoefficient(x[i], 1)
                elif pos == "RB": rb_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
                elif pos == "WR": wr_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
                elif pos == "TE": te_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)

            if solver.Solve() == pywraplp.Solver.OPTIMAL:
                for i in range(n):
                    if x[i].solution_value() > 0.5:
                        counts[i] += 1

        pct = int((b_end / num_simulations) * 100)
        progress_bar.progress(pct, text=f"Solved {b_end:,} / {num_simulations:,} linear slates...")

    progress_bar.empty()
    df["optimal_%"] = np.round((counts / num_simulations) * 100, 2)
    df["leverage"] = np.round(df["optimal_%"] / (df["salary"] / 1000), 2)
    return df.sort_values(by="optimal_%", ascending=False)

# --- 2. HEADER & PROMINENT RUN BUTTON ---
st.title("🏈 DraftKings Slate Scanner & Optimizer")

col_btn, col_info = st.columns([1, 3])
with col_btn:
    run_clicked = st.button("🚀 Run Live 17,500 Simulation", use_container_width=True, type="primary")

if run_clicked:
    with st.spinner("Connecting to DraftKings API & solving slates..."):
        t0 = time.time()
        contests_df, draft_group_id = get_target_slate()
        if contests_df.empty or not draft_group_id:
            st.error("No valid NFL contests found between $0.25 and $30.00 right now.")
        else:
            st.session_state["contests"] = contests_df
            players_df = fetch_player_pool(draft_group_id)
            sim_results = run_simulation(players_df, num_simulations=17500)
            optimal_roster = solve_optimal_lineup(sim_results, "proj_fpts")

            st.session_state["sim_results"] = sim_results
            st.session_state["optimal_roster"] = optimal_roster
            st.session_state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            st.success(f"Done in {time.time() - t0:.1f}s!")

# --- 3. DASHBOARD TABS ---
tab1, tab2, tab3 = st.tabs(["🏆 Weekly Optimal Lineup", "⚡ Simulated Exposures", "🎯 Target Contests ($0.25 - $30.00)"])

with tab1:
    st.header("Weekly Optimal Lineup (Max Projections & Salary Constraints)")
    if "optimal_roster" in st.session_state and st.session_state["optimal_roster"] is not None:
        roster = st.session_state["optimal_roster"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Salary", f"${roster['salary'].sum():,} / $50,000")
        c2.metric("Projected Points", f"{roster['proj_fpts'].sum():.2f}")
        c3.metric("Last Run", st.session_state.get("last_run", "N/A"))
        st.dataframe(roster[["position", "name", "salary", "proj_fpts", "optimal_%", "leverage"]], use_container_width=True)
    else:
        st.info("Tap the **🚀 Run Live 17,500 Simulation** button above to generate this week's optimal lineup.")

with tab2:
    st.header("17,500 Optimal Appearance Rates")
    if "sim_results" in st.session_state:
        df_sim = st.session_state["sim_results"]
        col1, col2 = st.columns([1, 3])
        with col1:
            min_opt = st.slider("Minimum Optimal %", 0.0, 40.0, 2.0, step=0.5)
        with col2:
            pos_filter = st.multiselect("Filter Positions", ["QB", "RB", "WR", "TE", "DST"], default=["QB", "RB", "WR", "TE", "DST"])
        filtered = df_sim[(df_sim["optimal_%"] >= min_opt) & (df_sim["position"].isin(pos_filter))]
        st.dataframe(filtered[["position", "name", "salary", "proj_fpts", "optimal_%", "leverage"]], use_container_width=True)
    else:
        st.warning("No simulation data found. Tap 'Run Live 17,500 Simulation' above!")

with tab3:
    st.header("Target Contests ($0.25 - $30.00)")
    if "contests" in st.session_state:
        st.dataframe(st.session_state["contests"][["name", "entry_fee", "prize_pool", "multiplier"]], use_container_width=True)
    else:
        st.info("Contest data will load once you run a simulation.")
