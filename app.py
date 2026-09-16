import streamlit as st
import pandas as pd
import numpy as np
import requests
import io
import csv
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
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
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
    return {"running": False, "msg": "Idle", "progress": 0, "last_run": "Never", "slate_name": "Showdown", "slate_type": "Showdown"}

def set_status(running, msg, progress=0, last_run=None, slate_name=None, slate_type=None):
    current = get_status()
    payload = {
        "running": running,
        "msg": msg,
        "progress": int(progress),
        "last_run": last_run if last_run else current.get("last_run", "Never"),
        "slate_name": slate_name if slate_name else current.get("slate_name", "Showdown"),
        "slate_type": slate_type if slate_type else current.get("slate_type", "Showdown")
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

    top_exposures = sim_results.head(10)
    exposure_rows = ""
    for idx, row in top_exposures.reset_index().iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        slot = str(row.get("roster_slot", ""))
        badge_color = pos_colors.get(slot, "#adb5bd")
        text_color = "#000000" if slot in ["WR", "RB", "TE", "CPT", "FLEX"] else "#ffffff"

        exposure_rows += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #e9ecef; text-align: left; font-size: 13px;">
            <td style="padding: 10px 12px;"><span style="background-color: {badge_color}; color: {text_color}; font-weight: 700; border-radius: 4px; padding: 3px 8px; font-size: 11px;">{slot}</span></td>
            <td style="padding: 10px 12px; font-weight: 600; color: #212529;">{row.get('name', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('position', '')}</td>
            <td style="padding: 10px 12px; color: #495057;">{row.get('team', '')}</td>
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

                <h3>🏆 Optimal Lineup (1 CPT + 5 FLEX)</h3>
                <table class="table-wrap">
                    <thead>
                        <tr><th>Slot</th><th>Player</th><th>Pos</th><th>Team</th><th>Matchup</th><th>Salary</th><th>Fpts</th><th>Opt %</th><th>Lev</th></tr>
                    </thead>
                    <tbody>{lineup_rows}</tbody>
                </table>

                <h3>⚡ Top 10 Simulated Exposures</h3>
                <table class="table-wrap">
                    <thead>
                        <tr><th>Slot</th><th>Player</th><th>Pos</th><th>Team</th><th>Salary</th><th>Fpts</th><th>Opt %</th><th>Lev</th></tr>
                    </thead>
                    <tbody>{exposure_rows}</tbody>
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

# --- LOBBY CONTEST SCANNER ---
def get_target_slate(target_mode="Auto-Detect Next Slate"):
    url = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
    resp = SESSION.get(url, timeout=12)
    if resp.status_code != 200 or not resp.text.strip():
        return None, "Classic", "DraftKings Lobby Unavailable"

    res = resp.json()
    now_et = datetime.now(ET_TZ)
    weekday = now_et.weekday()

    contests = []
    for c in res.get("Contests", []):
        fee = float(c.get("a", 0))
        pool = float(c.get("po", 0))
        dg = c.get("dg")
        name = c.get("n", "")
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

    if not contests:
        return None, "Classic", "No Contests Found"

    df = pd.DataFrame(contests)

    if target_mode == "Auto-Detect Next Slate":
        if weekday in [1, 2, 3]:  # Tue, Wed, Thu -> Target TNF Showdown
            matched = df[df["is_showdown"] & (df["name"].str.contains("TNF", case=False, na=False) | df["name"].str.contains("Thursday", case=False, na=False))]
            if matched.empty:
                matched = df[df["is_showdown"]]
        elif weekday == 0:  # Monday -> MNF Showdown
            matched = df[df["is_showdown"] & (df["name"].str.contains("MNF", case=False, na=False) | df["name"].str.contains("Monday", case=False, na=False))]
            if matched.empty:
                matched = df[df["is_showdown"]]
        elif weekday == 6 and now_et.hour < 16:
            matched = df[~df["is_showdown"] & df["name"].str.contains("Main", case=False, na=False)]
            if matched.empty:
                matched = df[~df["is_showdown"]]
        else:
            matched = df
    elif target_mode == "Thursday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("TNF", case=False, na=False) | df["name"].str.contains("Thursday", case=False, na=False))]
        if matched.empty:
            matched = df[df["is_showdown"]]
    elif target_mode == "Monday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("MNF", case=False, na=False) | df["name"].str.contains("Monday", case=False, na=False))]
        if matched.empty:
            matched = df[df["is_showdown"]]
    elif target_mode == "Sunday Night Football (Showdown)":
        matched = df[df["is_showdown"] & (df["name"].str.contains("SNF", case=False, na=False) | df["name"].str.contains("Sunday Night", case=False, na=False))]
        if matched.empty:
            matched = df[df["is_showdown"]]
    else:
        matched = df[~df["is_showdown"]]

    if matched.empty:
        matched = df

    best = matched.sort_values(by="prize_pool", ascending=False).iloc[0]
    slate_type = "Showdown" if best["is_showdown"] else "Classic"
    return best["draft_group_id"], slate_type, best["name"]

# --- ACCURATE PROJECTIONS ---
def calculate_true_projection(p, starting_qbs):
    name = p["name"]
    pos = p["position"]
    team = p["team"]
    sal = p["salary"]
    raw_fppg = p.get("raw_fppg", 0.0)

    # Filter out backup QBs
    if pos == "QB":
        if team in starting_qbs and name != starting_qbs[team]:
            return 0.0
        return max(raw_fppg, 18.5)

    if raw_fppg and raw_fppg > 4.0:
        return float(raw_fppg)

    # Active tier projections based on salaries
    if sal >= 10000:
        return round(15.0 + (sal - 10000) * 0.0012, 1)
    elif sal >= 7000:
        return round(10.5 + (sal - 7000) * 0.0013, 1)
    elif sal >= 4500:
        return round(6.5 + (sal - 4500) * 0.0014, 1)
    elif sal >= 2000:
        return round(3.5 + (sal - 2000) * 0.0012, 1)
    else:
        return 0.2

# --- HYBRID INGESTION ENGINE (JSON + DIRECT CSV FALLBACK) ---
def fetch_player_pool(draft_group_id, slate_type):
    # Method 1: Try JSON draftables endpoint
    url_json = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables?format=json"
    matchups_df = pd.DataFrame()
    team_opponents = {}
    base_list = []

    try:
        r = SESSION.get(url_json, timeout=10)
        if r.status_code == 200 and r.text.strip():
            data = r.json()
            competitions = data.get("competitions", [])
            for comp in competitions:
                m_name = comp.get("name", "")
                start_et = format_utc_to_et(comp.get("startTime", ""))
                matchups_df = pd.concat([matchups_df, pd.DataFrame([{"matchup": m_name, "start_time_et": start_et}])], ignore_index=True)
                if "@" in m_name:
                    away, home = [t.strip() for t in m_name.split("@")]
                    team_opponents[away] = home
                    team_opponents[home] = away

            raw_players = data.get("draftables", [])
            player_dict = {}
            for p in raw_players:
                if p.get("status", "None") in ["O", "IR", "D", "PUP", "SUS"]:
                    continue
                sal = float(p.get("salary", 0))
                if sal <= 0:
                    continue

                name = p.get("displayName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                team = p.get("teamAbbreviation", "")
                matchup = p.get("competition", {}).get("name", "")
                pid = str(p.get("playerId", name))
                pos = p.get("position") or "UTIL"
                roster_slot_id = p.get("rosterSlotId")
                is_cpt_record = (roster_slot_id == 65) or (pos == "CPT")

                fppg = 0.0
                for stat in p.get("draftStatAttributes", []):
                    if stat.get("id") == 90 or stat.get("description", "").lower() == "fppg":
                        try: fppg = float(stat.get("value", 0))
                        except Exception: pass

                base_sal = round(sal / 1.5) if is_cpt_record else sal
                if pid not in player_dict:
                    player_dict[pid] = {
                        "player_id": pid, "name": name,
                        "position": pos if pos != "CPT" else "UTIL",
                        "team": team, "matchup": matchup,
                        "salary": base_sal, "raw_fppg": fppg
                    }
                else:
                    if not is_cpt_record:
                        player_dict[pid]["salary"] = base_sal
                    if fppg > player_dict[pid]["raw_fppg"]:
                        player_dict[pid]["raw_fppg"] = fppg

            base_list = list(player_dict.values())
    except Exception:
        base_list = []

    # Method 2: Direct CSV Export Fallback (Always open and unblocked on DraftKings)
    if not base_list:
        url_csv = f"https://www.draftkings.com/lineup/getavailableplayerscsv?draftGroupId={draft_group_id}"
        r_csv = SESSION.get(url_csv, timeout=10)
        if r_csv.status_code == 200 and "Position" in r_csv.text:
            csv_data = pd.read_csv(io.StringIO(r_csv.text))
            
            # Extract games from Game Info column (e.g. "DET@BUF 09/17/2026 08:15PM ET")
            if "Game Info" in csv_data.columns:
                unique_games = csv_data["Game Info"].dropna().unique()
                for g in unique_games:
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
                pos = str(row.get("Position", "UTIL")).strip()
                roster_pos = str(row.get("Roster Position", pos)).strip()
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
                    if not is_cpt:
                        player_dict[pid]["salary"] = base_sal
                    if fppg > player_dict[pid]["raw_fppg"]:
                        player_dict[pid]["raw_fppg"] = fppg

            base_list = list(player_dict.values())

    if not base_list:
        raise ValueError(f"DraftGroup {draft_group_id} has not yet released active player pools.")

    # Starters calculation
    starting_qbs = {}
    for p in base_list:
        if p["position"] == "QB":
            t = p["team"]
            if t not in starting_qbs or p["salary"] > starting_qbs[t]["salary"]:
                starting_qbs[t] = p
    starting_qb_names = {t: q["name"] for t, q in starting_qbs.items()}

    entries = []
    if slate_type == "Showdown":
        for p in base_list:
            proj = calculate_true_projection(p, starting_qb_names)
            if proj <= 0.0:
                continue

            # 1. FLEX Row (1.0x)
            entries.append({
                "player_id": p["player_id"],
                "name": p["name"],
                "position": p["position"],
                "roster_slot": "FLEX",
                "team": p["team"],
                "matchup": p["matchup"],
                "salary": int(p["salary"]),
                "proj_fpts": proj
            })

            # 2. CPT Row (1.5x salary & points)
            entries.append({
                "player_id": p["player_id"],
                "name": p["name"],
                "position": p["position"],
                "roster_slot": "CPT",
                "team": p["team"],
                "matchup": p["matchup"],
                "salary": int(round(p["salary"] * 1.5)),
                "proj_fpts": round(proj * 1.5, 2)
            })
    else:
        for p in base_list:
            proj = calculate_true_projection(p, starting_qb_names)
            if proj <= 0.0:
                continue
            entries.append({
                "player_id": p["player_id"],
                "name": p["name"],
                "position": p["position"],
                "roster_slot": p["position"],
                "team": p["team"],
                "matchup": p["matchup"],
                "salary": int(p["salary"]),
                "proj_fpts": proj
            })

    pool_df = pd.DataFrame(entries)
    multipliers = {"QB": (0.35, 3.0), "RB": (0.40, 2.5), "WR": (0.55, 2.0), "TE": (0.45, 1.5), "DST": (0.65, 2.0), "CPT": (0.50, 3.0), "FLEX": (0.45, 2.0)}
    pool_df["std_dev"] = pool_df.apply(lambda r: round(r["proj_fpts"] * multipliers.get(r["roster_slot"], (0.45, 2.0))[0] + multipliers.get(r["roster_slot"], (0.45, 2.0))[1], 2), axis=1)

    return pool_df.reset_index(drop=True), matchups_df.drop_duplicates(), team_opponents

# --- SOLVER ---
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
            if slot == "CPT":
                cpt_ct.SetCoefficient(x[i], 1)
            elif slot == "FLEX":
                flex_ct.SetCoefficient(x[i], 1)

        # Mutual exclusion
        for pid, indices in df.groupby("player_id").groups.items():
            if len(indices) > 1:
                p_ct = solver.Constraint(0, 1)
                for idx in indices:
                    p_ct.SetCoefficient(x[idx], 1)

        # Never draft 2 QBs from the SAME team
        for tm in df["team"].unique():
            if not tm: continue
            tm_qb_indices = df[(df["team"] == tm) & (df["position"] == "QB")].index.tolist()
            if len(tm_qb_indices) > 1:
                qb_tm_ct = solver.Constraint(0, 1)
                for idx in tm_qb_indices:
                    qb_tm_ct.SetCoefficient(x[idx], 1)

        # Both teams represented
        teams = [t for t in df["team"].unique() if t]
        if len(teams) >= 2:
            for tm in teams:
                tm_ct = solver.Constraint(1, 5)
                for i in range(n):
                    if df.loc[i, "team"] == tm:
                        tm_ct.SetCoefficient(x[i], 1)
    else:
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

        for _, qb_row in df[df["position"] == "QB"].iterrows():
            qb_idx = qb_row.name
            team = qb_row["team"]
            partners = df[(df["team"] == team) & (df["position"].isin(["WR", "TE"]))].index.tolist()
            if partners:
                stk_ct = solver.Constraint(0, solver.infinity())
                stk_ct.SetCoefficient(x[qb_idx], -1)
                for p_idx in partners:
                    stk_ct.SetCoefficient(x[p_idx], 1)

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
            pos_map = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "DST": 5}
            lineup["order"] = lineup["position"].map(lambda p: pos_map.get(p, 9))
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
                for idx in res.index:
                    counts[idx] += 1

        pct = 15 + int((end / num_sims) * 70)
        set_status(True, f"Simulating {end:,} / {num_sims:,} slates...", progress=pct, slate_type=slate_type)

    df["optimal_%"] = np.round((counts / num_sims) * 100, 2)
    df["leverage"] = np.round(df["optimal_%"] / (df["salary"] / 1000), 2)
    return df.sort_values(by="optimal_%", ascending=False)

def background_task(target_mode, sender, pw, rec, num_sims=6700):
    try:
        set_status(True, f"Scanning for {target_mode}...", progress=5)
        draft_group_id, slate_type, slate_title = get_target_slate(target_mode)
        if not draft_group_id:
            set_status(False, f"No active contests found for {target_mode}.", progress=0)
            return

        set_status(True, f"Downloading pool for {slate_title}...", progress=15, slate_name=slate_title, slate_type=slate_type)
        players_df, matchups_df, team_opponents = fetch_player_pool(draft_group_id, slate_type)

        sim_results = run_monte_carlo(players_df, slate_type, team_opponents, num_sims=num_sims)

        set_status(True, f"Solving optimal {slate_type} roster...", progress=88, slate_name=slate_title, slate_type=slate_type)
        optimal_roster = solve_lineup(sim_results, sim_results["proj_fpts"].to_numpy(), slate_type, team_opponents)

        if optimal_roster is None or optimal_roster.empty:
            set_status(False, "Failed to resolve lineup within salary cap.", progress=0)
            return

        optimal_roster.to_csv(CACHE_ROSTER, index=False)
        sim_results.to_csv(CACHE_SIM, index=False)
        matchups_df.to_csv(CACHE_MATCHUPS, index=False)

        run_ts = get_current_et_str("%Y-%m-%d %I:%M %p ET")
        email_msg = ""
        if sender and pw and rec:
            set_status(True, "Delivering email digest...", progress=95, slate_name=slate_title, slate_type=slate_type)
            ok, emsg = send_email_report(optimal_roster, sim_results, matchups_df, slate_title, slate_type, sender, pw, rec)
            email_msg = f" | Email: {'Delivered' if ok else emsg}"
        else:
            email_msg = " | Email: Skipped"

        set_status(False, f"Completed successfully ({slate_type}){email_msg}", progress=100, last_run=run_ts, slate_name=slate_title, slate_type=slate_type)
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
            "Auto-Detect Next Slate",
            "Thursday Night Football (Showdown)",
            "Monday Night Football (Showdown)",
            "Sunday Main Slate (Classic)",
            "Sunday Night Football (Showdown)"
        ]
    )
    num_simulations = st.number_input("Monte Carlo Sample Size", min_value=1000, max_value=20000, value=6700, step=500)

    st.header("📧 Email Notifications")
    send_email = st.checkbox("Email report when simulation runs", value=True)
    sender_email = st.secrets.get("EMAIL_SENDER", "") if "EMAIL_SENDER" in st.secrets else ""
    sender_pw = st.secrets.get("EMAIL_PASSWORD", "") if "EMAIL_PASSWORD" in st.secrets else ""
    recipient_email = st.secrets.get("EMAIL_RECIPIENT", "") if "EMAIL_RECIPIENT" in st.secrets else ""

    if not sender_email:
        sender_email = st.text_input("Sender Gmail", "")
    if not sender_pw:
        sender_pw = st.text_input("Gmail App Password", type="password")
    if not recipient_email:
        recipient_email = st.text_input("Recipient Email", "")

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
        st.caption(f"Slate: **{status.get('slate_name', 'Showdown')}** ({status.get('slate_type', 'Showdown')}) | Status: **{status.get('msg', 'Idle')}**")

# --- TABLES ---
tab1, tab2, tab3 = st.tabs(["🏆 Optimal Lineup", "🏟️ Slate Games", "⚡ Simulated Exposures"])
cols_to_display = ["roster_slot", "name", "position", "team", "matchup", "salary", "proj_fpts", "optimal_%", "leverage"]

if os.path.exists(CACHE_ROSTER) and os.path.exists(CACHE_SIM):
    roster_df = pd.read_csv(CACHE_ROSTER)
    sim_df = pd.read_csv(CACHE_SIM)
    match_df = pd.read_csv(CACHE_MATCHUPS) if os.path.exists(CACHE_MATCHUPS) else pd.DataFrame()

    with tab1:
        st.header(f"Optimal Lineup: {status.get('slate_name', 'Showdown')} ({status.get('slate_type', 'Showdown')})")
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
            default_slots = [p for p in ["CPT", "FLEX", "QB", "RB", "WR", "TE", "DST"] if p in sim_df["roster_slot"].values]
            slot_filter = st.multiselect("Filter Slots", sim_df["roster_slot"].unique().tolist(), default=default_slots)
        valid_cols = [c for c in cols_to_display if c in sim_df.columns]
        filtered = sim_df[(sim_df["optimal_%"] >= min_opt) & (sim_df["roster_slot"].isin(slot_filter))]
        st.dataframe(filtered[valid_cols], width="stretch")
else:
    with tab1:
        st.info("No cached run found yet. Select your slate in the sidebar and tap **Run Live Simulation**.")
