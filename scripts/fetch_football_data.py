#!/usr/bin/env python3
"""football-data.co.uk 免费历史数据入库（2026-10-11）

免费拿什么：
- 欧洲主联赛（赛季制 /mmz4281/{2526,2627}/{DIV}.csv）：D1/D2/E0/E1/SP1/I1/F1/N1/P1/SC0
- 日历年联赛（/new/{CODE}.csv，2012 至今全历史）：JPN/CHN/BRA/FIN/NOR/SWE/USA
- 关键列：PSCH/PSCD/PSCA = Pinnacle 收盘价（CLV 的尖锐基准，免费！）

产出：data/state/fdc/{name}.csv 原样缓存 + data/state/fdc/parsed.jsonl 归一化
（date, league, home_en, away_en, hg, ag, psc_h/d/a, psh/d/a）

用代理：走 127.0.0.1:7897（与推送同一 Clash）。
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "state" / "fdc"
PROXY = {"https": "http://127.0.0.1:7897", "http": "http://127.0.0.1:7897"}

# 欧洲主联赛（赛季制）：竞彩主要盘口
SEASON_DIVS = ["D1", "D2", "E0", "E1", "SP1", "SP2", "I1", "I2", "F1", "F2",
               "N1", "P1", "SC0", "G1", "T1", "SW1"]
SEASONS = ["2526", "2627"]
# 日历年联赛（/new/ 全历史）
NEW_CODES = ["JPN", "CHN", "BRA", "FIN", "NOR", "SWE", "USA", "ARG", "RO1", "POL", "RUS", "MEX", "AUT"]


def fetch(url: str) -> bytes | None:
    try:
        r = requests.get(url, proxies=PROXY, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200 or b"<!DOCTYPE" in r.content[:200]:
        return None
    return r.content


def fetch_all() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    saved = []
    urls: dict[str, str] = {}
    for season in SEASONS:
        for div in SEASON_DIVS:
            urls[f"{season}_{div}"] = f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
    for code in NEW_CODES:
        urls[f"new_{code}"] = f"https://football-data.co.uk/new/{code}.csv"

    for name, url in urls.items():
        dest = OUT / f"{name}.csv"
        if dest.exists() and dest.stat().st_size > 5000:
            saved.append(dest)               # 已有缓存跳过
            continue
        data = fetch(url)
        if data:
            dest.write_bytes(data)
            saved.append(dest)
            print(f"  ✓ {name}: {len(data)}B")
        else:
            print(f"  ✗ {name} 不可用（跳过）")
    return saved


def _parse_date(s: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_all() -> int:
    """归一化全部缓存 CSV → parsed.jsonl（date/league/home_en/away_en/比分/收盘价）"""
    rows = 0
    with open(OUT / "parsed.jsonl", "w", encoding="utf-8") as out:
        for f in sorted(OUT.glob("*.csv")):
            try:
                text = f.read_text(encoding="utf-8-sig", errors="ignore")
                reader = csv.DictReader(text.splitlines())
                for r in reader:
                    date = _parse_date(r.get("Date", ""))
                    if not date:
                        continue
                    def fl(*keys):
                        for k in keys:
                            v = (r.get(k) or "").strip()
                            if v:
                                try:
                                    return float(v)
                                except ValueError:
                                    pass
                        return None
                    rec = {
                        "src": f.stem,
                        "date": date,
                        "league": (r.get("League") or r.get("Div") or "").strip(),
                        "season": (r.get("Season") or "").strip(),
                        "home_en": (r.get("HomeTeam") or r.get("Home") or "").strip(),
                        "away_en": (r.get("AwayTeam") or r.get("Away") or "").strip(),
                        "hg": r.get("HG") if r.get("HG") not in (None, "") else r.get("FTHG"),
                        "ag": r.get("AG") if r.get("AG") not in (None, "") else r.get("FTAG"),
                        "psc_h": fl("PSCH"), "psc_d": fl("PSCD"), "psc_a": fl("PSCA"),
                        "ps_h": fl("PSH"), "ps_d": fl("PSD"), "ps_a": fl("PSA"),
                        "b365c_h": fl("B365CH"), "b365c_d": fl("B365CD"), "b365c_a": fl("B365CA"),
                        "maxc_h": fl("MaxCH"), "maxc_d": fl("MaxCD"), "maxc_a": fl("MaxCA"),
                        "avgc_h": fl("AvgCH"), "avgc_d": fl("AvgCD"), "avgc_a": fl("AvgCA"),
                        "b365_h": fl("B365H"), "b365_d": fl("B365D"), "b365_a": fl("B365A"),
                    }
                    if rec["home_en"] and rec["away_en"]:
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        rows += 1
            except Exception as e:
                print(f"  ⚠ {f.name} 解析失败: {e}")
    return rows


import json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="football-data.co.uk 免费历史数据入库")
    parser.add_argument("--parse-only", action="store_true", help="跳过下载只重新解析")
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    if not args.parse_only:
        print("下载 CSV ...")
        files = fetch_all()
        print(f"缓存 {len(files)} 个文件")
    n = parse_all()
    print(f"归一化 {n} 行 → {OUT / 'parsed.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
