import sys, re, time
from datetime import datetime

import requests
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.table import Table
from rich import box
from nba_api.stats.endpoints import leaguedashteamstats

# ============================================================
# 流程總覽：
#   1. 使用者選擇「例行賽」或「季後賽」模式
#   2. 一次性呼叫 NBA.com API，抓「全聯盟30隊」的進攻/防守效率(進階數據)
#   3. 一次性呼叫 ESPN 標準排名 API，抓「全聯盟30隊」的戰績/勝率(不論模式都抓)
#   4. 使用者輸入兩支球隊 -> 平行呼叫 ESPN 賽程 API，抓兩隊的近況
#   5. 整合三份數據，計算兩種預測比分，用 rich 套件畫成表格
#   6. 重複步驟4~5，直到使用者輸入 q
# ============================================================


def get_current_espn_season():
    """
    回傳 ESPN API 用的賽季年份（賽季「結束」那一年，例如 2025-26 賽季回傳 2026）。
    NBA 賽季固定 10 月開打、隔年 6 月打完，所以 10 月(含)之後算新賽季開始。
    """
    now = datetime.now()
    return now.year + 1 if now.month >= 10 else now.year


def get_current_nba_season_str():
    """回傳 NBA.com API 用的賽季字串，例如 '2025-26'。"""
    end_year = get_current_espn_season()
    return f"{end_year - 1}-{str(end_year)[2:]}"


def nba_fetch(fn, retries=5, delay=10):
    """
    NBA.com 對高頻請求很容易限流，這個包裝函式：
      1. 呼叫前固定等待 delay 秒
      2. 重置 session，避免沿用可能被標記的連線
      3. 失敗就以遞增秒數重試，最多 retries 次，最後一次仍失敗才拋出例外
    """
    from nba_api.stats.library.http import NBAStatsHTTP
    for i in range(retries):
        try:
            time.sleep(delay)
            NBAStatsHTTP._session = None
            return fn()
        except Exception as e:
            if i < retries - 1:
                wait = delay * (i + 2)
                console.print(f'[yellow]NBA API 限流，{wait}秒後重試...[/yellow]')
                time.sleep(wait)
            else:
                raise e


console = Console()
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
STANDINGS_URL = 'https://site.api.espn.com/apis/v2/sports/basketball/nba/standings'

# 中文球隊俗名 -> NBA.com 官方 TEAM_ID 對照表
# '小牛'/'獨行俠' 對應同一支球隊，因為中文譯名曾經改過，兩種叫法都支援
TEAM_MAP = {
    '塞爾提克':1610612738,'籃網':1610612751,'尼克':1610612752,'七六人':1610612755,'暴龍':1610612761,
    '公牛':1610612741,'騎士':1610612739,'活塞':1610612765,'溜馬':1610612754,'公鹿':1610612749,
    '老鷹':1610612737,'黃蜂':1610612766,'熱火':1610612748,'魔術':1610612753,'巫師':1610612764,
    '金塊':1610612743,'灰狼':1610612750,'雷霆':1610612760,'拓荒者':1610612757,'爵士':1610612762,
    '勇士':1610612744,'快艇':1610612746,'湖人':1610612747,'太陽':1610612756,'國王':1610612758,
    '小牛':1610612742,'獨行俠':1610612742,'火箭':1610612745,'灰熊':1610612763,'鵜鶘':1610612740,'馬刺':1610612759,
}

# NBA.com TEAM_ID -> ESPN team id 對照表（兩邊系統用不同編號，需要轉換才能組出 ESPN API 網址）
ESPN_ID = {
    1610612738:2,  1610612751:17, 1610612752:18, 1610612755:20, 1610612761:28,
    1610612741:4,  1610612739:5,  1610612765:8,  1610612754:11, 1610612749:15,
    1610612737:1,  1610612766:30, 1610612748:14, 1610612753:19, 1610612764:27,
    1610612743:7,  1610612750:16, 1610612760:25, 1610612757:22, 1610612762:26,
    1610612744:9,  1610612746:12, 1610612747:13, 1610612756:21, 1610612758:23,
    1610612742:6,  1610612745:10, 1610612763:29, 1610612740:3,  1610612759:24,
}


def get_espn(url):
    """
    呼叫 ESPN API 並回傳 JSON；任何錯誤（逾時、非200、解析失敗）都回傳空 dict，
    後續一律用 .get(...) 搭配預設值讀取，避免程式崩潰。
    """
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def to_score(v):
    """
    把 ESPN 回傳的比分欄位轉成 float。欄位有時是數字字串，有時是
    {'value':..., 'displayValue':...} 這種 dict，轉換失敗一律回傳 0.0。
    """
    if isinstance(v, dict):
        v = v.get('value') or v.get('displayValue') or 0
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def fetch_all_nba_stats():
    """
    一次性抓取全聯盟30隊的進階數據（進攻/防守效率、PACE 及聯盟排名），
    之後查詢對戰組合時直接查表（df.loc[...]），不必每次重新呼叫 API。
    """
    with console.status('[orange1]📡 載入聯盟進階數據...[/orange1]'):
        df_adv = nba_fetch(lambda: leaguedashteamstats.LeagueDashTeamStats(
            season=get_current_nba_season_str(),
            measure_type_detailed_defense='Advanced',
            per_mode_detailed='PerGame',
            rank='Y'
        ).get_data_frames()[0])
    return df_adv.set_index('TEAM_ID')


def fetch_standings(espn_season):
    """
    一次性抓取全聯盟的完整戰績（總戰績、主客場戰績、勝率）。

    註：舊版原本逐隊呼叫 /teams/{id} 讀取 team.record.items，但 ESPN 已棄用
    該欄位（恆為空 dict），改用 standings 端點一次拿到全聯盟資料，
    比逐隊呼叫更準確也更有效率。

    回傳：{ESPN球隊id(字串): {'wins', 'losses', 'win_percent', 'home', 'road'}}
    """
    data = get_espn(f'{STANDINGS_URL}?season={espn_season}')
    result = {}
    for conference in data.get('children', []):
        for entry in conference.get('standings', {}).get('entries', []):
            eid = str(entry.get('team', {}).get('id', ''))
            if not eid:
                continue
            stats = {s.get('type'): s for s in entry.get('stats', [])}

            def display(key, default='N/A'):
                s = stats.get(key)
                return s.get('displayValue', default) if s else default

            win_percent = stats.get('winpercent', {}).get('value')
            result[eid] = {
                'wins': int(display('wins', 0)),
                'losses': int(display('losses', 0)),
                'win_percent': float(win_percent) if win_percent is not None else 0.0,
                'home': display('home'),
                'road': display('road'),
            }
    return result


def fetch_espn_team(nba_id, all_standings, season_type, espn_season, show_vs500):
    """
    抓取單一球隊的近況資料，回傳 dict：
      - 整季戰績/主客場戰績/勝率：來自 fetch_standings() 已抓好的資料
      - 整季場均得失分：從逐場真實比分算出（近10場pace估算會用到）
      - 近10場戰績、每場得失分：來自賽程 API 逐場計算
      - (例行賽限定) 對五成以上/以下球隊戰績

    參數：
      nba_id：NBA.com TEAM_ID，查 ESPN_ID 對照表轉成 ESPN 隊伍編號
      all_standings：fetch_standings() 的回傳值，不論模式都要傳入真實資料
                     （不能傳 None，否則整季戰績會顯示 0-0/N/A）
      season_type：ESPN 賽季類型，2=例行賽、3=季後賽
      espn_season：ESPN 用的賽季年份，見 get_current_espn_season()
      show_vs500：是否計算「對五成球隊戰況」，只有例行賽模式為 True
    """
    eid = str(ESPN_ID.get(nba_id, ''))
    own = all_standings.get(eid, {})

    # 必須帶 season 參數，否則休賽期間 ESPN 無法判斷「目前賽季」而回傳空賽程
    sched_data = get_espn(f'{BASE}/teams/{eid}/schedule?seasontype={season_type}&season={espn_season}')

    evts = sched_data.get('events', [])
    completed = sorted(
        [e for e in evts if e.get('competitions', [{}])[0].get('status', {}).get('type', {}).get('completed')],
        key=lambda e: e.get('date', '')
    )
    last10 = completed[-10:]

    l10w = l10l = 0
    l10pts_list = []
    l10opp_list = []
    season_pts_list = []
    season_opp_list = []
    vaW = vaL = vbW = vbL = 0  # va=對五成以上球隊的勝/負；vb=對五成以下球隊的勝/負

    # 無條件跑過整季已完賽比賽，統計整季場均得失分；
    # 五成球隊戰況只在例行賽模式(show_vs500=True)才順便計算，避免對completed重複跑兩次迴圈
    for ev in completed:
        comp = ev.get('competitions', [{}])[0]
        me = next((c for c in comp.get('competitors', []) if str(c.get('team', {}).get('id')) == eid), None)
        opp = next((c for c in comp.get('competitors', []) if str(c.get('team', {}).get('id')) != eid), None)
        if not me or not opp:
            continue
        season_pts_list.append(int(to_score(me.get('score', 0))))
        season_opp_list.append(int(to_score(opp.get('score', 0))))
        if show_vs500:
            won = bool(me.get('winner'))
            opp_id = str(opp.get('team', {}).get('id'))
            owp = all_standings.get(opp_id, {}).get('win_percent', 0.0)
            if owp >= 0.5:
                vaW += won
                vaL += (not won)
            else:
                vbW += won
                vbL += (not won)

    for ev in last10:
        comp = ev.get('competitions', [{}])[0]
        me = next((c for c in comp.get('competitors', []) if str(c.get('team', {}).get('id')) == eid), None)
        opp = next((c for c in comp.get('competitors', []) if str(c.get('team', {}).get('id')) != eid), None)
        if not me or not opp:
            continue
        if me.get('winner'):
            l10w += 1
        else:
            l10l += 1
        l10pts_list.append(int(to_score(me.get('score', 0))))
        l10opp_list.append(int(to_score(opp.get('score', 0))))

    n = len(l10pts_list) or 1  # 避免近10場一場都沒有時除以0
    sn = len(season_pts_list) or 1

    return {
        'wins': own.get('wins', 0), 'losses': own.get('losses', 0), 'wp': own.get('win_percent', 0.0),
        'home': own.get('home', 'N/A'),
        'away': own.get('road', 'N/A'),
        'l10rec': f'{l10w}-{l10l}',
        'l10off': f'{sum(l10pts_list)/n:.1f}',
        'l10def': f'{sum(l10opp_list)/n:.1f}',
        'season_off': f'{sum(season_pts_list)/sn:.1f}',  # 整季場均得分(逐場真實比分算出)
        'season_def': f'{sum(season_opp_list)/sn:.1f}',  # 整季場均失分
        'va500': f'{vaW}-{vaL}',
        'vb500': f'{vbW}-{vbL}',
        'l10pts': ' / '.join(str(p) for p in l10pts_list),
        'l10opp': ' / '.join(str(p) for p in l10opp_list),
    }


def parse_input(raw):
    """
    解析使用者輸入的對戰字串，支援：湖人:火箭 / 湖人：火箭 / 湖人vs火箭 /
    湖人VS火箭 / 湖人 對上 火箭 / 湖人 對決 火箭 / 湖人v火箭。
    回傳 (中文隊名A, NBA_ID_A, 中文隊名B, NBA_ID_B)，格式不對或隊名無法辨識回傳 None。
    """
    parts = re.split(r'\s*(?:vs\.?|VS\.?|對上|對決|v|:|：)\s*', raw.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return None

    def resolve(s):
        # 用「包含」而不是「完全相等」比對，容錯像「洛杉磯湖人」這種輸入
        for cn, nba_id in TEAM_MAP.items():
            if cn in s:
                return cn, nba_id
        return None, None

    cnA, idA = resolve(parts[0])
    cnB, idB = resolve(parts[1])
    return (cnA, idA, cnB, idB) if idA and idB else None


def rank_str(r):
    """把排名數字轉成 "(#3)" 格式；r 是 NaN/None/無法轉換時回傳空字串。"""
    try:
        return f'(#{int(r)})'
    except Exception:
        return ''



if __name__ == "__main__":
    console.print('[bold orange1]請選擇查詢模式[/]')
    console.print('1. 例行賽')
    console.print('2. 季後賽')

    while True:
        mode = console.input('請輸入 1 或 2: ').strip()
        if mode in ('1', '2'):
            break
        console.print('[red]請輸入 1 或 2[/red]')

    SEASON_TYPE = '2' if mode == '1' else '3'  # ESPN: 2=例行賽, 3=季後賽
    SHOW_VS500 = (mode == '1')                 # 只有例行賽模式才顯示「五成球隊戰況」
    ESPN_SEASON = get_current_espn_season()

    df_adv = fetch_all_nba_stats()

    # 不論模式都無條件抓全聯盟整季戰績，避免季後賽模式下「整季戰績」顯示0-0
    with console.status('[orange1]📡 載入全聯盟戰績...[/orange1]'):
        all_standings = fetch_standings(ESPN_SEASON)

    while True:
        raw = console.input('\n[bold orange1]請輸入對戰組合[/] [dim](例：湖人:雷霆，輸入q退出)[/dim]: ').strip()
        if raw.lower() == 'q':
            break
        parsed = parse_input(raw)
        if not parsed:
            console.print('[red]格式錯誤，請輸入如：湖人vs雷霆[/red]')
            continue

        cnA, idA, cnB, idB = parsed

        with console.status('[orange1]📡 載入戰績數據...[/orange1]'):
            with ThreadPoolExecutor(max_workers=2) as ex:
                fa = ex.submit(fetch_espn_team, idA, all_standings, SEASON_TYPE, ESPN_SEASON, SHOW_VS500)
                fb = ex.submit(fetch_espn_team, idB, all_standings, SEASON_TYPE, ESPN_SEASON, SHOW_VS500)
                eA, eB = fa.result(), fb.result()

        def get_row(df, nba_id):
            return df.loc[nba_id] if nba_id in df.index else None

        advA, advB = get_row(df_adv, idA), get_row(df_adv, idB)

        # Pace 改用 NBA.com Advanced 數據裡官方算好的整季 PACE 欄位，不再用經驗係數估算
        def get_pace(row, fb=100.0):
            if row is None or row.get('PACE') is None:
                return fb
            return round(float(row.get('PACE')), 1)

        pace_A = get_pace(advA)
        pace_B = get_pace(advB)

        def adv_val(row, col, fb='N/A'):
            if row is None or row.get(col) is None:
                return str(fb) if not isinstance(fb, str) else fb
            return f'{row.get(col):.1f}'

        def adv_rank(row, col):
            return rank_str(row.get(col)) if row is not None else ''

        console.print()
        console.rule(f'[bold orange1]{cnA}  VS  {cnB}[/bold orange1]')

        tables = [
            ('📋 整季戰績', [
                ('整季戰績',   f'{eA["wins"]}-{eA["losses"]} ({eA["wp"]:.3f})', f'{eB["wins"]}-{eB["losses"]} ({eB["wp"]:.3f})'),
                ('主場戰績',   eA['home'],  eB['home']),
                ('客場戰績',   eA['away'],  eB['away']),
            ]),
            ('⚡ 進攻 / 防守效率', [
                ('本賽季進攻效率', f'{adv_val(advA,"OFF_RATING")} {adv_rank(advA,"OFF_RATING_RANK")}',
                                    f'{adv_val(advB,"OFF_RATING")} {adv_rank(advB,"OFF_RATING_RANK")}'),
                ('本賽季防守效率', f'{adv_val(advA,"DEF_RATING")} {adv_rank(advA,"DEF_RATING_RANK")}',
                                    f'{adv_val(advB,"DEF_RATING")} {adv_rank(advB,"DEF_RATING_RANK")}'),
            ]),
        ]

        if SHOW_VS500:
            tables.append(('🎯 五成球隊戰況', [
                ('對五成以上球隊', eA['va500'], eB['va500']),
                ('對五成以下球隊', eA['vb500'], eB['vb500']),
            ]))

        tables += [
            ('🔥 近期 10 場', [
                ('戰績',     eA['l10rec'], eB['l10rec']),
                ('平均得分', eA['l10off'], eB['l10off']),
                ('平均失分', eA['l10def'], eB['l10def']),
            ]),
            ('🔮 預測比分', [
                # 預測法1「近10場平均」：A預測得分 = (A近10場平均得分 + B近10場平均失分) / 2
                # 概念：取A的進攻能力與B的防守弱點的平均值
                ('預測得分 近10場',
                f'{(float(eA["l10off"]) + float(eB["l10def"])) / 2:.1f}',
                f'{(float(eB["l10off"]) + float(eA["l10def"])) / 2:.1f}'),

                # 預測法2「整季PACE法」：得分效率(得分/整季pace) x 兩隊整季平均節奏
                # 概念：若本場節奏落在兩隊整季平均值，A用近10場效率大約能拿幾分
                ('整季PACE法',
                f'{float(eA["l10off"]) / max(pace_A,1) * (pace_A+pace_B)/2:.1f}',
                f'{float(eB["l10off"]) / max(pace_B,1) * (pace_A+pace_B)/2:.1f}'),
            ]),
        ]

        for title, rows in tables:
            t = Table(title=title, box=box.SIMPLE_HEAVY, show_header=True,
                    header_style='bold orange1', title_style='bold white', min_width=70)
            t.add_column('指標',  style='dim',      width=20)
            t.add_column(cnA,     justify='center', style='cyan',  min_width=22)
            t.add_column(cnB,     justify='center', style='green', min_width=22)
            for row in rows:
                t.add_row(*row)
            console.print(t)
