import sys, re, time

import requests
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.table import Table
from rich import box
from nba_api.stats.endpoints import leaguedashteamstats

def nba_fetch(fn, retries=5, delay=10):
    from nba_api.stats.library.http import NBAStatsHTTP
    for i in range(retries):
        try:
            time.sleep(delay)
            # 每次重置 session 避免被追蹤
            NBAStatsHTTP._session = None
            result = fn()
            return result
        except Exception as e:
            if i < retries - 1:
                wait = delay * (i + 2)
                console.print(f'[yellow]NBA API 限流，{wait}秒後重試...[/yellow]')
                time.sleep(wait)
            else:
                raise e

console = Console()
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'

TEAM_MAP = {
    '塞爾提克':1610612738,'籃網':1610612751,'尼克':1610612752,'七六人':1610612755,'暴龍':1610612761,
    '公牛':1610612741,'騎士':1610612739,'活塞':1610612765,'溜馬':1610612754,'公鹿':1610612749,
    '老鷹':1610612737,'黃蜂':1610612766,'熱火':1610612748,'魔術':1610612753,'巫師':1610612764,
    '金塊':1610612743,'灰狼':1610612750,'雷霆':1610612760,'拓荒者':1610612757,'爵士':1610612762,
    '勇士':1610612744,'快艇':1610612746,'湖人':1610612747,'太陽':1610612756,'國王':1610612758,
    '小牛':1610612742,'獨行俠':1610612742,'火箭':1610612745,'灰熊':1610612763,'鵜鶘':1610612740,'馬刺':1610612759,
}

# ESPN team id (for schedule/record)
ESPN_ID = {
    1610612738:2,  1610612751:17, 1610612752:18, 1610612755:20, 1610612761:28,
    1610612741:4,  1610612739:5,  1610612765:8,  1610612754:11, 1610612749:15,
    1610612737:1,  1610612766:30, 1610612748:14, 1610612753:19, 1610612764:27,
    1610612743:7,  1610612750:16, 1610612760:25, 1610612757:22, 1610612762:26,
    1610612744:9,  1610612746:12, 1610612747:13, 1610612756:21, 1610612758:23,
    1610612742:6,  1610612745:10, 1610612763:29, 1610612740:3,  1610612759:24,
}

def get_espn(url):
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.ok else {}
    except:
        return {}

def to_score(v):
    if isinstance(v, dict): v = v.get('value') or v.get('displayValue') or 0
    try: return float(v or 0)
    except: return 0.0

# ── 一次性拉取所有球隊的 NBA.com 進階數據 ──────────────────
def fetch_all_nba_stats():
    with console.status('[orange1]📡 載入聯盟進階數據...[/orange1]'):
        # Advanced 整季: OffRtg / DefRtg
        df_adv = nba_fetch(lambda: leaguedashteamstats.LeagueDashTeamStats(
            season='2025-26',
            measure_type_detailed_defense='Advanced',
            per_mode_detailed='PerGame',
            rank='Y'
        ).get_data_frames()[0])

    return df_adv.set_index('TEAM_ID')

# ── ESPN 戰績 + 近 10 場 ────────────────────────────────────
def fetch_espn_team(nba_id, all_wp, season_type):
    eid = str(ESPN_ID.get(nba_id, ''))
    team_data = get_espn(f'{BASE}/teams/{eid}')
    sched_data = get_espn(f'{BASE}/teams/{eid}/schedule?seasontype={season_type}')

    # 戰績
    rec_items = team_data.get('team', {}).get('record', {}).get('items', [])
    def find_item(*types):
        for it in rec_items:
            if it.get('type','').lower() in [t.lower() for t in types]:
                return it
        return None
    def rec_str(item):
        if not item: return 'N/A'
        s = item.get('stats', [])
        w = next((x['value'] for x in s if x.get('name') == 'wins'), None)
        l = next((x['value'] for x in s if x.get('name') == 'losses'), None)
        return f'{int(w)}-{int(l)}' if w is not None and l is not None else item.get('summary','N/A')

    total = find_item('total')
    home  = find_item('home')
    road  = find_item('road', 'away')

    def total_stat(*names):
        for s in (total or {}).get('stats', []):
            if s.get('name') in names: return s.get('value')
        return None

    wins   = int(total_stat('wins')   or 0)
    losses = int(total_stat('losses') or 0)
    wp     = float(total_stat('winPercent') or 0)

    # 近 10 場 + vs .500
    evts = sched_data.get('events', [])
    completed = sorted(
        [e for e in evts if e.get('competitions',[{}])[0].get('status',{}).get('type',{}).get('completed')],
        key=lambda e: e.get('date','')
    )
    last10 = completed[-10:]
    l10w = l10l = 0
    l10pts_list = []
    l10opp_list = []
    vaW = vaL = vbW = vbL = 0

    if all_wp is not None:
        for ev in completed:
            comp = ev.get('competitions',[{}])[0]
            me  = next((c for c in comp.get('competitors',[]) if str(c.get('team',{}).get('id')) == eid), None)
            opp = next((c for c in comp.get('competitors',[]) if str(c.get('team',{}).get('id')) != eid), None)
            if not me or not opp: continue
            won = bool(me.get('winner'))
            owp = all_wp.get(str(opp.get('team',{}).get('id')), 0.0)
            if owp >= 0.5:
                vaW += won; vaL += (not won)
            else:
                vbW += won; vbL += (not won)

    for ev in last10:
        comp = ev.get('competitions',[{}])[0]
        me  = next((c for c in comp.get('competitors',[]) if str(c.get('team',{}).get('id')) == eid), None)
        opp = next((c for c in comp.get('competitors',[]) if str(c.get('team',{}).get('id')) != eid), None)
        if not me or not opp: continue
        if me.get('winner'): l10w += 1
        else: l10l += 1
        l10pts_list.append(int(to_score(me.get('score', 0))))
        l10opp_list.append(int(to_score(opp.get('score', 0))))

    n = len(l10pts_list) or 1
    return {
        'wins': wins, 'losses': losses, 'wp': wp,
        'home':   rec_str(home),
        'away':   rec_str(road),
        'l10rec': f'{l10w}-{l10l}',
        'l10off': f'{sum(l10pts_list)/n:.1f}',
        'l10def': f'{sum(l10opp_list)/n:.1f}',
        'va500':  f'{vaW}-{vaL}',
        'vb500':  f'{vbW}-{vbL}',
        'l10pts': ' / '.join(str(p) for p in l10pts_list),
        'l10opp': ' / '.join(str(p) for p in l10opp_list),
    }

def fetch_all_wp():
    def one(eid):
        d = get_espn(f'{BASE}/teams/{eid}')
        items = d.get('team', {}).get('record', {}).get('items', [])
        for it in items:
            if it.get('type','').lower() == 'total':
                for s in it.get('stats',[]):
                    if s.get('name') == 'winPercent':
                        return str(eid), float(s['value'])
        return str(eid), 0.0
    with ThreadPoolExecutor(max_workers=30) as ex:
        return dict(ex.map(one, range(1, 31)))

def parse_input(raw):
    parts = re.split(r'\s*(?:vs\.?|VS\.?|對上|對決|v|:|：)\s*', raw.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2: return None
    def resolve(s):
        for cn, nba_id in TEAM_MAP.items():
            if cn in s: return cn, nba_id
        return None, None
    cnA, idA = resolve(parts[0])
    cnB, idB = resolve(parts[1])
    return (cnA, idA, cnB, idB) if idA and idB else None

def rank_str(r):
    try: return f'(#{int(r)})'
    except: return ''

# ── 主迴圈 ────────────────────────────────────────────────
console.print('[bold orange1]請選擇查詢模式[/]')
console.print('  1) 例行賽')
console.print('  2) 季後賽')
while True:
    mode = console.input('請輸入 1 或 2: ').strip()
    if mode in ('1', '2'):
        break
    console.print('[red]請輸入 1 或 2[/red]')

SEASON_TYPE = '2' if mode == '1' else '3'
SHOW_VS500 = (mode == '1')

df_adv = fetch_all_nba_stats()

while True:
    raw = console.input('\n[bold orange1]請輸入對戰組合[/] [dim](例：湖人:火箭，輸入q退出)[/dim]: ').strip()
    if raw.lower() == 'q':
        break
    parsed = parse_input(raw)
    if not parsed:
        console.print('[red]格式錯誤，請輸入如：湖人vs火箭[/red]')
        continue

    cnA, idA, cnB, idB = parsed

    with console.status('[orange1]📡 載入戰績數據...[/orange1]'):
        all_wp = fetch_all_wp() if SHOW_VS500 else None
        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(fetch_espn_team, idA, all_wp, SEASON_TYPE)
            fb = ex.submit(fetch_espn_team, idB, all_wp, SEASON_TYPE)
            eA, eB = fa.result(), fb.result()

    # NBA.com 進階數據
    def get_row(df, nba_id):
        return df.loc[nba_id] if nba_id in df.index else None

    advA, advB = get_row(df_adv, idA), get_row(df_adv, idB)

    # 用 ESPN 數據估算近 10 場 Pace：(avg_off + avg_def) / 2.3
    def est_pace(off, def_): return round((float(off) + float(def_)) / 2.3, 1)
    l10pace_A = est_pace(eA['l10off'], eA['l10def'])
    l10pace_B = est_pace(eB['l10off'], eB['l10def'])

    def adv_val(row, col, fb='N/A'):
        if row is None: return str(fb) if not isinstance(fb, str) else fb
        v = row.get(col)
        if v is None: return str(fb) if not isinstance(fb, str) else fb
        return f'{v:.1f}'

    def adv_rank(row, col):
        if row is None: return ''
        return rank_str(row.get(col))

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
        ('📈 近 10 場得失分', [
            ('近10場得分', eA['l10pts'], eB['l10pts']),
            ('近10場失分', eA['l10opp'], eB['l10opp']),
        ]),
        ('🔮 預測比分', [
            (f'預測得分 近10場\n(pace {l10pace_A} / {l10pace_B})',
             f'{(float(eA["l10off"]) + float(eB["l10def"])) / 2:.1f}',
             f'{(float(eB["l10off"]) + float(eA["l10def"])) / 2:.1f}'),
            ('PACE法\n(攻守效率 x 平均節奏)',
             f'{float(eA["l10off"]) / max(l10pace_A,1) * (l10pace_A+l10pace_B)/2:.1f}',
             f'{float(eB["l10off"]) / max(l10pace_B,1) * (l10pace_A+l10pace_B)/2:.1f}'),
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
