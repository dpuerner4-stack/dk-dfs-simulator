import requests
import numpy as np
import pandas as pd
from ortools.linear_solver import pywraplp
from multiprocessing import Pool, cpu_count
import time
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- 1. SCAN DRAFTKINGS LOBBY FOR $0.25 - $30 CONTESTS ---
def get_target_slate(min_fee=0.25, max_fee=30.0, min_pool=25000):
    url = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    
    print("[1/4] Scanning DraftKings lobby for top value contests...")
    try:
        res = requests.get(url, headers=headers).json()
    except Exception as e:
        print(f"Error connecting to DraftKings: {e}")
        return pd.DataFrame()

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

    df = df.sort_values(by=["prize_pool", "multiplier"], ascending=[False, False])
    df.to_csv("top_contests.csv", index=False)
    return df

# --- 2. FETCH PLAYER POOL DIRECTLY FROM DK API ---
def fetch_player_pool(draft_group_id):
    print(f"[2/4] Fetching live player pool for DraftGroup {draft_group_id}...")
    url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables?format=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    res = requests.get(url, headers=headers).json()
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

# --- 3. 17,500 SOLVER ENGINE ---
G_SALARIES, G_POSITIONS, G_N = None, None, None

def init_worker(salaries, positions):
    global G_SALARIES, G_POSITIONS, G_N
    G_SALARIES, G_POSITIONS, G_N = salaries, positions, len(salaries)

def solve_batch(sim_batch):
    global G_SALARIES, G_POSITIONS, G_N
    counts = np.zeros(G_N, dtype=np.int32)

    for b in range(len(sim_batch)):
        scores = sim_batch[b]
        solver = pywraplp.Solver.CreateSolver("CBC")
        if not solver:
            continue

        x = [solver.BoolVar(f"x_{i}") for i in range(G_N)]
        obj = solver.Objective()
        for i in range(G_N):
            obj.SetCoefficient(x[i], float(scores[i]))
        obj.SetMaximization()

        sal_ct = solver.Constraint(0, 50000)
        qb_ct, dst_ct = solver.Constraint(1, 1), solver.Constraint(1, 1)
        rb_ct, wr_ct, te_ct = solver.Constraint(2, 3), solver.Constraint(3, 4), solver.Constraint(1, 2)
        flex_ct, tot_ct = solver.Constraint(7, 7), solver.Constraint(9, 9)

        for i, pos in enumerate(G_POSITIONS):
            sal_ct.SetCoefficient(x[i], int(G_SALARIES[i]))
            tot_ct.SetCoefficient(x[i], 1)
            if pos == "QB": qb_ct.SetCoefficient(x[i], 1)
            elif pos == "DST": dst_ct.SetCoefficient(x[i], 1)
            elif pos == "RB": rb_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
            elif pos == "WR": wr_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)
            elif pos == "TE": te_ct.SetCoefficient(x[i], 1); flex_ct.SetCoefficient(x[i], 1)

        if solver.Solve() == pywraplp.Solver.OPTIMAL:
            for i in range(G_N):
                if x[i].solution_value() > 0.5:
                    counts[i] += 1
    return counts

def run_simulation(df, num_simulations=17500):
    print(f"[3/4] Running {num_simulations:,} simulations...")
    cores = cpu_count()
    sim_matrix = np.random.normal(df["proj_fpts"], df["std_dev"], size=(num_simulations, len(df)))
    sim_matrix = np.clip(sim_matrix, 0, None).astype(np.float32)

    chunks = np.array_split(sim_matrix, cores * 4)
    with Pool(processes=cores, initializer=init_worker, initargs=(df["salary"].to_numpy(dtype=np.int32), df["position"].to_numpy())) as pool:
        results = pool.map(solve_batch, chunks)

    total_counts = np.sum(results, axis=0)
    df["optimal_%"] = np.round((total_counts / num_simulations) * 100, 2)
    df["leverage"] = np.round(df["optimal_%"] / (df["salary"] / 1000), 2)
    return df.sort_values(by="optimal_%", ascending=False)

# --- 4. EMAIL DISPATCH (OPTIONAL) ---
def send_email_alert(top_df, top_contests):
    sender = os.environ.get("DFS_SENDER_EMAIL")
    pwd = os.environ.get("DFS_EMAIL_PASSWORD")
    recipient = os.environ.get("DFS_RECEIVER_EMAIL")

    if not sender or not pwd or not recipient:
        print("[4/4] Skipping email (Credentials not configured).")
        return

    print("[4/4] Sending Saturday Morning Email Summary...")
    contests_html = top_contests.head(5)[["name", "entry_fee", "prize_pool", "multiplier"]].to_html(index=False)
    players_html = top_df.head(15)[["position", "name", "salary", "proj_fpts", "optimal_%", "leverage"]].to_html(index=False)

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>DraftKings Saturday Intelligence Report</h2>
        <h3>Top Payout Contests ($0.25 - $30.00)</h3>
        {contests_html}
        <br>
        <h3>Top 17,500 Simulation Targets</h3>
        {players_html}
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "DraftKings Saturday Slate Simulation & Contests"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, pwd)
            server.sendmail(sender, recipient, msg.as_string())
        print("Email sent successfully!")
    except Exception as e:
        print(f"Email failed to send: {e}")

if __name__ == "__main__":
    t0 = time.time()
    contests_df = get_target_slate(min_fee=0.25, max_fee=30.0)
    
    if not contests_df.empty:
        target = contests_df.iloc[0]
        print(f"Targeting: {target['name']} | Fee: ${target['entry_fee']} | Prize: ${target['prize_pool']:,}")

        df_players = fetch_player_pool(target["draft_group_id"])
        sim_results = run_simulation(df_players, num_simulations=17500)
        
        sim_results.to_csv("saturday_recommendations.csv", index=False)
        sim_results.to_csv("today_lineup_targets.csv", index=False)
        print(f"\nPipeline finished in {time.time() - t0:.1f}s!")
        print(sim_results[sim_results["optimal_%"] >= 2.0].head(20).to_string(index=False))

        send_email_alert(sim_results, contests_df)
