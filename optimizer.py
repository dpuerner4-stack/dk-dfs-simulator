import sys
import pulp
import pandas as pd

def optimize_showdown(csv_path, min_cpt_salary=6000, min_cpt_proj=10.0):
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # Normalize column headers
    cols = {c.strip().lower(): c for c in df.columns}
    
    name_col = cols.get('name') or cols.get('player')
    pos_col = cols.get('position') or cols.get('pos')
    sal_col = cols.get('salary')
    proj_col = cols.get('proj_fpts') or cols.get('fppg') or cols.get('projected_points')
    team_col = cols.get('team') or cols.get('teamabbrev')

    if not all([name_col, pos_col, sal_col, proj_col]):
        print(f"Could not detect required columns in: {list(df.columns)}")
        return

    df = df.rename(columns={
        name_col: 'name',
        pos_col: 'position',
        sal_col: 'salary',
        proj_col: 'proj'
    })
    
    if team_col:
        df['team'] = df[team_col]
    else:
        df['team'] = 'N/A'

    df['proj'] = pd.to_numeric(df['proj'], errors='coerce').fillna(0)
    df['salary'] = pd.to_numeric(df['salary'], errors='coerce').fillna(0)

    # 1. Base filter: remove sub-2.0 phantom depth chart players
    df = df[df['proj'] >= 2.0].copy()

    # 2. Filter out non-starter QBs
    df = df[~((df['position'].str.upper() == 'QB') & (df['proj'] < 12.0))].copy()

    # 3. Deduplicate
    df = df.sort_values(by='salary', ascending=True).drop_duplicates(subset=['name']).reset_index(drop=True)

    players = df.index.tolist()

    # Optimization Problem
    prob = pulp.LpProblem("DK_Showdown_GPP", pulp.LpMaximize)
    cpt = pulp.LpVariable.dicts("CPT", players, cat=pulp.LpBinary)
    flex = pulp.LpVariable.dicts("FLEX", players, cat=pulp.LpBinary)

    # Objective: Maximize projected fantasy points
    prob += pulp.lpSum([
        (df.loc[i, 'proj'] * 1.5) * cpt[i] + (df.loc[i, 'proj'] * 1.0) * flex[i]
        for i in players
    ])

    # Constraint 1: 1 Captain, 5 Flex
    prob += pulp.lpSum([cpt[i] for i in players]) == 1
    prob += pulp.lpSum([flex[i] for i in players]) == 5

    # Constraint 2: Mutual Exclusivity
    for i in players:
        prob += cpt[i] + flex[i] <= 1

    # Constraint 3: Salary Cap ($50,000)
    prob += pulp.lpSum([
        (df.loc[i, 'salary'] * 1.5) * cpt[i] + (df.loc[i, 'salary'] * 1.0) * flex[i]
        for i in players
    ]) <= 50000

    # Constraint 4: Captain Quality Floor (prevents sub-$6k punts at CPT)
    for i in players:
        if df.loc[i, 'salary'] < min_cpt_salary and df.loc[i, 'proj'] < min_cpt_proj:
            prob += cpt[i] == 0

    # Constraint 5: Stacking rule (If team data is available)
    if 'team' in df.columns and df['team'].nunique() > 1:
        # At least 1 player from each team (Official DraftKings Rule)
        teams = df['team'].unique()
        for t in teams:
            team_indices = df[df['team'] == t].index.tolist()
            prob += pulp.lpSum([cpt[i] + flex[i] for i in team_indices]) >= 1

    # Solve
    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if pulp.LpStatus[prob.status] != "Optimal":
        print("No optimal lineup found under constraints. Try lowering min_cpt_salary.")
        return

    results = []
    for i in players:
        if pulp.value(cpt[i]) == 1:
            results.append({
                'slot': 'CPT',
                'name': df.loc[i, 'name'],
                'pos': df.loc[i, 'position'],
                'salary': int(df.loc[i, 'salary'] * 1.5),
                'proj': round(df.loc[i, 'proj'] * 1.5, 2)
            })
        elif pulp.value(flex[i]) == 1:
            results.append({
                'slot': 'FLEX',
                'name': df.loc[i, 'name'],
                'pos': df.loc[i, 'position'],
                'salary': int(df.loc[i, 'salary']),
                'proj': round(df.loc[i, 'proj'], 2)
            })

    res_df = pd.DataFrame(results)
    # Sort so CPT shows at top
    res_df['sort_order'] = res_df['slot'].apply(lambda x: 0 if x == 'CPT' else 1)
    res_df = res_df.sort_values(by=['sort_order', 'salary'], ascending=[True, False]).drop(columns=['sort_order'])

    total_sal = res_df['salary'].sum()
    total_pts = res_df['proj'].sum()

    print("\n" + "=" * 55)
    print(f"Optimal Lineup | Salary: ${total_sal:,} / $50,000 | Proj: {total_pts:.2f} pts")
    print("=" * 55)
    print(res_df[['slot', 'pos', 'name', 'salary', 'proj']].to_string(index=False))
    print("=" * 55 + "\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        optimize_showdown(sys.argv[1])
    else:
        print("Usage: python3 optimizer.py today_lineup_targets.csv")
