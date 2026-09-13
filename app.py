import streamlit as st
import pandas as pd
import numpy as np
import requests
from ortools.linear_solver import pywraplp
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

st.set_page_config(page_title="DraftKings Optimizer", layout="wide")

# --- MODERN EMAIL TEMPLATE GENERATOR ---
def generate_email_html(optimal_roster, sim_results, matchups_df):
    pos_colors = {
        "QB": "#e06666",
        "RB": "#6fa8dc",
        "WR": "#ffd966",
        "TE": "#93c47d",
        "DST": "#8e7cc3"
    }

    # Format Slate Matchup Badges
    matchup_badges = ""
    if isinstance(matchups_df, pd.DataFrame) and not matchups_df.empty:
        for _, row in matchups_df.iterrows():
            matchup_badges += f"""
            <span style="display: inline-block; background-color: #f1f3f5; border: 1px solid #dee2e6; border-radius: 6px; padding: 6px 12px; margin: 4px; font-weight: 600; font-size: 13px; color: #343a40;">
                🏈 {row['matchup']} &nbsp;<span style="font-weight: 400; color: #6c757d;">({row['start_time']})</span>
            </span>
            """
    else:
        matchup_badges = "<span style='color: #6c757d; font-size: 13px;'>No specific slate games found.</span>"

    # Lineup Rows Builder
    lineup_rows = ""
    for idx, row in optimal_roster.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        pos = row.get("position", "")
        badge_color = pos_colors.get(pos, "#adb5bd")
        text_color = "#000000" if pos in ["WR", "RB", "TE"] else "#ffffff"
        
        lineup_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{pos}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
            <td style="padding: 10px 12px; color: #6c757d; font-size: 12px;">{row.get('matchup', '')}</td>
            <td style="padding: 10px 12px; font-weight: 600; color: #198754;">${int(row.get('salary', 0)):,}</td>
            <td style="padding: 10px 12px; font-weight: 600;">{row.get('proj_fpts', 0):.1f}</td>
            <td style="padding: 10px 12px; color: #0d6efd; font-weight: 600;">{row.get('optimal_%', 0):.2f}%</td>
            <td style="padding: 10px 12px; font-weight: 600;">{row.get('leverage', 0):.2f}x</td>
        </tr>
        """

    # Top Leverage Exposures Builder
    top_exposures = sim_results.head(10)
    exposure_rows = ""
    for idx, row in top_exposures.reset_index().iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        pos = row.get("position", "")
        badge_color = pos_colors.get(pos, "#adb5bd")
        text_color = "#000000" if pos in ["WR", "RB", "TE"] else "#ffffff"

        exposure_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{pos}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
            <td style="padding: 10px 12px; color: #6c757d; font-size: 12px;">{row.get('matchup', '')}</td>
            <td style="padding: 10px 12px; font-weight: 600; color: #198754;">${int(row.get('salary', 0)):,}</td>
            <td style="padding: 10px 12px;">{row.get('proj_fpts', 0):.1f}</td>
            <td style="padding: 10px 12px; color: #0d6efd; font-weight: 700;">{row.get('optimal_%', 0):.2f}%</td>
            <td style="padding: 10px 12px; font-weight: 700; color: #d63384;">{row.get('leverage', 0):.2f}x</td>
        </tr>
        """

    total_sal = int(optimal_roster['salary'].sum())
    rem_sal = 50000 - total_sal
    total_proj = optimal_roster['proj_fpts'].sum()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 24px; color: #212529; }}
            .container {{ max-width: 680px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); overflow: hidden; border: 1px solid #e9ecef; }}
            .header {{ background: linear-gradient(135deg, #1e3a8a, #0b5394); padding: 28px 24px; color: #ffffff; }}
            .content {{ padding: 24px; }}
            .kpi-row {{ display: table; width: 100%; margin: 18px 0; border-collapse: separate; border-spacing: 8px 0; }}
            .kpi-card {{ display: table-cell; width: 33.33%; background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 12px 14px; text-align: center; }}
            .kpi-label {{ font-size: 11px; text-transform: uppercase; color: #6c757d; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 4px; }}
            .kpi-val {{ font-size: 20px; font-weight: 800; color: #212529; }}
            .table-wrap {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 24px; border-radius: 8px; overflow: hidden; border: 1px solid #dee2e6; }}
            th {{ background-color: #f1f3f5; color: #495057; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px; border-bottom: 2px solid #dee2e6; text-align: left; }}
            h3 {{ font-size: 16px; margin: 20px 0 8px 0; color: #1e3a8a; display: flex; align-items: center; }}
            .footer {{ background-color: #f8f9fa; border-top: 1px solid #e9ecef; padding: 16px; text-align: center; font-size: 11px; color: #adb5bd; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2 style="margin: 0 0 6px 0; font-size: 22px; font-weight: 800;">🏈 DraftKings Optimizer Digest</h2>
                <div style="font-size: 13px; opacity: 0.85;">Generated on {time.strftime('%A, %b %d, %Y at %I:%M %p')}</div>
            </div>
            <div class="content">
                <div style="margin-bottom: 16px;">
                    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #6c757d; margin-bottom: 6px;">Included Matchups</div>
                    {matchup_badges}
                </div>

                <div class="kpi-row">
                    <div class="kpi-card">
                        <div class="kpi-label">Lineup Salary</div>
                        <div class="kpi-val" style="color: #198754;">${total_sal:,}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Remaining Cap</div>
                        <div class="kpi-val" style="color: #0d6efd;">${rem_sal:,}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Projected Fpts</div>
                        <div class="kpi-val">{total_proj:.2f}</div>
                    </div>
                </div>

                <h3>🏆 Optimal Lineup (Max FPPG Constrained)</h3>
                <table class="table-wrap">
                    <thead>
                        <tr>
                            <th>Pos</th>
                            <th>Player</th>
                            <th>Team</th>
                            <th>Matchup</th>
                            <th>Salary</th>
                            <th>Fpts</th>
                            <th>Opt %</th>
                            <th>Lev</th>
                        </tr>
                    </thead>
                    <tbody>
                        {lineup_rows}
                    </tbody>
                </table>

                <h3>⚡ Top 10 Simulated Target Exposures</h3>
                <table class="table-wrap">
                    <thead>
                        <tr>
                            <th>Pos</th>
                            <th>Player</th>
                            <th>Team</th>
                            <th>Matchup</th>
                            <th>Salary</th>
                            <th>Fpts</th>
                            <th>Opt %</th>
                            <th>Lev</th>
                        </tr>
                    </thead>
                    <tbody>
                        {exposure_rows}
                    </tbody>
                </table>
            </div>
            <div class="footer">
                Automated report via Streamlit & GitHub Actions • Monte Carlo (17,500 iterations)
            </div>
        </div>
    </body>
    </html>
    """
    return html

def send_email_report(optimal_roster, sim_results, matchups_df, sender_email, sender_password, recipient_email):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🏈 DraftKings Lineup Alert: {optimal_roster['proj_fpts'].sum():.1f} FPTS (${int(optimal_roster['salary'].sum()):,})"
        msg["From"] = sender_email
        msg["To"] = recipient_email

        html = generate_email_html(optimal_roster, sim_results, matchups_df)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        return True, "Email report sent successfully!"
    except Exception as e:
        return False, f"Failed to send email: {e}"

# --- CORE SIMULATION & DATA EXTRACTION ---
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
    
    competitions = res.get("competitions", [])
    games = []
    for comp in competitions:
        name = comp.get("name", "")
        start = comp.get("startTime", "")
        start_str = start.replace("T", " ").split(".")[0] if start else "TBD"
        games.append({"matchup": name, "start_time": start_str})
    matchups_df = pd.DataFrame(games)

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
        team = p.get("teamAbbreviation", "")
        comp_info = p.get("competition", {})
        matchup = comp_info.get("name", "")
        
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
            "team": team,
            "matchup": matchup,
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
    return df.reset_index(drop=True), matchups_df

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

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    st.header("📧 Email Notifications")
    send_email = st.checkbox("Email report when simulation runs", value=True)
    sender_email = st.secrets.get("EMAIL_SENDER", "") if "EMAIL_SENDER" in st.secrets else st.text_input("Sender Gmail", "")
    sender_pw = st.secrets.get("EMAIL_PASSWORD", "") if "EMAIL_PASSWORD" in st.secrets else st.text_input("Gmail App Password", type="password")
    recipient_email = st.secrets.get("EMAIL_RECIPIENT", "") if "EMAIL_RECIPIENT" in st.secrets else st.text_input("Recipient Email", "")

# --- HEADER & RUN BUTTON ---
st.title("🏈 DraftKings Slate Scanner & Optimizer")

col_btn, col_info = st.columns([1, 3])
with col_btn:
    run_clicked = st.button("🚀 Run Live 17,500 Simulation", width="stretch", type="primary")

if run_clicked:
    with st.spinner("Connecting to DraftKings API & solving slates..."):
        t0 = time.time()
        contests_df, draft_group_id = get_target_slate()
        if contests_df.empty or not draft_group_id:
            st.error("No valid NFL contests found between $0.25 and $30.00 right now.")
        else:
            st.session_state["contests"] = contests_df
            players_df, matchups_df = fetch_player_pool(draft_group_id)
            sim_results = run_simulation(players_df, num_simulations=17500)
            optimal_roster = solve_optimal_lineup(sim_results, "proj_fpts")

            st.session_state["matchups"] = matchups_df
            st.session_state["sim_results"] = sim_results
            st.session_state["optimal_roster"] = optimal_roster
            st.session_state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")

            if send_email and sender_email and sender_pw and recipient_email:
                ok, msg = send_email_report(optimal_roster, sim_results, matchups_df, sender_email, sender_pw, recipient_email)
                if ok:
                    st.success("Simulation complete & email report delivered!")
                else:
                    st.error(f"Email error: {msg}")
            else:
                st.success(f"Simulation complete in {time.time() - t0:.1f}s!")

# --- DASHBOARD TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["🏆 Weekly Optimal Lineup", "🏟️ Slate Games", "⚡ Simulated Exposures", "🎯 Target Contests"])

cols_to_display = ["position", "name", "team", "matchup", "salary", "proj_fpts", "optimal_%", "leverage"]

with tab1:
    st.header("Weekly Optimal Lineup")
    if "optimal_roster" in st.session_state and st.session_state["optimal_roster"] is not None:
        roster = st.session_state["optimal_roster"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Salary", f"${roster['salary'].sum():,} / $50,000")
        c2.metric("Projected Points", f"{roster['proj_fpts'].sum():.2f}")
        c3.metric("Last Run", st.session_state.get("last_run", "N/A"))
        valid_cols = [c for c in cols_to_display if c in roster.columns]
        st.dataframe(roster[valid_cols], width="stretch")
    else:
        st.info("Tap '🚀 Run Live 17,500 Simulation' to generate the optimal lineup.")

with tab2:
    st.header("🏟️ Games on this Slate")
    if "matchups" in st.session_state and isinstance(st.session_state["matchups"], pd.DataFrame) and not st.session_state["matchups"].empty:
        st.dataframe(st.session_state["matchups"], width="stretch")
    else:
        st.info("Game matchups will appear after running the simulation.")

with tab3:
    st.header("17,500 Optimal Appearance Rates")
    if "sim_results" in st.session_state:
        df_sim = st.session_state["sim_results"]
        col1, col2 = st.columns([1, 3])
        with col1:
            min_opt = st.slider("Minimum Optimal %", 0.0, 40.0, 2.0, step=0.5)
        with col2:
            pos_filter = st.multiselect("Filter Positions", ["QB", "RB", "WR", "TE", "DST"], default=["QB", "RB", "WR", "TE", "DST"])
        valid_cols = [c for c in cols_to_display if c in df_sim.columns]
        filtered = df_sim[(df_sim["optimal_%"] >= min_opt) & (df_sim["position"].isin(pos_filter))]
        st.dataframe(filtered[valid_cols], width="stretch")
    else:
        st.warning("No simulation data found.")

with tab4:
    st.header("Target Contests ($0.25 - $30.00)")
    if "contests" in st.session_state:
        st.dataframe(st.session_state["contests"][["name", "entry_fee", "prize_pool", "multiplier"]], width="stretch")
    else:
        st.info("Contest data will load once you run a simulation.")
