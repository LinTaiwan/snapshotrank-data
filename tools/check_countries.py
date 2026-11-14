#!/usr/bin/env python3
"""檢查 config.COUNTRIES 每個國家的 iPhone 免費總榜 RSS 有沒有資料。"""
import concurrent.futures as cf, json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import COUNTRIES

def check(cc):
    url = f"https://itunes.apple.com/{cc}/rss/topfreeapplications/limit=10/json"
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.loads(r.read().decode(), strict=False)
            e = d.get("feed", {}).get("entry", [])
            return cc, len(e if isinstance(e, list) else [e])
        except urllib.error.HTTPError as ex:
            if ex.code in (400, 404):
                return cc, -ex.code
        except Exception:
            pass
    return cc, None

with cf.ThreadPoolExecutor(6) as ex:
    res = list(ex.map(check, COUNTRIES))
bad = [(c, n) for c, n in res if not n or n <= 0]
print(f"{len(COUNTRIES)} countries, {len(COUNTRIES) - len(bad)} ok")
print("bad:", bad)
