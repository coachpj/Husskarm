#!/usr/bin/env python3
"""Uppdaterar data.json för husskärmen med kommande matcher, prospect-statistik
och Dallas/Texas-resultat direkt från ligornas öppna källor.

Körs av GitHub Actions (.github/workflows/stars.yml). Fältet "news" och
spelarnas "note" lämnas orörda (de kommer från Prospekt-radarn).
Om en källa inte svarar behålls föregående värden för just den spelaren/laget.
"""
import json, re, sys, traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

UTC = timezone.utc
NOW = datetime.now(UTC)
STHLM = ZoneInfo("Europe/Stockholm")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
LOG = []

def log(msg):
    LOG.append(msg)
    print(msg, flush=True)

S = requests.Session()
S.headers.update(UA)

def get(url, **kw):
    last = None
    for _ in range(3):
        try:
            r = S.get(url, timeout=40, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa
            last = e
    raise last

def get_json(url):
    txt = get(url).text.strip()
    if txt.startswith("(") and txt.endswith(")"):
        txt = txt[1:-1]
    return json.loads(txt)

def iso_z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00Z")

def i(x, default=0):
    try:
        return int(float(str(x).replace(",", ".").replace("−", "-")))
    except Exception:
        return default

def signed(n):
    return f"+{n}" if n > 0 else ("−" + str(-n) if n < 0 else "0")

# ---------------------------------------------------------------- KONFIG
HT = {  # HockeyTech (chl.ca, theahl.com)
    "whl": "41b145a848f4bd67",
    "ohl": "f1aa699db3d81487",
    "ahl": "50c2cd9b5e18e390",
}
HT_URL = "https://lscluster.hockeytech.com/feed/index.php"

# n = namnet exakt som i data.json
PROSPECTS = [
    {"n": "Cameron Schmidt", "who": "Schmidt", "src": "ht", "lg": "whl", "team_id": 227, "pid": 29235, "club": "Victoria Royals"},
    {"n": "Jakub Vaněček", "who": "Vaněček", "src": "ht", "lg": "whl", "team_id": 217, "pid": 29955, "club": "Tri-City Americans"},
    {"n": "Ryan Brown", "who": "Brown", "src": "ht", "lg": "ohl", "team_id": 14, "pid": 8920, "club": "London Knights"},
    {"n": "Brandon Gorzynski", "who": "Gorzynski", "src": "ht", "lg": "whl", "team_id": 209, "pid": 29502, "club": "Prince Albert Raiders"},
    {"n": "Mikhail Cherepanov", "who": "Cherepanov", "src": "ht", "lg": "whl", "team_id": 203, "pid": 30657, "club": "Kamloops Blazers"},
    {"n": "Jasper Kuhta", "who": "Kuhta", "src": "ncaa", "ics": "https://umassathletics.com/calendar.ashx/calendar.ics?sport_id=8", "chi": 125877, "club": "UMass"},
    {"n": "Dawson Sharkey", "who": "Sharkey", "src": "ncaa", "ics": "https://nuhuskies.com/calendar.ashx/calendar.ics?sport_id=7", "chi": 128431, "club": "Northeastern"},
    {"n": "William Samuelsson", "who": "Samuelsson", "src": "ncaa", "ics": "https://lssulakers.com/calendar.ashx/calendar.ics?sport_id=5", "chi": 125371, "club": "Lake Superior State"},
    {"n": "George Fegaras", "who": "Fegaras", "src": "ncaa", "ics": "https://cornellbigred.com/calendar.ashx/calendar.ics?sport_id=7", "chi": 58829, "club": "Cornell"},
    {"n": "Atte Joki", "who": "Joki", "src": "liiga", "lpid": 40114901, "team": "HIFK", "club": "HIFK"},
    {"n": "Måns Goos", "who": "Goos", "src": "swe", "series": 20962, "team": "BIK Karlskoga", "goalie": "Goos, Måns", "club": "BIK Karlskoga"},
    {"n": "Anton Emil Wilde Larsen", "who": "Wilde Larsen", "src": "swe", "series": 20964, "team": "Linköping HC", "goalie": "Wilde Larsen, Anton", "club": "Linköping HC J20", "oppsuffix": " J20"},
]
LIIGA_SEASON = 2027
NHL_SEASON = "20262027"
PROSPECT_DAYS = 7
STARS_DAYS = 14

# ---------------------------------------------------------------- HOCKEYTECH
_ht_season = {}

def ht(lg, **params):
    p = {"key": HT[lg], "client_code": lg, "fmt": "json"}
    p.update(params)
    q = "&".join(f"{k}={v}" for k, v in p.items())
    return get_json(f"{HT_URL}?{q}")

def ht_season(lg):
    if lg not in _ht_season:
        seasons = ht(lg, feed="modulekit", view="seasons")["SiteKit"]["Seasons"]
        today = NOW.date().isoformat()
        cand = [s for s in seasons if str(s.get("playoff")) == "0" and "regular" in s.get("season_name", "").lower()
                and s.get("start_date", "") <= today]
        cand.sort(key=lambda s: s.get("start_date", ""), reverse=True)
        _ht_season[lg] = int(cand[0]["season_id"])
    return _ht_season[lg]

def ht_schedule(lg, team_id):
    sid = ht_season(lg)
    return ht(lg, feed="modulekit", view="schedule", team_id=team_id, season_id=sid)["SiteKit"]["Schedule"]

_TZ_ABBR = {"P": "America/Los_Angeles", "M": "America/Denver", "C": "America/Chicago", "E": "America/New_York",
            "A": "America/Halifax"}

def ht_game_time(g):
    # GameDateISO8601 har ibland fel offset (sommartid i november), så räkna
    # själv från lokal tid + tidszonsförkortningen i scheduled_time.
    try:
        dp, st = g["date_played"], g["schedule_time"]
        tzf = str(g.get("timezone", "")).replace("\\/", "/")
        m = re.search(r"\b([PMCEA])[SDC]T\b", str(g.get("scheduled_time", "")).upper())
        if any(k in tzf for k in ("Regina", "Saskatchewan", "Swift")):
            zone = "America/Regina"
        elif m:
            zone = _TZ_ABBR[m.group(1)]
        else:
            zone = tzf
        return datetime.fromisoformat(f"{dp}T{st}").replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)
    except Exception:
        return datetime.fromisoformat(g["GameDateISO8601"]).astimezone(UTC)

def ht_opp(g, team_id):
    home = str(g["home_team"]) == str(team_id)
    side = "visiting" if home else "home"
    name = g.get(f"{side}_team_name") or ""
    city = g.get(f"{side}_team_city") or name
    return home, name, city

def ht_upcoming(lg, team_id, days):
    out = []
    for g in ht_schedule(lg, team_id):
        if str(g.get("final")) == "1":
            continue
        t = ht_game_time(g)
        if NOW - timedelta(hours=3) < t <= NOW + timedelta(days=days):
            home, name, city = ht_opp(g, team_id)
            out.append({"t": iso_z(t), "opp": name, "s": city, "home": home})
    return out

def ht_player(p):
    lg, pid = p["lg"], p["pid"]
    sid = ht_season(lg)
    d = ht(lg, feed="modulekit", view="player", category="seasonstats", player_id=pid)["SiteKit"]["Player"]
    row = [r for r in d.get("regular", []) if str(r.get("season_id")) == str(sid)]
    if not row:
        raise ValueError("ingen säsongsrad")
    # ibland finns både lagrader och en totalrad, eller dubbletter: välj totalrad, annars en rad per lag
    tot = [r for r in row if str(r.get("team_id", "")).strip() in ("", "0") or "total" in str(r.get("team_name", "")).lower()]
    if tot:
        row = tot[:1]
    else:
        row = list({str(r.get("team_id")): r for r in row}.values())
    s = {"gp": 0, "g": 0, "a": 0, "p": 0, "pm": 0, "pim": 0}
    for r in row:
        s["gp"] += i(r.get("games_played")); s["g"] += i(r.get("goals")); s["a"] += i(r.get("assists"))
        s["p"] += i(r.get("points")); s["pm"] += i(r.get("plus_minus")); s["pim"] += i(r.get("penalty_minutes"))
    gl = ht(lg, feed="modulekit", view="player", category="gamebygame", player_id=pid, season_id=sid)["SiteKit"]["Player"].get("games", [])
    today = NOW.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    gl = [g for g in gl if g.get("date_played", "9") <= today]
    gl.sort(key=lambda g: (g.get("date_played", ""), str(g.get("id"))))
    last = gl[-5:]
    l5 = {"gp": len(last), "g": sum(i(g.get("goals")) for g in last), "a": sum(i(g.get("assists")) for g in last)}
    l5["p"] = l5["g"] + l5["a"]
    return s, l5

# ---------------------------------------------------------------- NCAA
def ics_events(url):
    txt = get(url).text.replace("\r\n", "\n")
    txt = re.sub(r"\n[ \t]", "", txt)  # avveckla vikta rader
    evs = []
    for block in txt.split("BEGIN:VEVENT")[1:]:
        ev = {}
        for line in block.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                ev[k.split(";")[0]] = v.replace("\\,", ",").replace("\\;", ";").replace("&amp;", "&")
        evs.append(ev)
    return evs

def clean_ncaa_opp(x):
    x = re.split(r" - |\s\|\s", x)[0]
    x = re.sub(r"^\(?#?\d+\)?\s+", "", x.strip())
    x = re.sub(r"\s*\(.*?\)\s*$", "", x)
    return x.strip()

def short_ncaa(x):
    s = re.sub(r"^(The )?University of ", "", x)
    s = re.sub(r"\s+(University|College)$", "", s)
    s = re.sub(r"\s+(Huskies|Skyhawks|Friars|Beavers|Golden Knights|Saints|Bears|Big Red)$", "", s)
    return s.strip()

def ncaa_upcoming(url, days):
    out, seen = [], set()
    for ev in ics_events(url):
        uid = ev.get("UID")
        if uid in seen:
            continue
        seen.add(uid)
        ds = ev.get("DTSTART", "")
        if not re.match(r"^\d{8}T\d{6}Z$", ds):
            continue  # saknar klockslag – hoppa hellre över än att gissa
        t = datetime.strptime(ds, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if not (NOW - timedelta(hours=3) < t <= NOW + timedelta(days=days)):
            continue
        summ = re.sub(r"^\[[WLT]\]\s*", "", ev.get("SUMMARY", "")).replace("  ", " ")
        m = re.search(r"\s(vs\.?|at)\s+(.*)$", summ)
        if not m:
            continue
        opp = clean_ncaa_opp(m.group(2))
        out.append({"t": iso_z(t), "opp": opp, "s": short_ncaa(opp), "home": m.group(1).startswith("vs")})
    return out

def table_rows(soup):
    for tr in soup.find_all("tr"):
        if tr.find("table"):
            continue  # yttre layoutrad med en nästlad tabell
        cells = [c.get_text(" ", strip=True).replace("‑", "-").replace("−", "-") for c in tr.find_all(["td", "th"])]
        yield tr, cells

def ncaa_player(chi_id):
    html = get(f"https://collegehockeyinc.com/players/career/{chi_id}").text
    soup = BeautifulSoup(html, "html.parser")
    season = None
    games = {}
    for tr, cells in table_rows(soup):
        if not cells:
            continue
        if season is None and re.match(r"^\s*2026.27\b", cells[0]) and len(cells) >= 11:
            nums = cells[-10:]
            if all(re.match(r"^-?\d+$", c or "x") for c in nums):
                season = {"gp": i(nums[0]), "g": i(nums[1]), "a": i(nums[2]), "p": i(nums[3]),
                          "pim": i(nums[7]), "pm": i(nums[9])}
        a = tr.find("a", href=re.compile(r"/box/final/(\d{8})/"))
        if a and len(cells) >= 7:
            m = re.search(r"/box/final/(\d{8})/([^/]+)/([^/]+)", a["href"])
            g, as_, pts = cells[-6], cells[-5], cells[-4]
            if re.match(r"^\d+$", g) and re.match(r"^\d+$", as_):
                games[m.group(0)] = (m.group(1), i(g), i(as_))
    if season is None:
        raise ValueError("hittade ingen 2026-27-rad")
    gl = sorted(games.values())[-5:]
    l5 = {"gp": len(gl), "g": sum(x[1] for x in gl), "a": sum(x[2] for x in gl)}
    l5["p"] = l5["g"] + l5["a"]
    return season, l5

# ---------------------------------------------------------------- LIIGA
_liiga_games = None

def liiga_games():
    global _liiga_games
    if _liiga_games is None:
        _liiga_games = get_json(f"https://liiga.fi/api/v2/games?tournament=runkosarja&season={LIIGA_SEASON}")
    return _liiga_games

def liiga_team_games(team):
    return [g for g in liiga_games() if team in (g["homeTeam"].get("teamName"), g["awayTeam"].get("teamName"))]

def liiga_upcoming(team, days):
    out = []
    for g in liiga_team_games(team):
        if g.get("ended"):
            continue
        t = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        if NOW - timedelta(hours=3) < t <= NOW + timedelta(days=days):
            home = g["homeTeam"]["teamName"] == team
            opp = (g["awayTeam"] if home else g["homeTeam"])["teamName"]
            out.append({"t": iso_z(t), "opp": opp, "s": opp, "home": home})
    return out

def liiga_player(p):
    info = get_json(f"https://liiga.fi/api/v2/players/info/{p['lpid']}")
    rows = [r for r in info.get("historical", {}).get("regular", [])
            if r.get("season") == LIIGA_SEASON and str(r.get("tournament", "")).upper() == "RUNKOSARJA"]
    if not rows:
        raise ValueError("ingen säsongsrad")
    s = {"gp": sum(i(r.get("games")) for r in rows), "g": sum(i(r.get("goals")) for r in rows),
         "a": sum(i(r.get("assists")) for r in rows), "p": sum(i(r.get("points")) for r in rows),
         "pm": sum(i(r.get("plusMinus")) for r in rows), "pim": sum(i(r.get("penaltyMinutes")) for r in rows),
         "toi": sum(i(r.get("timeOnIce")) for r in rows), "played": sum(i(r.get("playedGames")) for r in rows)}
    l5 = None
    try:
        ended = sorted([g for g in liiga_team_games(p["team"]) if g.get("ended")], key=lambda g: g["start"])[-5:]
        l5 = {"gp": 0, "g": 0, "a": 0}
        for g in ended:
            st = get_json(f"https://liiga.fi/api/v2/games/stats/{LIIGA_SEASON}/{g['id']}")
            found = False
            for side in ("homeTeam", "awayTeam"):
                for per in st.get(side, []) or []:
                    for ps in per.get("periodPlayerStats", []) or []:
                        if ps.get("playerId") == p["lpid"]:
                            found = True
                            l5["g"] += i(ps.get("period", {}).get("goals"))
                            l5["a"] += i(ps.get("period", {}).get("assists"))
            if found:
                l5["gp"] += 1
        l5["p"] = l5["g"] + l5["a"]
    except Exception as e:
        log(f"Liiga l5: {e}")
    return s, l5

# ---------------------------------------------------------------- SWEHOCKEY
def swe_short(x):
    x = re.sub(r"\b(IK|IF|HC|HK|BK|AIF|IS|HF|SK|HC|BIK|Hockey|Lakers|IBK)\b", "", x)
    return re.sub(r"\s+", " ", x).strip() or x

def swe_upcoming(series, team, days, suffix=""):
    html = get(f"https://stats.swehockey.se/ScheduleAndResults/Schedule/{series}").text
    soup = BeautifulSoup(html, "html.parser")
    out, cur_date = [], None
    for tr, cells in table_rows(soup):
        joined = " | ".join(cells)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", joined)
        if m:
            cur_date = m.group(1)
        if team not in joined or not cur_date:
            continue
        game = next((c for c in cells if " - " in c and team in c), None)
        tm = re.search(r"\b(\d{1,2}:\d{2})\b", joined)
        if not game or not tm:
            continue
        if any(re.match(r"^\d+ - \d+$", c) for c in cells):
            continue  # redan spelad
        h, a = [x.strip() for x in game.split(" - ", 1)]
        t = datetime.fromisoformat(f"{cur_date}T{tm.group(1).zfill(5)}").replace(tzinfo=STHLM)
        if NOW - timedelta(hours=3) < t <= NOW + timedelta(days=days):
            home = team in h
            opp = a if home else h
            out.append({"t": iso_z(t), "opp": opp + suffix, "s": swe_short(opp), "home": home})
    return out

def swe_goalie(series, name):
    html = get(f"https://stats.swehockey.se/Players/Statistics/LeadingGoaliesSVS/{series}").text
    soup = BeautifulSoup(html, "html.parser")
    header = None
    for tr, cells in table_rows(soup):
        if "GPI" in cells and "GAA" in cells:
            header = cells
            continue
        if header and any(name in c for c in cells):
            off = len(cells) - len(header)
            col = lambda k: cells[header.index(k) + off]
            svp = float(col("SVS%").replace(",", ".")) / 100
            return {"gp": i(col("GPI")), "w": i(col("W")), "l": i(col("L")),
                    "gaa": round(float(col("GAA").replace(",", ".")), 2), "sv": round(svp, 3), "so": i(col("SO"))}
    raise ValueError(f"{name} saknas i målvaktsstatistiken")

# ---------------------------------------------------------------- NHL / DALLAS
def nhl_games():
    return get_json(f"https://api-web.nhle.com/v1/club-schedule-season/DAL/{NHL_SEASON}")["games"]

def nhl_team_name(t):
    return f"{t.get('placeName', {}).get('default', '')} {t.get('commonName', {}).get('default', '')}".strip()

def nhl_upcoming(games, days):
    out = []
    for g in games:
        if g.get("gameType") != 2 or g.get("gameState") in ("OFF", "FINAL"):
            continue
        t = datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00"))
        if NOW - timedelta(hours=3) < t <= NOW + timedelta(days=days):
            home = g["homeTeam"]["abbrev"] == "DAL"
            o = g["awayTeam"] if home else g["homeTeam"]
            out.append({"team": "dallas", "t": iso_z(t), "opp": nhl_team_name(o),
                        "s": o.get("placeName", {}).get("default", ""), "home": home})
    return out

def fmt_scorers(lst):
    order, cnt, tags = [], {}, {}
    for name, tag in lst:
        if name not in cnt:
            order.append(name); cnt[name] = 0; tags[name] = []
        cnt[name] += 1
        if tag:
            tags[name].append(tag)
    res = []
    for n in order:
        s = n + (f" {cnt[n]}" if cnt[n] > 1 else "")
        if tags[n]:
            s += " (" + ", ".join(dict.fromkeys(tags[n])) + ")"
        res.append(s)
    return res

def nhl_result_doc(g):
    gid = g["id"]
    land = get_json(f"https://api-web.nhle.com/v1/gamecenter/{gid}/landing")
    box = get_json(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore")
    home = g["homeTeam"]["abbrev"] == "DAL"
    me, op = (g["homeTeam"], g["awayTeam"]) if home else (g["awayTeam"], g["homeTeam"])
    ms, os_ = i(me.get("score")), i(op.get("score"))
    lpt = (g.get("gameOutcome") or {}).get("lastPeriodType") or land.get("periodDescriptor", {}).get("periodType", "REG")
    res = ("W" if ms > os_ else "L") if lpt == "REG" else (("OT" if lpt == "OT" else "SO") + ("W" if ms > os_ else "L"))
    sc, osc = [], []
    for per in land.get("summary", {}).get("scoring", []):
        ptype = per.get("periodDescriptor", {}).get("periodType")
        for gl in per.get("goals", []):
            name = f"{gl.get('firstName', {}).get('default', '')} {gl.get('lastName', {}).get('default', '')}".strip()
            tag = {"pp": "PP", "sh": "SH"}.get(gl.get("strength"))
            if ptype == "OT":
                tag = "OT"
            if ptype == "SO":
                continue
            if gl.get("goalModifier") == "empty-net":
                tag = "EN"
            (sc if gl.get("teamAbbrev", {}).get("default") == "DAL" else osc).append((name, tag))
    note = ""
    try:
        side = "homeTeam" if home else "awayTeam"
        gs = [x for x in box["playerByGameStats"][side]["goalies"] if x.get("shotsAgainst")]
        note = ", ".join(f"{x['name']['default']} {i(x.get('saves'))} räddningar" for x in gs)
    except Exception:
        pass
    date = g.get("gameDate")
    oppname = nhl_team_name(op)
    return {"team": "dallas", "d": date, "title": f"Dallas Stars {ms}–{os_} {oppname}", "res": res,
            "sc": fmt_scorers(sc), "note": note}

def ahl_result_doc(g, team_id):
    lg = "ahl"
    gs = ht(lg, feed="gc", tab="gamesummary", game_id=g["id"])["GC"]["Gamesummary"]
    home = str(g["home_team"]) == str(team_id)
    hg, vg = i(g.get("home_goal_count")), i(g.get("visiting_goal_count"))
    ms, os_ = (hg, vg) if home else (vg, hg)
    st = str(g.get("game_status", ""))
    res = "W" if ms > os_ else "L"
    if "SO" in st:
        res = "SO" + res
    elif "OT" in st:
        res = "OT" + res
    sc = []
    for gl in gs.get("goals", []) or []:
        if str(gl.get("team_id")) != str(team_id):
            continue
        p = gl.get("goal_scorer") or {}
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        tag = "PP" if str(gl.get("power_play")) == "1" else ("SH" if str(gl.get("short_handed")) == "1" else None)
        if str(gl.get("empty_net")) == "1":
            tag = "EN"
        sc.append((name, tag))
    _, oppname, _ = ht_opp(g, team_id)
    return {"team": "texas", "d": g["date_played"], "title": f"Texas Stars {ms}–{os_} {oppname}", "res": res,
            "sc": fmt_scorers(sc), "note": ""}

# ---------------------------------------------------------------- TEXTRADER
def stats_line_skater(s):
    return f"{s['gp']} GP · {s['g']} M · {s['a']} A · {s['p']} P · {signed(s['pm'])}"

def stats_line_goalie(s):
    sv = f"{s['sv']:.3f}".lstrip("0")
    return f"{s['gp']} GP · {s['w']}-{s['l']} · {s['gaa']:.2f} GAA · {sv}"

# ---------------------------------------------------------------- HUVUD
def main():
    data = json.load(open("data.json", encoding="utf-8"))
    before = json.dumps({k: v for k, v in data.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)
    pros = {p["n"]: p for p in data.get("prospects", [])}
    old_next = data.get("next", [])
    nxt = []
    ok_who = set()

    for p in PROSPECTS:
        entry = pros.get(p["n"])
        try:
            if p["src"] == "ht":
                s, l5 = ht_player(p)
                up = ht_upcoming(p["lg"], p["team_id"], PROSPECT_DAYS)
            elif p["src"] == "ncaa":
                s, l5 = ncaa_player(p["chi"])
                up = ncaa_upcoming(p["ics"], PROSPECT_DAYS)
            elif p["src"] == "liiga":
                s, l5 = liiga_player(p)
                up = liiga_upcoming(p["team"], PROSPECT_DAYS)
            elif p["src"] == "swe":
                s, l5 = swe_goalie(p["series"], p["goalie"]), None
                up = swe_upcoming(p["series"], p["team"], PROSPECT_DAYS, p.get("oppsuffix", ""))
            if entry is not None:
                if "w" in s:
                    entry["s"] = stats_line_goalie(s)
                else:
                    line = stats_line_skater(s)
                    if p["src"] == "liiga" and s.get("played"):
                        mm, ss = divmod(s["toi"] // s["played"], 60)
                        line += f" · {mm}:{ss:02d} istid/match"
                    entry["s"] = line
                    if l5 and l5["gp"]:
                        entry["l5"] = f"{l5['g']}+{l5['a']}={l5['p']}"
                        entry["l5p"] = l5["p"]
            for u in up:
                nxt.append({"team": "prospect", "who": p["who"], "club": p["club"], **u})
            ok_who.add(p["who"])
            log(f"OK {p['n']}: {entry['s'] if entry else s} | {len(up)} kommande")
        except Exception as e:
            log(f"FEL {p['n']}: {e}")
            traceback.print_exc()

    # Dallas
    games_new = []
    try:
        ng = nhl_games()
        nxt += nhl_upcoming(ng, STARS_DAYS)
        ok_who.add("dallas")
        done = sorted([g for g in ng if g.get("gameType") == 2 and g.get("gameState") in ("OFF", "FINAL")],
                      key=lambda g: g["startTimeUTC"])[-3:]
        for g in done:
            try:
                games_new.append(nhl_result_doc(g))
            except Exception as e:
                log(f"FEL Dallas-match {g.get('id')}: {e}")
        log(f"OK Dallas: {len(done)} färdiga, kommande hämtade")
    except Exception as e:
        log(f"FEL Dallas: {e}")
    # Texas
    try:
        sch = ht_schedule("ahl", 380)
        for g in sch:
            if str(g.get("final")) == "1":
                continue
            t = ht_game_time(g)
            if NOW - timedelta(hours=3) < t <= NOW + timedelta(days=STARS_DAYS):
                home, name, city = ht_opp(g, 380)
                nxt.append({"team": "texas", "t": iso_z(t), "opp": name, "s": city, "home": home})
        ok_who.add("texas")
        done = sorted([g for g in sch if str(g.get("final")) == "1"], key=lambda g: g["GameDateISO8601"])[-3:]
        for g in done:
            try:
                games_new.append(ahl_result_doc(g, 380))
            except Exception as e:
                log(f"FEL Texas-match {g.get('id')}: {e}")
        log(f"OK Texas: {len(done)} färdiga")
    except Exception as e:
        log(f"FEL Texas: {e}")

    # behåll tidigare poster för källor som inte svarade
    for x in old_next:
        key = x.get("who") if x.get("team") == "prospect" else x.get("team")
        if key not in ok_who:
            t = datetime.fromisoformat(x["t"].replace("Z", "+00:00"))
            if t > NOW - timedelta(hours=3):
                nxt.append(x)
    nxt.sort(key=lambda x: x["t"])
    data["next"] = nxt

    # games: lägg till nya resultat, behåll befintliga (radarns noteringar går före)
    games = data.get("games", [])
    have = {(g["team"], g["d"]) for g in games}
    for g in games_new:
        if (g["team"], g["d"]) not in have:
            games.append(g)
    games.sort(key=lambda g: g["d"], reverse=True)
    data["games"] = games[:4]

    if not data.get("prospects"):
        sys.exit("prospects tom – skriver inte")
    after = json.dumps({k: v for k, v in data.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)
    if after == before:
        print("Inget nytt – data.json lämnas orörd")
        return
    data["updated"] = NOW.astimezone(STHLM).isoformat(timespec="seconds")
    txt = json.dumps(data, ensure_ascii=False, indent=1)
    json.loads(txt)
    open("data.json", "w", encoding="utf-8").write(txt + "\n")
    errors = [l for l in LOG if l.startswith("FEL")]
    print(f"\nKlart: {len(nxt)} kommande matcher, {len(errors)} fel")

if __name__ == "__main__":
    main()
