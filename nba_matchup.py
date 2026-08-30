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
#
# 輸入慣例：「客隊:主隊」，後面輸入的那隊是主場（例：尼克:馬刺 -> 馬刺主場）
# ============================================================

HOME_ADVANTAGE = 1  # 主場優勢加成(分)，回測驗證三季合併後兩種預測法正確率均提升約0.6個百分點

def get_current_espn_season():
    """回傳目前賽季對應的ESPN季末年份(球季跨兩個日曆年，10月後算下一年)。"""
    now = datetime.now()
    return now.year + 1 if now.month >= 10 else now.year


def get_current_nba_season_str():
    """把季末年份轉成NBA.com慣用的"YYYY-YY"格式字串。"""
    end_year = get_current_espn_season()
    return f"{end_year - 1}-{str(end_year)[2:]}"


def nba_fetch(fn, retries=5, delay=10):
    """呼叫NBA.com API的包裝函式，遇到限流會自動等待重試(最多5次)。"""
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

TEAM_MAP = {
    '塞爾提克':1610612738,'籃網':1610612751,'尼克':1610612752,'七六人':1610612755,'暴龍':1610612761,
    '公牛':1610612741,'騎士':1610612739,'活塞':1610612765,'溜馬':1610612754,'公鹿':1610612749,
    '老鷹':1610612737,'黃蜂':1610612766,'熱火':1610612748,'魔術':1610612753,'巫師':1610612764,
    '金塊':1610612743,'灰狼':1610612750,'雷霆':1610612760,'拓荒者':1610612757,'爵士':1610612762,
    '勇士':1610612744,'快艇':1610612746,'湖人':1610612747,'太陽':1610612756,'國王':1610612758,
    '小牛':1610612742,'獨行俠':1610612742,'火箭':1610612745,'灰熊':1610612763,'鵜鶘':1610612740,'馬刺':1610612759,
}

ESPN_ID = {
    1610612738:2,  1610612751:17, 1610612752:18, 1610612755:20, 1610612761:28,
    1610612741:4,  1610612739:5,  1610612765:8,  1610612754:11, 1610612749:15,
    1610612737:1,  1610612766:30, 1610612748:14, 1610612753:19, 1610612764:27,
    1610612743:7,  1610612750:16, 1610612760:25, 1610612757:22, 1610612762:26,
    1610612744:9,  1610612746:12, 1610612747:13, 1610612756:21, 1610612758:23,
    1610612742:6,  1610612745:10, 1610612763:29, 1610612740:3,  1610612759:24,
}


def get_espn(url):
    """對ESPN API發GET請求，失敗時回傳空字典而不是丟例外。"""
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def to_score(v):
    """把ESPN回傳的分數欄位(可能是數字、字串或巢狀字典)統一轉成float。"""
    if isinstance(v, dict):
        v = v.get('value') or v.get('displayValue') or 0
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def fetch_all_nba_stats():
    """抓取全聯盟30隊的進攻/防守效率等進階數據(NBA.com)。"""
    with console.status('[orange1]📡 載入聯盟進階數據...[/orange1]'):
        df_adv = nba_fetch(lambda: leaguedashteamstats.LeagueDashTeamStats(
            season=get_current_nba_season_str(),
            measure_type_detailed_defense='Advanced',
            per_mode_detailed='PerGame',
            rank='Y'
        ).get_data_frames()[0])
    return df_adv.set_index('TEAM_ID')


def fetch_standings(espn_season):
    """抓取全聯盟30隊的戰績/勝率排名(ESPN)。"""
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
    """抓取單一球隊的賽程與近況，計算近10場戰績、對戰五成以上/以下球隊的勝負紀錄。"""
    eid = str(ESPN_ID.get(nba_id, ''))
    own = all_standings.get(eid, {})

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
    vaW = vaL = vbW = vbL = 0 

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

    n = len(l10pts_list) or 1 
    sn = len(season_pts_list) or 1

    return {
        'wins': own.get('wins', 0), 'losses': own.get('losses', 0), 'wp': own.get('win_percent', 0.0),
        'home': own.get('home', 'N/A'),
        'away': own.get('road', 'N/A'),
        'l10rec': f'{l10w}-{l10l}',
        'l10off': f'{sum(l10pts_list)/n:.1f}',
        'l10def': f'{sum(l10opp_list)/n:.1f}',
        'season_off': f'{sum(season_pts_list)/sn:.1f}', 
        'season_def': f'{sum(season_opp_list)/sn:.1f}', 
        'va500': f'{vaW}-{vaL}',
        'vb500': f'{vbW}-{vbL}',
        'l10pts': ' / '.join(str(p) for p in l10pts_list),
        'l10opp': ' / '.join(str(p) for p in l10opp_list),
    }


def parse_input(raw):
    """解析使用者輸入的對戰組合文字(支援多種分隔符)，回傳兩隊的中文名與球隊ID。"""
    parts = re.split(r'\s*(?:vs\.?|VS\.?|對上|對決|v|:|：)\s*', raw.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return None

    def resolve(s):
        for cn, nba_id in TEAM_MAP.items():
            if cn in s:
                return cn, nba_id
        return None, None

    cnA, idA = resolve(parts[0])
    cnB, idB = resolve(parts[1])
    return (cnA, idA, cnB, idB) if idA and idB else None


def rank_str(r):
    """把排名數字格式化成"(#N)"顯示字串。"""
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
    SHOW_VS500 = (mode == '1')                 
    ESPN_SEASON = get_current_espn_season()

    df_adv = fetch_all_nba_stats()


    with console.status('[orange1]📡 載入全聯盟戰績...[/orange1]'):
        all_standings = fetch_standings(ESPN_SEASON)

    while True:
        raw = console.input('\n[bold orange1]請輸入對戰組合[/] [dim](例：(客隊):(主隊)，輸入q退出)[/dim]: ').strip()
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
        console.rule(f'[bold orange1]{cnA}(客)  VS  {cnB}(主)[/bold orange1]')

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
                ('預測得分 近10場',
                f'{(float(eA["l10off"]) + float(eB["l10def"])) / 2:.1f}',
                f'{(float(eB["l10off"]) + float(eA["l10def"])) / 2 + HOME_ADVANTAGE:.1f}'),

                ('PACE調整法',
                f'{(float(eA["season_off"]) / max(pace_A,1) * (pace_A+pace_B)/2 + float(eB["season_def"]) / max(pace_B,1) * (pace_A+pace_B)/2) / 2:.1f}',
                f'{(float(eB["season_off"]) / max(pace_B,1) * (pace_A+pace_B)/2 + float(eA["season_def"]) / max(pace_A,1) * (pace_A+pace_B)/2) / 2 + HOME_ADVANTAGE:.1f}'),
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
