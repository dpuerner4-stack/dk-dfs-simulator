import streamlit as st
import pandas as pd
import numpy as np
import requests
import io
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
DEFAULT_SIMULATIONS = 6700

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.draftkings.com",
    "Referer": "https://www.draftkings.com/"
})

def get_current_et_str(fmt="%A, %b %d, %Y at %I:%M %p ET"):
    return datetime.now(ET_TZ).strftime(fmt)

def format_utc_to_et(utc_str):
    if not utc_str or utc_str == "TBD":
        return "TBD"
    try:
        clean_str = utc_str.replace("Z", "").split(".")[0]
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

# --- EMAIL DIGEST GENERATOR ---
def generate_email_html(optimal_roster, sim_results, matchups_df, slate_title, slate_type):
    pos_colors = {"QB": "#e06666", "RB": "#6fa8dc", "WR": "#ffd966", "TE": "#93c47d", "DST": "#8e7cc3", "K": "#b4a7d6", "CPT": "#f6b26b", "FLEX": "#6fa8dc"}

    matchup_badges = ""
    if isinstance(matchups_df, pd.DataFrame) and not matchups_df.empty:
        for _, row in matchups_df.iterrows():
            matchup_badges += f"""
            <span style="display: inline-block; background-color: #f1f3f5; border: 1px solid #dee2e6; border-radius: 6px; padding: 6px 12px; margin: 4px; font-weight: 600; font-size: 13px; color: #343a40;">
                🏈 {row['matchup']} &nbsp;<span style="font-weight: 400; color: #6c757d;">({row.get('start_time_et', 'TBD')})</span>
            </span>
            """
    else:
        matchup_badges = "<span style='color: #6c757d; font-size: 13px;'>No specific matchups found.</span>"

    lineup_rows = ""
    for idx, row in optimal_roster.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        slot = str(row.get("roster_slot", row.get("position", "")))
        badge_color = pos_colors.get(slot, "#adb5bd")
        text_color = "#000000" if slot in ["WR", "RB", "TE", "CPT", "FLEX"] else "#ffffff"
        crown = "👑 " if slot == "CPT" else ""
        
        lineup_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{crown}{slot}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('position', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
            <td style="padding: 10px 12px; color: #6c757d; font-size: 12px;">{row.get('matchup', '')}</td>
            <td style="padding: 10px 12px; font-weight: 600; color: #198754;">${int(row.get('salary', 0)):,}</td>
            <td style="padding: 10px 12px; font-weight: 600;">{float(row.get('proj_fpts', 0)):.1f}</td>
            <td style="padding: 10px 12px; color: #0d6efd; font-weight: 600;">{float(row.get('optimal_%', 0)):.2f}%</td>
            <td style="padding: 10px 12px; font-weight: 600;">{float(row.get('leverage', 0)):.2f}x</td>
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
                <h2 style="margin: 0 0 6px 0; font-size: 22px; font-weight: 800;">🏈 {slate_title}</h2>
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

                <h3>🏆 Optimal Lineup (100% DK Legal)</h3>
                <table class="table-wrap">
                    <thead>
                        <tr><th>Slot</th><th>Player</th><th>Pos</th><th>Team</th><th>Matchup</th><th>Salary</th><th>Fpts</th><th>Opt %</th><th>Lev</th></tr>
                    </thead>
                    <tbody>{lineup_rows}</tbody>
                </table>
            </div>
            <div class="footer">Automated report via Streamlit & GitHub Actions • Monte Carlo (6,700 iterations)</div>
        </div>
    </body>
    </html>
    """

def send_email_report(optimal_roster, sim_results, matchups_df, slate_title, slate_type, sender_email, sender_password, recipient_email):
    if not sender_email or not sender_password or not recipient_email:
        return False, "Missing credentials"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🏈 DraftKings Lineup: {slate_title} ({optimal_roster['proj_fpts'].sum():.1f} FPTS)"
        msg["From"] = sender_email
        msg["To"] = recipient_email

        html = generate_email_html(optimal_roster, sim_results, matchups_df, slate_title, slate_type)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        return True, "Delivered"
    except Exception as e:
        return False, str(e)

def get_candidate_contests(target_mode="Auto-Detect Next Slate"):
    url = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
    resp = SESSION.get(url, timeout=12)
    if resp.status_code != 200 or not resp.text.strip():
        return []

    res = resp.json()
    contests = []

    for c in res.get("Contests", []):
        fee = float(c.get("a", 0))
        pool = float(c.get("po", 0))
        dg = c.get("dg")
        name = c.get("n", "")

        # Block tournaments without player pools
        if "midseason" in name.lower() or "qualifier" in name.lower():
            continue

        game_type = str(c.get("gameType", "")).lower()
        is_sd = "showdown" in name.lower() or "single game" in name.lower() or "showdown" in game_type

        if fee >= 0.25 and pool >= 1000 and dg:
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
        return []

    if target_mode == "Sunday Main Slate (Classic)":
        matched = df[~df["is_showdown"] & (df["name"].str.contains("Millionaire", case=False, na=False) | df["name"].str.contains("Play-Action", case=False, na=False) | df["name"].str.contains("Main", case=False, na=False))]
        if matched.empty: matched = df[~df["is_showdown"]]
    elif target_mode == "Thursday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("TNF", case=False, na=False) | df["name"].str.contains("Thursday", case=False, na=False))]
        if matched.empty: matched = df[df["is_showdown"]]
    elif target_mode == "Sunday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("SNF", case=False, na=False) | df["name"].str.contains("Sunday Night", case=False, na=False))]
        if matched.empty: matched = df[df["is_showdown"]]
    elif target_mode == "Monday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("MNF", case=False, na=False) | df["name"].str.contains("Monday", case=False, na=False))]
        if matched.empty: matched = df[df["is_showdown"]]
    else:
        matched = df[~df["is_showdown"] & (df["name"].str.contains("Millionaire", case=False, na=False) | df["name"].str.contains("Play-Action", case=False, na=False) | df["name"].str.contains("Main", case=False, na=False))]
        if matched.empty: matched = df[~df["is_showdown"]]

    if matched.empty:
        matched = df

    return matched.sort_values(by="prize_pool", ascending=False).to_dict(orient="records")

def fetch_player_pool_by_dg(draft_group_id, target_slate_type):
    matchups_df = pd.DataFrame()
    team_opponents = {}
    base_list = []

    url_csv = f"https://www.draftkings.com/lineup/getavailableplayerscsv?draftGroupId={draft_group_id}"
    try:
        r_csv = SESSION.get(url_csv, timeout=10)
        if r_csv.status_code == 200 and "Position" in r_csv.text:
            csv_data = pd.read_csv(io.StringIO(r_csv.text))
            if "Game Info" in csv_data.columns:
                for g in csv_data["Game Info"].dropna().unique():
                    parts = g.split()
                    m_name = parts[0].replace("@", " @ ")
                    start_str = " ".join(parts[1:]) if len(parts) > 1 else "TBD"
                    matchups_df = pd.concat([matchups_df, pd.DataFrame([{"matchup": m_name, "start_time_et": start_str}])], ignore_index=True)
                    if "@" in parts[0]:
                        away, home = parts[0].split("@")
                        team_opponents[away] = home
                        team_opponents[home] = away

            player_dict = {}
            for _, row in csv_data.iterrows():
                name = str(row.get("Name", "")).strip()
                pos = str(row.get("Position", "UTIL")).strip().upper()
                roster_pos = str(row.get("Roster Position", pos)).strip().upper()
                sal = float(row.get("Salary", 0))
                team = str(row.get("TeamAbbrev", "")).strip()
                fppg = float(row.get("AvgPointsPerGame", 0.0))
                pid = str(row.get("ID", name))
                matchup = str(row.get("Game Info", "")).split()[0].replace("@", " @ ") if row.get("Game Info") else ""

                is_cpt = (roster_pos == "CPT") or (pos == "CPT")
                base_sal = round(sal / 1.5) if is_cpt else sal

                if pid not in player_dict:
                    player_dict[pid] = {
                        "player_id": pid, "name": name,
                        "position": pos if pos != "CPT" else "UTIL",
                        "team": team, "matchup": matchup,
                        "salary": base_sal, "raw_fppg": fppg
                    }
                else:
                    if not is_cpt: player_dict[pid]["salary"] = base_sal
                    if fppg > player_dict[pid]["raw_fppg"]: player_dict[pid]["raw_fppg"] = fppg

            base_list = list(player_dict.values())
    except Exception:
        base_list = []

    if not base_list:
        return None, None, None, None

    num_games = len(matchups_df.drop_duplicates())
    actual_slate_type = "Classic" if num_games > 1 else target_slate_type

    entries = []
    if actual_slate_type == "Showdown":
        for p in base_list:
            proj = p["raw_fppg"] if p["raw_fppg"] > 0 else round(p["salary"] / 450.0, 2)
            entries.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"], "roster_slot": "FLEX", "team": p["team"], "matchup": p["matchup"], "salary": int(p["salary"]), "proj_fpts": proj})
            entries.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"], "roster_slot": "CPT", "team": p["team"], "matchup": p["matchup"], "salary": int(round(p["salary"] * 1.5)), "proj_fpts": round(proj * 1.5, 2)})
    else:
        for p in base_list:
            proj = p["raw_fppg"] if p["raw_fppg"] > 0 else round(p["salary"] / 450.0, 2)
            entries.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"], "roster_slot": p["position"], "team": p["team"], "matchup": p["matchup"], "salary": int(p["salary"]), "proj_fpts": proj})

    pool_df = pd.DataFrame(entries)
    multipliers = {"QB": (0.35, 3.0), "RB": (0.40, 2.5), "WR": (0.55, 2.0), "TE": (0.45, 1.5), "DST": (0.65, 2.0), "CPT": (0.50, 3.0), "FLEX": (0.45, 2.0)}
    pool_df["std_dev"] = pool_df.apply(lambda r: round(r["proj_fpts"] * multipliers.get(r["roster_slot"], (0.45, 2.0))[0] + multipliers.get(r["roster_slot"], (0.45, 2.0))[1], 2), axis=1)

    return pool_df.reset_index(drop=True), matchups_df.drop_duplicates(), team_opponents, actual_slate_type

# --- STRICT MATHEMATICAL SOLVER (ZERO POSITION DRIFT) ---
def solve_lineup(df, scores, slate_type, team_opponents=None):
    solver = pywraplp.Solver.CreateSolver("CBC")
    if not solver:
        return None
    n = len(df)
    x = [solver.BoolVar(f"x_{i}") for i in range(n)]

    obj = solver.Objective()
    for i in range(n):
        obj.SetCoefficient(x[i], float(scores[i]))
    obj.SetMaximization()

    # Total Salary <= $50,000
    sal_ct = solver.Constraint(0, 50000)
    for i in range(n):
        sal_ct.SetCoefficient(x[i], int(df.loc[i, "salary"]))

    if slate_type == "Showdown":
        tot_ct = solver.Constraint(6, 6)
        cpt_ct = solver.Constraint(1, 1)
        flex_ct = solver.Constraint(5, 5)

        for i in range(n):
            tot_ct.SetCoefficient(x[i], 1)
            slot = df.loc[i, "roster_slot"]
            if slot == "CPT": cpt_ct.SetCoefficient(x[i], 1)
            elif slot == "FLEX": flex_ct.SetCoefficient(x[i], 1)

        for pid, indices in df.groupby("player_id").groups.items():
            if len(indices) > 1:
                p_ct = solver.Constraint(0, 1)
                for idx in indices: p_ct.SetCoefficient(x[idx], 1)

        teams = [t for t in df["team"].unique() if t]
        if len(teams) >= 2:
            for tm in teams:
                tm_ct = solver.Constraint(1, 5)
                for i in range(n):
                    if df.loc[i, "team"] == tm: tm_ct.SetCoefficient(x[i], 1)
    else:
        # EXACT DRAFTKINGS 9-MAN ROSTER:
        # Exactly 1 QB, Exactly 1 DST, Exactly 1 TE (+1 optional via FLEX), 
        # 2-3 RB, 3-4 WR, Total Skill (RB+WR+TE) = 7, Total Roster = 9
        tot_ct = solver.Constraint(9, 9)
        qb_ct = solver.Constraint(1, 1)
        dst_ct = solver.Constraint(1, 1)
        rb_ct = solver.Constraint(2, 3)
        wr_ct = solver.Constraint(3, 4)
        te_ct = solver.Constraint(1, 2)
        skill_ct = solver.Constraint(7, 7)

        for i, pos in enumerate(df["position"]):
            tot_ct.SetCoefficient(x[i], 1)
            if pos == "QB":
                qb_ct.SetCoefficient(x[i], 1)
            elif pos == "DST":
                dst_ct.SetCoefficient(x[i], 1)
            elif pos == "RB":
                rb_ct.SetCoefficient(x[i], 1)
                skill_ct.SetCoefficient(x[i], 1)
            elif pos == "WR":
                wr_ct.SetCoefficient(x[i], 1)
                skill_ct.SetCoefficient(x[i], 1)
            elif pos == "TE":
                te_ct.SetCoefficient(x[i], 1)
                skill_ct.SetCoefficient(x[i], 1)

        # Primary QB Stack (At least 1 WR/TE from same team)
        for _, qb_row in df[df["position"] == "QB"].iterrows():
            qb_idx = qb_row.name
            team = qb_row["team"]
            partners = df[(df["team"] == team) & (df["position"].isin(["WR", "TE"]))].index.tolist()
            if partners:
                stk_ct = solver.Constraint(0, solver.infinity())
                stk_ct.SetCoefficient(x[qb_idx], -1)
                for p_idx in partners: stk_ct.SetCoefficient(x[p_idx], 1)

        # DST anti-correlation (Never play DST against your starting QB)
        if team_opponents:
            for _, qb_row in df[df["position"] == "QB"].iterrows():
                qb_idx = qb_row.name
                opp = team_opponents.get(qb_row["team"])
                if opp:
                    for d_idx in df[(df["team"] == opp) & (df["position"] == "DST")].index.tolist():
                        anti = solver.Constraint(0, 1)
                        anti.SetCoefficient(x[qb_idx], 1)
                        anti.SetCoefficient(x[d_idx], 1)

    status = solver.Solve()
    if status in [pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE]:
        selected = [i for i in range(n) if x[i].solution_value() > 0.5]
        lineup = df.loc[selected].copy()
        
        if slate_type == "Showdown":
            lineup["order"] = lineup["roster_slot"].map(lambda p: 0 if p == "CPT" else 1)
        else:
            # Assign true DraftKings slot tags (QB, RB1, RB2, WR1, WR2, WR3, TE, FLEX, DST)
            assigned_slots = []
            rb_count = 0
            wr_count = 0
            te_count = 0
            
            for _, r in lineup.iterrows():
                p = r["position"]
                if p == "QB":
                    assigned_slots.append("QB")
                elif p == "DST":
                    assigned_slots.append("DST")
                elif p == "RB":
                    rb_count += 1
                    assigned_slots.append("RB" if rb_count <= 2 else "FLEX")
                elif p == "WR":
                    wr_count += 1
                    assigned_slots.append("WR" if wr_count <= 3 else "FLEX")
                elif p == "TE":
                    te_count += 1
                    assigned_slots.append("TE" if te_count <= 1 else "FLEX")
                else:
                    assigned_slots.append("FLEX")

            lineup["roster_slot"] = assigned_slots
            pos_map = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "FLEX": 5, "DST": 6}
            lineup["order"] = lineup["roster_slot"].map(lambda p: pos_map.get(p, 9))
            
        return lineup.sort_values(by="order").drop(columns=["order"])
    return None

def run_monte_carlo(df, slate_type, team_opponents, num_sims=6700):
    n = len(df)
    sim_matrix = np.random.normal(df["proj_fpts"].to_numpy(), df["std_dev"].to_numpy(), size=(num_sims, n))
    sim_matrix = np.clip(sim_matrix, 0, None)

    counts = np.zeros(n, dtype=np.int32)
    step = 500

    for b in range(0, num_sims, step):
        end = min(b + step, num_sims)
        for s in range(b, end):
            res = solve_lineup(df, sim_matrix[s], slate_type, team_opponents)
            if res is not None and not res.empty:
                for idx in res.index: counts[idx] += 1

        pct = 15 + int((end / num_sims) * 70)
        set_status(True, f"Simulating {end:,} / {num_sims:,} slates...", progress=pct, slate_type=slate_type)

    df["optimal_%"] = np.round((counts / num_sims) * 100, 2)
    df["leverage"] = np.round(df["optimal_%"] / (df["salary"] / 1000), 2)
    return df.sort_values(by="optimal_%", ascending=False)

def background_task(target_mode, sender, pw, rec, num_sims=6700):
    try:
        set_status(True, f"Scanning for {target_mode}...", progress=5)
        candidates = get_candidate_contests(target_mode)
        if not candidates:
            set_status(False, f"No active contests found for {target_mode}.", progress=0)
            return

        players_df, matchups_df, team_opponents, actual_slate_type = None, None, None, None
        chosen_contest = None

        for c in candidates:
            dg = c["draft_group_id"]
            title = c["name"]
            stype = "Showdown" if c["is_showdown"] else "Classic"
            set_status(True, f"Checking pool for {title}...", progress=12, slate_name=title, slate_type=stype)
            pdf, mdf, topps, act_stype = fetch_player_pool_by_dg(dg, stype)
            if pdf is not None and not pdf.empty:
                players_df, matchups_df, team_opponents, actual_slate_type = pdf, mdf, topps, act_stype
                chosen_contest = c
                break

        if players_df is None or players_df.empty:
            set_status(False, "Could not find an active draft group with published salaries.", progress=0)
            return

        slate_title = chosen_contest["name"]
        set_status(True, f"Downloaded {len(players_df)} players for {slate_title}...", progress=20, slate_name=slate_title, slate_type=actual_slate_type)

        sim_results = run_monte_carlo(players_df, actual_slate_type, team_opponents, num_sims=num_sims)

        set_status(True, f"Solving optimal {actual_slate_type} roster...", progress=88, slate_name=slate_title, slate_type=actual_slate_type)
        optimal_roster = solve_lineup(sim_results, sim_results["proj_fpts"].to_numpy(), actual_slate_type, team_opponents)

        if optimal_roster is None or optimal_roster.empty:
            set_status(False, "Failed to resolve lineup within salary cap.", progress=0)
            return

        optimal_roster.to_csv(CACHE_ROSTER, index=False)
        sim_results.to_csv(CACHE_SIM, index=False)
        matchups_df.to_csv(CACHE_MATCHUPS, index=False)

        run_ts = get_current_et_str("%Y-%m-%d %I:%M %p ET")
        email_msg = ""
        if sender and pw and rec:
            set_status(True, "Delivering email digest...", progress=95, slate_name=slate_title, slate_type=actual_slate_type)
            ok, emsg = send_email_report(optimal_roster, sim_results, matchups_df, slate_title, actual_slate_type, sender, pw, rec)
            email_msg = f" | Email: {'Delivered' if ok else emsg}"
        else:
            email_msg = " | Email: Skipped"

        set_status(False, f"Completed successfully ({actual_slate_type}){email_msg}", progress=100, last_run=run_ts, slate_name=slate_title, slate_type=actual_slate_type)
    except Exception as e:
        set_status(False, f"Error: {e}", progress=0)

if len(sys.argv) > 1 and sys.argv[1] == "--cron":
    cron_target = sys.argv[2] if len(sys.argv) > 2 else "Auto-Detect Next Slate"
    s_email = os.environ.get("EMAIL_SENDER", "")
    s_pw = os.environ.get("EMAIL_PASSWORD", "")
    s_rec = os.environ.get("EMAIL_RECIPIENT", "")
    background_task(cron_target, s_email, s_pw, s_rec, 6700)
    sys.exit(0)

# --- USER INTERFACE ---
with st.sidebar:
    st.header("⚙️ Slate Configuration")
    slate_selection = st.selectbox(
        "Target Game Window",
        [
            "Sunday Main Slate (Classic)",
            "Auto-Detect Next Slate",
            "Thursday Night Football (Showdown)",
            "Sunday Night Football (Showdown)",
            "Monday Night Football (Showdown)"
        ]
    )
    num_simulations = st.number_input("Monte Carlo Sample Size", min_value=1000, max_value=20000, value=6700, step=500)

    st.header("📧 Email Notifications")
    send_email = st.checkbox("Email report when simulation runs", value=True)
    sender_email = st.secrets.get("EMAIL_SENDER", "") if "EMAIL_SENDER" in st.secrets else ""
    sender_pw = st.secrets.get("EMAIL_PASSWORD", "") if "EMAIL_PASSWORD" in st.secrets else ""
    recipient_email = st.secrets.get("EMAIL_RECIPIENT", "") if "EMAIL_RECIPIENT" in st.secrets else ""

    if not sender_email: sender_email = st.text_input("Sender Gmail", "")
    if not sender_pw: sender_pw = st.text_input("Gmail App Password", type="password")
    if not recipient_email: recipient_email = st.text_input("Recipient Email", "")

st.title("🏈 DraftKings Slate Scanner & Optimizer")

status = get_status()

col_btn, col_info = st.columns([1, 2])
with col_btn:
    if status["running"]:
        st.button("⏳ Solving in background...", width="stretch", disabled=True)
    else:
        if st.button(f"🚀 Run Live {num_simulations:,} Simulation", width="stretch", type="primary"):
            pw = sender_pw if send_email else ""
            t = threading.Thread(
                target=background_task,
                args=(slate_selection, sender_email, pw, recipient_email, int(num_simulations)),
                daemon=True
            )
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
        st.caption(f"Slate: **{status.get('slate_name', 'Main')}** ({status.get('slate_type', 'Classic')}) | Status: **{status.get('msg', 'Idle')}**")

# --- TABLES ---
tab1, tab2, tab3 = st.tabs(["🏆 Optimal Lineup", "🏟️ Slate Games", "⚡ Simulated Exposures"])
cols_to_display = ["roster_slot", "name", "position", "team", "matchup", "salary", "proj_fpts", "optimal_%", "leverage"]

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
        st.header("Optimal Appearance Rates")
        col1, col2 = st.columns([1, 3])
        with col1:
            min_opt = st.slider("Minimum Optimal %", 0.0, 40.0, 1.0, step=0.5)
        with col2:
            default_slots = [p for p in ["QB", "RB", "WR", "TE", "DST", "CPT", "FLEX"] if p in sim_df["roster_slot"].values]
            slot_filter = st.multiselect("Filter Slots", sim_df["roster_slot"].unique().tolist(), default=default_slots)
        valid_cols = [c for c in cols_to_display if c in sim_df.columns]
        filtered = sim_df[(sim_df["optimal_%"] >= min_opt) & (sim_df["roster_slot"].isin(slot_filter))]
        st.dataframe(filtered[valid_cols], width="stretch")
else:
    with tab1:
        st.info("No cached run found yet. Select your slate in the sidebar and tap **Run Live Simulation**.")
