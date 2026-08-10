from fastapi import FastAPI
from nba_matchup import fetch_all_nba_stats, fetch_standings, fetch_espn_team, TEAM_MAP, get_current_espn_season
from fastapi import HTTPException

app = FastAPI()

df_adv = None
all_standings = None
ESPN_SEASON = None

@app.on_event("startup")
def load_data():
    global df_adv, all_standings, ESPN_SEASON
    ESPN_SEASON = get_current_espn_season()
    df_adv = fetch_all_nba_stats()
    all_standings = fetch_standings(ESPN_SEASON)

@app.get("/matchup")
def matchup(teamA: str, teamB: str):
    if teamA not in TEAM_MAP or teamB not in TEAM_MAP:
        raise HTTPException(status_code=400, detail="球隊名稱無法辨識，請確認中文隊名是否正確")
    idA = TEAM_MAP[teamA]
    idB = TEAM_MAP[teamB]
    SEASON_TYPE = "2"  # 例行賽
    SHOW_VS500 = True

    eA = fetch_espn_team(idA, all_standings, SEASON_TYPE, ESPN_SEASON, SHOW_VS500)
    eB = fetch_espn_team(idB, all_standings, SEASON_TYPE, ESPN_SEASON, SHOW_VS500)

    def get_pace(row, fb=100.0):
        if row is None or row.get('PACE') is None:
            return fb
        return round(float(row.get('PACE')), 1)

    advA = df_adv.loc[idA] if idA in df_adv.index else None
    advB = df_adv.loc[idB] if idB in df_adv.index else None
    pace_A = get_pace(advA)
    pace_B = get_pace(advB)
    predict_l10_A = (float(eA["l10off"]) + float(eB["l10def"])) / 2
    predict_l10_B = (float(eB["l10off"]) + float(eA["l10def"])) / 2

    predict_pace_A = float(eA["l10off"]) / max(pace_A, 1) * (pace_A + pace_B) / 2
    predict_pace_B = float(eB["l10off"]) / max(pace_B, 1) * (pace_A + pace_B) / 2

    return {
        "teamA": teamA,
        "teamB": teamB,
        "近10場平均法": {"teamA": round(predict_l10_A, 1), "teamB": round(predict_l10_B, 1)},
        "整季PACE法": {"teamA": round(predict_pace_A, 1), "teamB": round(predict_pace_B, 1)},
    }