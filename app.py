import streamlit as st
import pandas as pd
import numpy as np
import requests
from ortools.linear_solver import pywraplp
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import json
import os
import sys

st.set_page_config(page_title="DraftKings Optimizer", layout="wide")

ET_TZ = ZoneInfo("America/New_York")

def get_current_et_str(fmt="%A, %b %d, %Y at %I:%M %p ET"):
    return datetime.now(ET_TZ).strftime(fmt)

def format_utc_to_et(utc_str):
    if not utc_str or utc_str == "TBD":
        return "TBD"
    try:
        clean_str = utc_str.replace("Z", "")
        if "." in clean_str:
            clean_str = clean_str.split(".")[0]
        dt_utc = datetime.fromisoformat(clean_str).replace(tzinfo=ZoneInfo("UTC"))
        return dt_utc.astimezone(ET_TZ).strftime("%a %I:%M %p ET")
    except Exception:
        return utc_str

CACHE_DIR = "sim_cache"
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_ROSTER = os.path.join(CACHE_DIR, "latest_roster.csv")
CACHE_SIM = os.path.join(CACHE_DIR, "latest_sim.csv")
CACHE_MATCHUPS = os.path.join(CACHE_DIR, "latest_matchups.csv")
CACHE_STATUS = os.path.join(CACHE_DIR, "status.json")

# --- BACKGROUND THREAD STATUS HELPERS ---
def get_status():
    if os.path.exists(CACHE_STATUS):
        try:
            with open(CACHE_STATUS, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"running": False, "msg": "Idle", "progress": 0, "last_run": "Never", "slate_name": "Main Slate", "slate_type": "Classic"}

def set_status(running, msg, progress=0, last_run=None, slate_name=None, slate_type=None):
    current = get_status()
    payload = {
        "running": running,
        "msg": msg,
        "progress": int(progress),
        "last_run": last_run if last_run else current.get("last_run", "Never"),
        "slate_name": slate_name if slate_name else current.get("slate_name", "Main Slate"),
        "slate_type": slate_type if slate_type else current.get("slate_type", "Classic")
    }
    with open(CACHE_STATUS, "w") as f:
        json.dump(payload, f)

# --- MODERN EMAIL TEMPLATE GENERATOR ---
def generate_email_html(optimal_roster, sim_results, matchups_df, slate_title, slate_type):
    pos_colors = {"QB": "#e06666", "RB": "#6fa8dc", "WR": "#ffd966", "TE": "#93c47d", "DST": "#8e7cc3", "CPT": "#f6b26b", "FLEX": "#6fa8dc"}

    matchup_badges = ""
    if isinstance(matchups_df, pd.DataFrame) and not matchups_df.empty:
        for _, row in matchups_df.iterrows():
            matchup_badges += f"""
            <span style="display: inline-block; background-color: #f1f3f5; border: 1px solid #dee2e6; border-radius: 6px; padding: 6px 12px; margin: 4px; font-weight: 600; font-size: 13px; color: #343a40;">
                🏈 {row['matchup']} &nbsp;<span style="font-weight: 400; color: #6c757d;">({row.get('start_time_et', 'TBD')})</span>
            </span>
            """
    else:
        matchup_badges = "<span style='color: #6c757d; font-size: 13px;'>No specific slate games found.</span>"

    lineup_rows = ""
    for idx, row in optimal_roster.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        pos = str(row.get("slot", row.get("position", "")))
        badge_color = pos_colors.get(pos, "#adb5bd")
        text_color = "#000000" if pos in ["WR", "RB", "TE", "CPT", "FLEX"] else "#ffffff"
        
        lineup_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{pos}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
            <td style="padding: 10px 12px; color: #6c757d; font-size: 12px;">{row.get('matchup', '')}</td>
            <td style="padding: 10px 12px; font-weight: 600; color: #198754;">${int(row.get('salary', 0)):,}</td>
            <td style="padding: 10px 12px; font-weight: 600;">{float(row.get('proj_fpts', 0)):.1f}</td>
            <td style="padding: 10px 12px; color: #0d6efd; font-weight: 600;">{float(row.get('optimal_%', 0)):.2f}%</td>
            <td style="padding: 10px 12px; font-weight: 600;">{float(row.get('leverage', 0)):.2f}x</td>
        </tr>
        """

    top_exposures = sim_results.head(10)
    exposure_rows = ""
    for idx, row in top_exposures.reset_index().iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        pos = str(row.get("position", ""))
        badge_color = pos_colors.get(pos, "#adb5bd")
        text_color = "#000000" if pos in ["WR", "RB", "TE", "CPT", "FLEX"] else "#ffffff"

        exposure_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{pos}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
            <td style="padding: 10px 12px; color: #6c757d; font-size: 12px;">{row.get('matchup', '')}</td>
            <td style="padding: 10px 12px; font-weight: 600; color: #198754;">${int(row.get('salary', 0)):,}</td>
            <td style="padding: 10px 12px;">{float(row.get('proj_fpts', 0)):.1f}</td>
            <td style="padding: 10px 12px; color: #0d6efd; font-weight: 700;">{float(row.get('optimal_%', 0)):.2f}%</td>
            <td style="padding: 10px 12px; font-weight: 700; color: #d63384;">{float(row.get('leverage', 0)):.2f}x</td>
        </tr>
        """

    total_sal = int(optimal_roster['salary'].sum())
    rem_sal = 50000 - total_sal
    total_proj = float(optimal_roster['proj_fpts'].sum())

    return f"""
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
            h3 {{ font-size: 16px; margin: 20px 0 8px 0; color: #1e3a8a; }}
            .footer {{ background-color: #f8f9fa; border-top: 1px solid #e9ecef; padding: 16px; text-align: center; font-size: 11px; color: #adb5bd; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2 style="margin: 0 0 6px 0; font-size: 22px; font-weight: 800;">🏈 {slate_title} Report</h2>
                <div style="font-size: 13px; opacity: 0.85;">Generated on {get_current_et_str()} ({slate_type})</div>
            </div>
            <div class="content">
                <div style="margin-bottom: 16px;">
                    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #6c757d; margin-bottom: 6px;">Included Matchups (Eastern Time)</div>
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

                <h3>🏆 Optimal Lineup (Precision Optimized)</h3>
                <table class="table-wrap">
                    <thead>
                        <tr><th>Pos</th><th>Player</th><th>Team</th><th>Matchup</th><th>Salary</th><th>Fpts</th><th>Opt %</th><th>Lev</th></tr>
                    </thead>
                    <tbody>{lineup_rows}</tbody>
                </table>

                <h3>⚡ Top 10 Simulated Target Exposures</h3>
                <table class="table-wrap">
                    <thead>
                        <tr><th>Pos</th><th>Player</th><th>Team</th><th>Matchup</th><th>Salary</th><th>Fpts</th><th>Opt %</th><th>Lev</th></tr>
                    </thead>
                    <tbody>{exposure_rows}</tbody>
                </table>
            </div>
            <div class="footer">Automated report via Streamlit & GitHub Actions • Monte Carlo (17,500 iterations)</div>
        </div>
    </body>
    </html>
    """

def send_email_report(optimal_roster, sim_results, matchups_df, slate_title, slate_type, sender_email, sender_password, recipient_email):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🏈 DraftKings Lineup Alert: {slate_title} - {optimal_roster['proj_fpts'].sum():.1f} FPTS (${int(optimal_roster['salary'].sum()):,})"
        msg["From"] = sender_email
        msg["To"] = recipient_email

        html = generate_email_html(optimal_roster, sim_results, matchups_df, slate_title, slate_type)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        return True, "Email report sent successfully!"
    except Exception as e:
        return False, f"Failed to send email: {e}"

# --- SLATE SCANNER WITH EXPLICIT WINDOW FILTERING ---
def get_target_slate(target_mode="Auto-Detect Next Slate", min_fee=0.25, max_fee=30.0, min_pool=5000):
    url = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
    except Exception:
        return pd.DataFrame(), None, "Classic", "Unknown Slate"

    contests = []
    for c in res.get("Contests", []):
        fee = float(c.get("a", 0))
        pool = float(c.get("po", 0))
        dg = c.get("dg")
        name = c.get("n", "")
        game_type = c.get("gameType", "")
        is_sd = "showdown" in name.lower() or "single game" in name.lower() or game_type in ["Showdown", "SingleGame"]

        if fee >= min_fee and dg:
            contests.append({
                "contest_id": c.get("id"),
                "name": name,
                "entry_fee": fee,
                "prize_pool": pool,
                "draft_group_id": dg,
                "is_showdown": is_sd
            })

    df = pd.DataFrame(contests)
    if df.empty:
        return pd.DataFrame(), None, "Classic", "No Slates Found"

    # Filter based on user target selection
    matched_df = df.copy()
    if target_mode == "Sunday Main Slate (Classic)":
        matched_df = df[~df["is_showdown"] & df["name"].str.contains("Main", case=False, na=False)]
        if matched_df.empty:
            matched_df = df[~df["is_showdown"]]
    elif target_mode == "Thursday Night Football (Showdown)":
        matched_df = df[df["is_showdown"] & (df["name"].str.contains("TNF", case=False, na=False) | df["name"].str.contains("Thursday", case=False, na=False))]
    elif target_mode == "Sunday Night Football (Showdown)":
        matched_df = df[df["is_showdown"] & (df["name"].str.contains("SNF", case=False, na=False) | df["name"].str.contains("Sunday Night", case=False, na=False))]
    elif target_mode == "Monday Night Football (Showdown)":
        matched_df = df[df["is_showdown"] & (df["name"].str.contains("MNF", case=False, na=False) | df["name"].str.contains("Monday", case=False, na=False))]

    if matched_df.empty:
        matched_df = df  # fallback to top contest

    matched_df = matched_df.sort_values(by="prize_pool", ascending=False)
    best = matched_df.iloc[0]
    slate_type = "Showdown" if best["is_showdown"] else "Classic"
    slate_title = best["name"]

    return matched_df, best["draft_group_id"], slate_type, slate_title

def fetch_player_pool(draft_group_id, slate_type):
    url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables?format=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers, timeout=10).json()
    
    competitions = res.get("competitions", [])
    games = []
    team_opponents = {}
    for comp in competitions:
        name = comp.get("name", "")
        start_raw = comp.get("startTime", "")
        start_et = format_utc_to_et(start_raw)
        games.append({"matchup": name, "start_time_et": start_et})
        
        if "@" in name:
            away, home = [t.strip() for t in name.split("@")]
            team_opponents[away] = home
            team_opponents[home] = away

    matchups_df = pd.DataFrame(games)
    draftables = res.get("draftables", [])
    players = []

    for p in draftables:
        if p.get("status", "None") in ["O", "IR", "D", "PUP", "SUS"]:
            continue
        salary = float(p.get("salary", 0))
        if salary <= 0:
            continue

        raw_pos = p.get("position") or "UTIL"
        name = p.get("displayName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
        team = p.get("teamAbbreviation", "")
        matchup = p.get("competition", {}).get("name", "")
        p_id = p.get("playerId", name)

        fppg = 0.0
        for stat in p.get("draftStatAttributes", []):
            if stat.get("id") == 90 or stat.get("description", "").lower() == "fppg":
                try:
                    fppg = float(stat.get("value", 0))
                except Exception:
                    fppg = 0.0

        if fppg <= 0.0:
            fppg = round(salary / 450.0, 2)

        players.append({
            "player_id": p_id,
            "name": name,
            "position": raw_pos,
            "team": team,
            "matchup": matchup,
            "salary": salary,
            "fppg": fppg,
            "status": p.get("status", "None")
        })

    if not players:
        raise ValueError("No active players found for this DraftKings slate right now.")

    df = pd.DataFrame(players).drop_duplicates(subset=["player_id", "position"])
    df["proj_fpts"] = pd.to_numeric(df["fppg"], errors="coerce").fillna(df["salary"] / 450.0)

    multipliers = {"QB": (0.35, 3.0), "RB": (0.40, 2.5), "WR": (0.55, 2.0), "TE": (0.45, 1.5), "DST": (0.65, 2.0), "CPT": (0.50, 3.0), "FLEX": (0.45, 2.0)}
    def calc_std(r):
        slope, intercept = multipliers.get(r["position"], (0.45, 2.0))
        return round(float(r["proj_fpts"]) * slope + intercept, 2)

    df["std_dev"] = df.apply(calc_std, axis=1)
    return df.reset_index(drop=True), matchups_df, team_opponents

# --- OR-TOOLS LINEAR SOLVER ---
def solve_slate(df, score_column, slate_type, team_opponents=None):
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
    for i in range(n):
        sal_ct.SetCoefficient(x[i], int(df.loc[i, "salary"]))

    if slate_type == "Showdown":
        tot_ct = solver.Constraint(6, 6)
        cpt_ct = solver.Constraint(1, 1)
        flex_ct = solver.Constraint(5, 5)

        for i, pos in enumerate(df["position"]):
            tot_ct.SetCoefficient(x[i], 1)
            if pos == "CPT":
                cpt_ct.SetCoefficient(x[i], 1)
            else:
                flex_ct.SetCoefficient(x[i], 1)

        player_groups = df.groupby("player_id").groups
        for pid, indices in player_groups.items():
            if len(indices) > 1:
                p_ct = solver.Constraint(0, 1)
                for idx in indices:
                    p_ct.SetCoefficient(x[idx], 1)

        teams = [t for t in df["team"].unique() if t]
        if len(teams) >= 2:
            for tm in teams:
                tm_ct = solver.Constraint(1, 5)
                for i in range(n):
                    if df.loc[i, "team"] == tm:
                        tm_ct.SetCoefficient(x[i], 1)
    else:
        qb_ct = solver.Constraint(1, 1)
        dst_ct = solver.Constraint(1, 1)
        rb_ct = solver.Constraint(2, 3)
        wr_ct = solver.Constraint(3, 4)
        te_ct = solver.Constraint(1, 2)
        flex_ct = solver.Constraint(7, 7)
        tot_ct = solver.Constraint(9, 9)

        for i, pos in enumerate(df["position"]):
            tot_ct.SetCoefficient(x[i], 1)
            if pos == "QB": qb_ct.SetCoefficient(x[i], 1)
            elif pos == "DST": dst_ct.SetCoefficient(x[i], 1)
            elif pos == "RB": rb_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
            elif pos == "WR": wr_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
            elif pos == "TE": te_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)

        # Primary QB Stack: At least 1 WR/TE from same team
        qbs = df[df["position"] == "QB"]
        for _, qb_row in qbs.iterrows():
            qb_idx = qb_row.name
            team = qb_row["team"]
            stack_partners = df[(df["team"] == team) & (df["position"].isin(["WR", "TE"]))].index.tolist()
            if stack_partners:
                stack_ct = solver.Constraint(0, solver.infinity())
                stack_ct.SetCoefficient(x[qb_idx], -1)
                for partner_idx in stack_partners:
                    stack_ct.SetCoefficient(x[partner_idx], 1)

        # Anti-Correlation: Do not roster opposing DST against QB
        if team_opponents:
            for _, qb_row in qbs.iterrows():
                qb_idx = qb_row.name
                opp_team = team_opponents.get(qb_row["team"])
                if opp_team:
                    opp_dsts = df[(df["team"] == opp_team) & (df["position"] == "DST")].index.tolist()
                    for dst_idx in opp_dsts:
                        anti_ct = solver.Constraint(0, 1)
                        anti_ct.SetCoefficient(x[qb_idx], 1)
                        anti_ct.SetCoefficient(x[dst_idx], 1)

    if solver.Solve() == pywraplp.Solver.OPTIMAL:
        selected = [i for i in range(n) if x[i].solution_value() > 0.5]
        lineup = df.loc[selected].copy()
        if slate_type == "Showdown":
            lineup["slot"] = lineup["position"]
            lineup["order"] = lineup["position"].map(lambda p: 0 if p == "CPT" else 1)
        else:
            pos_order = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "DST": 5}
            lineup["slot"] = lineup["position"]
            lineup["order"] = lineup["position"].map(lambda p: pos_order.get(p, 9))
        return lineup.sort_values(by="order").drop(columns=["order"])
    return None

def run_simulation_pure(df, slate_type, team_opponents, num_simulations=17500):
    n = len(df)
    sim_matrix = np.random.normal(df["proj_fpts"], df["std_dev"], size=(num_simulations, n))
    sim_matrix = np.clip(sim_matrix, 0, None).astype(np.float32)

    counts = np.zeros(n, dtype=np.int32)
    batch_size = 1000

    for b_start in range(0, num_simulations, batch_size):
        b_end = min(b_start + batch_size, num_simulations)
        for s in range(b_start, b_end):
            df["sim_score"] = sim_matrix[s]
            res = solve_slate(df, "sim_score", slate_type, team_opponents)
            if res is not None:
                for idx in res.index:
                    counts[idx] += 1

        pct = 15 + int((b_end / num_simulations) * 70)
        set_status(True, f"Simulating {b_end:,} / {num_simulations:,} slates with stacking & rules...", progress=pct, slate_type=slate_type)

    df["optimal_%"] = np.round((counts / num_simulations) * 100, 2)
    df["leverage"] = np.round(df["optimal_%"] / (df["salary"] / 1000), 2)
    return df.sort_values(by="optimal_%", ascending=False)

# --- DETACHED WORKER ---
def background_task(target_mode, sender, pw, rec):
    try:
        set_status(True, f"Scanning for {target_mode}...", progress=5)
        contests_df, draft_group_id, slate_type, slate_title = get_target_slate(target_mode)
        if contests_df.empty or not draft_group_id:
            set_status(False, f"No active contests found for {target_mode}.", progress=0)
            return

        set_status(True, f"Downloading {slate_type} player pool & matchups...", progress=15, slate_name=slate_title, slate_type=slate_type)
        players_df, matchups_df, team_opponents = fetch_player_pool(draft_group_id, slate_type)

        sim_results = run_simulation_pure(players_df, slate_type, team_opponents, num_simulations=17500)

        set_status(True, f"Solving optimal {slate_type} roster...", progress=88, slate_name=slate_title, slate_type=slate_type)
        optimal_roster = solve_slate(sim_results, "proj_fpts", slate_type, team_opponents)
        if optimal_roster is None or optimal_roster.empty:
            set_status(False, "Failed to resolve optimal roster within cap constraints.", progress=0)
            return

        optimal_roster.to_csv(CACHE_ROSTER, index=False)
        sim_results.to_csv(CACHE_SIM, index=False)
        matchups_df.to_csv(CACHE_MATCHUPS, index=False)
        
        run_ts = get_current_et_str("%Y-%m-%d %I:%M %p ET")
        if sender and pw and rec:
            set_status(True, "Sending email digest...", progress=94, slate_name=slate_title, slate_type=slate_type)
            send_email_report(optimal_roster, sim_results, matchups_df, slate_title, slate_type, sender, pw, rec)

        set_status(False, f"Completed successfully ({slate_type})", progress=100, last_run=run_ts, slate_name=slate_title, slate_type=slate_type)
    except Exception as e:
        set_status(False, f"Error: {e}", progress=0)

# --- CLI DISPATCH FOR CRON / GITHUB ACTIONS ---
if len(sys.argv) > 1 and sys.argv[1] == "--cron":
    cron_target = sys.argv[2] if len(sys.argv) > 2 else "Auto-Detect Next Slate"
    s_email = os.environ.get("EMAIL_SENDER", "")
    s_pw = os.environ.get("EMAIL_PASSWORD", "")
    s_rec = os.environ.get("EMAIL_RECIPIENT", "")
    print(f"Executing scheduled automated run for: {cron_target}")
    background_task(cron_target, s_email, s_pw, s_rec)
    sys.exit(0)

# --- UI & SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Slate Selection")
    slate_selection = st.selectbox(
        "Target Game Window",
        [
            "Auto-Detect Next Slate",
            "Sunday Main Slate (Classic)",
            "Thursday Night Football (Showdown)",
            "Sunday Night Football (Showdown)",
            "Monday Night Football (Showdown)"
        ]
    )
    st.header("📧 Email Notifications")
    send_email = st.checkbox("Email report when simulation runs", value=True)
    sender_email = st.secrets.get("EMAIL_SENDER", "") if "EMAIL_SENDER" in st.secrets else st.text_input("Sender Gmail", "")
    sender_pw = st.secrets.get("EMAIL_PASSWORD", "") if "EMAIL_PASSWORD" in st.secrets else st.text_input("Gmail App Password", type="password")
    recipient_email = st.secrets.get("EMAIL_RECIPIENT", "") if "EMAIL_RECIPIENT" in st.secrets else st.text_input("Recipient Email", "")

st.title("🏈 DraftKings Slate Scanner & Optimizer")

status = get_status()

col_btn, col_info = st.columns([1, 2])
with col_btn:
    if status["running"]:
        st.button("⏳ Solving in background...", width="stretch", disabled=True)
    else:
        if st.button("🚀 Run Live 17,500 Simulation", width="stretch", type="primary"):
            pw = sender_pw if send_email else ""
            t = threading.Thread(target=background_task, args=(slate_selection, sender_email, pw, recipient_email), daemon=True)
            t.start()
            set_status(True, f"Scanning {slate_selection}...", progress=2)
            st.rerun()

with col_info:
    if status["running"]:
        curr_pct = status.get("progress", 0)
        curr_msg = status.get("msg", "Processing...")
        st.progress(curr_pct, text=f"**{curr_pct}%** — {curr_msg} *(Safe to close/sleep screen)*")
        time.sleep(1)
        st.rerun()
    else:
        st.caption(f"Slate: **{status.get('slate_name', 'Main')}** ({status.get('slate_type', 'Classic')}) | Last completed: **{status.get('last_run', 'Never')}**")

# --- DISPLAY TABS ---
tab1, tab2, tab3 = st.tabs(["🏆 Weekly Optimal Lineup", "🏟️ Slate Games", "⚡ Simulated Exposures"])
cols_to_display = ["slot", "position", "name", "team", "matchup", "salary", "proj_fpts", "optimal_%", "leverage"]

if os.path.exists(CACHE_ROSTER) and os.path.exists(CACHE_SIM):
    roster_df = pd.read_csv(CACHE_ROSTER)
    sim_df = pd.read_csv(CACHE_SIM)
    match_df = pd.read_csv(CACHE_MATCHUPS) if os.path.exists(CACHE_MATCHUPS) else pd.DataFrame()

    with tab1:
        st.header(f"Optimal Lineup: {status.get('slate_name', 'Main')} ({status.get('slate_type', 'Classic')})")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Salary", f"${int(roster_df['salary'].sum()):,} / $50,000")
        c2.metric("Projected Points", f"{float(roster_df['proj_fpts'].sum()):.2f}")
        c3.metric("Last Completed Run", status.get("last_run", "N/A"))
        valid_cols = [c for c in cols_to_display if c in roster_df.columns]
        st.dataframe(roster_df[valid_cols], width="stretch")

    with tab2:
        st.header("🏟️ Games on this Slate (Eastern Time)")
        if not match_df.empty:
            st.dataframe(match_df, width="stretch")
        else:
            st.info("No game data found.")

    with tab3:
        st.header("17,500 Optimal Appearance Rates")
        col1, col2 = st.columns([1, 3])
        with col1:
            min_opt = st.slider("Minimum Optimal %", 0.0, 40.0, 2.0, step=0.5)
        with col2:
            default_pos = [p for p in ["CPT", "FLEX", "QB", "RB", "WR", "TE", "DST"] if p in sim_df["position"].values]
            pos_filter = st.multiselect("Filter Positions", sim_df["position"].unique().tolist(), default=default_pos)
        valid_cols = [c for c in cols_to_display if c in sim_df.columns]
        filtered = sim_df[(sim_df["optimal_%"] >= min_opt) & (sim_df["position"].isin(pos_filter))]
        st.dataframe(filtered[valid_cols], width="stretch")
else:
    with tab1:
        st.info("No cached run found yet. Choose your target slate in the sidebar and click **Run Live 17,500 Simulation**.")
