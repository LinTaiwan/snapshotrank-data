#!/usr/bin/env python3
"""SnapshotRank 排名收集器。

每 3 小時跑一次，把 App Store 各國排行榜存成精簡的文字檔，只保留最近 N 天（預設 30）。
長期歷史由手機 App 自己保存，伺服器這邊只當作「最近幾天的緩衝」。

資料來源（都是 Apple 公開的）：
- App Store 官網排行榜頁（apps.apple.com/{cc}/{iphone|mac}/charts/{genre}）：
  頁面內嵌的資料有免費、付費各前 200 名。iPhone 和 Mac 的總榜（genre 36）、分類榜都用這個
- iPhone 營收榜：官方 RSS feed（itunes.apple.com/{cc}/rss/topgrossingapplications），前 100 名

用法（GitHub Actions 會分成好幾台機器同時跑，每台負責一部分國家）：
    python collect.py collect --shard 0 --shards 8 --partial parts/0.json
    python collect.py publish parts/*.json --out site

輸出（out 目錄）：
    manifest.json
    days/2026-11-14/iphone-free.txt        總榜
    days/2026-11-14/6002-iphone-free.txt   分類榜（6002 = 工具程式）
    days/2026-11-14/mac-free.txt           Mac 總榜（官網的 genre 36）
    days/2026-11-14/6002-mac-free.txt
每個 .txt 一行一個國家：「國碼 第1名ID 第2名ID ...」，名次就是排列順序（舊格式，只留 14 天）。
    days/2026-11-14/apps/00.txt … ff.txt   依 App ID 分包（App 現在讀這個，見 write_shards）
    days/2026-11-14/coverage.txt           每個榜抓到了哪些國家

只用標準函式庫，GitHub Actions 不用另外安裝套件。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import COUNTRIES, IPHONE_GENRES, MAC_GENRES  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
MANIFEST_VERSION = 1
SHARDS = 256  # 分包數。App 用「App ID % 256」找檔案，改了舊版 App 會找錯檔，不要動


class RateGate:
    """同一個主機的所有執行緒共用，自動調整速度：
    - 每個請求之間至少隔 interval 秒（全部執行緒加起來的速度上限）
    - 被 Apple 限流（403／429）：暫停一下，間隔拉長 1.5 倍
    - 順利的請求：間隔慢慢縮回原本的速度
    """

    def __init__(self, interval: float, max_interval: float = 4.0) -> None:
        self._lock = threading.Lock()
        self._next_at = 0.0
        self.base = interval
        self.interval = interval
        self.max_interval = max_interval
        self.hits = 0  # 被限流幾次

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_at:
                    self._next_at = now + self.interval
                    return
                delay = self._next_at - now
            time.sleep(min(delay, 5))

    def ok(self) -> None:
        with self._lock:
            self.interval = max(self.base, self.interval * 0.97)

    def throttled(self, pause: float) -> None:
        with self._lock:
            self.hits += 1
            self.interval = min(self.interval * 1.5, self.max_interval)
            self._next_at = max(self._next_at, time.monotonic() + pause)


def http_get(url: str, gate: RateGate, timeout: float = 30, tries: int = 6) -> bytes | None:
    for attempt in range(tries):
        gate.wait()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "en-US", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            gate.ok()
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # 這個國家沒有這個榜：不用重試
            if e.code in (403, 429):
                # Apple 限流時回 403 或 429：這個請求等久一點再試，整體也放慢一點
                gate.throttled(3)
                time.sleep(2 * 2 ** attempt + random.random() * 2)
            else:
                time.sleep(2 * (attempt + 1) + random.random())
        except Exception:
            time.sleep(2 * (attempt + 1) + random.random())
    return None


# ---------- iPhone：官方 RSS ----------

def rss_url(cc: str, chart: str, genre: str | None) -> str:
    feed = {"free": "topfreeapplications", "paid": "toppaidapplications", "grossing": "topgrossingapplications"}[chart]
    g = f"/genre={genre}" if genre else ""
    return f"https://itunes.apple.com/{cc}/rss/{feed}/limit=100{g}/json"


def parse_rss(body: bytes) -> list[str] | None:
    try:
        d = json.loads(body.decode("utf-8"), strict=False)
    except Exception:
        return None
    entries = d.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    ids = []
    for e in entries:
        try:
            ids.append(str(e["id"]["attributes"]["im:id"]))
        except (KeyError, TypeError):
            continue
    return ids


# ---------- Mac：官網排行榜頁 ----------

SERVER_DATA = re.compile(rb'<script type="application/json" id="serialized-server-data">(.*?)</script>', re.S)


def page_url(cc: str, platform: str, genre: str) -> str:
    return f"https://apps.apple.com/{cc}/{platform}/charts/{genre}?chart=top-free"


def parse_chart_page(body: bytes) -> dict[str, list[str]] | None:
    """回傳 {"free": [...], "paid": [...]}，各最多 200 個。"""
    m = SERVER_DATA.search(body)
    if not m:
        return None
    try:
        d = json.loads(m.group(1).decode("utf-8"))
        segments = d["data"][0]["data"]["segments"]
    except Exception:
        return None
    out: dict[str, list[str]] = {}
    for seg in segments:
        chart = seg.get("chart")  # "top-free" / "top-paid"
        kind = {"top-free": "free", "top-paid": "paid"}.get(chart)
        if not kind:
            continue
        ids: list[str] = []
        try:
            for shelf in seg.get("shelves", []):
                for it in shelf.get("items", []):
                    if it.get("adamId"):
                        ids.append(str(it["adamId"]))
            for it in (seg.get("nextPage") or {}).get("remainingContent", []):
                if it.get("id"):
                    ids.append(str(it["id"]))
        except Exception:
            continue
        # 去重但保留順序
        seen: set[str] = set()
        out[kind] = [i for i in ids if not (i in seen or seen.add(i))]
    return out or None


# ---------- 收集（一個分片） ----------

def shard_countries(shard: int, shards: int) -> list[str]:
    return [cc for i, cc in enumerate(COUNTRIES) if i % shards == shard]


def collect(countries: list[str], ig: list[str], mg: list[str], rss_workers: int, web_workers: int) -> dict:
    """抓這些國家的全部榜單，回傳 {"results": {key: {cc: [ids]}}, "stats": {...}, "fetchedAt": ...}"""
    rss_gate = RateGate(0.5)    # iPhone 營收榜 RSS
    web_gate = RateGate(0.35)   # 官網排行榜頁：每台機器每秒最多約 3 頁
    results: dict[str, dict[str, list[str]]] = {}
    lock = threading.Lock()
    stats = {"rss_ok": 0, "rss_fail": 0, "web_ok": 0, "web_fail": 0}

    def put(key: str, cc: str, ids: list[str]) -> None:
        if ids:
            with lock:
                results.setdefault(key, {})[cc] = ids

    def rss_job(cc: str, chart: str, genre: str | None) -> None:
        body = http_get(rss_url(cc, chart, genre), rss_gate)
        ids = parse_rss(body) if body else None
        with lock:
            stats["rss_ok" if ids is not None else "rss_fail"] += 1
        if ids:
            put((f"{genre}-" if genre else "") + f"iphone-{chart}", cc, ids)

    def web_job(cc: str, platform: str, genre: str) -> None:
        body = http_get(page_url(cc, platform, genre), web_gate)
        parsed = parse_chart_page(body) if body else None
        with lock:
            stats["web_ok" if parsed is not None else "web_fail"] += 1
        for kind, ids in (parsed or {}).items():
            put(("" if genre == "36" else f"{genre}-") + f"{platform}-{kind}", cc, ids)

    # 官網排行榜頁：iPhone、Mac 的總榜（36）和分類榜，一頁同時有免費和付費前 200 名
    web_jobs = [(cc, "iphone", g) for cc in countries for g in ["36"] + ig]
    web_jobs += [(cc, "mac", g) for cc in countries for g in mg]
    # 官網沒有營收榜：iPhone 營收總榜用 RSS（前 100 名）
    rss_jobs = [(cc, "grossing", None) for cc in countries]
    print(f"countries={len(countries)} rss={len(rss_jobs)} web={len(web_jobs)}", flush=True)

    def run(jobs, fn, workers, label):
        t0 = time.time()
        with cf.ThreadPoolExecutor(workers) as ex:
            futs = [ex.submit(fn, *j) for j in jobs]
            for i, f in enumerate(cf.as_completed(futs), 1):
                f.result()
                if i % 250 == 0 or i == len(futs):
                    print(f"  {label} {i}/{len(futs)} {time.time() - t0:.0f}s", flush=True)

    t0 = time.time()
    # RSS 和官網是不同主機，同時跑
    with cf.ThreadPoolExecutor(2) as outer:
        a = outer.submit(run, rss_jobs, rss_job, rss_workers, "rss")
        b = outer.submit(run, web_jobs, web_job, web_workers, "web")
        a.result()
        b.result()
    stats["throttled_rss"] = rss_gate.hits
    stats["throttled_web"] = web_gate.hits
    stats["seconds"] = round(time.time() - t0)
    print(f"collected in {stats['seconds']}s  stats={stats}", flush=True)
    return {
        "fetchedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
        "stats": stats,
    }


# ---------- 發佈（合併分片、寫檔、只留最近 N 天） ----------

def legacy_files(day_dir: str) -> list[str]:
    """這天還在的舊格式檔案（每個榜一個 .txt，不含 coverage.txt 和 apps/）"""
    return sorted(f[:-4] for f in os.listdir(day_dir) if f.endswith(".txt") and f != "coverage.txt")


def write_shards(day_dir: str, results: dict[str, dict[str, list[str]]]) -> None:
    """依 App ID 分包：同一個 App 在所有國家、所有榜的名次寫在同一行，
    依「App ID 除以 SHARDS 的餘數」分成 SHARDS 個小檔。手機只要下載自己追蹤的 App 所在的那一包。

    apps/3f.txt   一行一個 App：「AppID<TAB>榜=國:名次,國:名次<TAB>榜=…」
    coverage.txt  這次每個榜抓到了哪些國家：「榜 國 國 …」。App 要靠它分辨
                  「這個國家這個榜查過、沒上榜」和「這個國家這個榜根本沒抓到」
    """
    buckets: list[dict[str, dict[str, list[str]]]] = [dict() for _ in range(SHARDS)]
    coverage: list[str] = []
    for key, by_cc in sorted(results.items()):
        ccs = []
        for cc, ids in sorted(by_cc.items()):
            if not ids:
                continue
            ccs.append(cc)
            seen: set[str] = set()
            for i, app in enumerate(ids):
                if not app.isdigit() or app in seen:  # 同一份榜裡重複出現：只算最前面那次
                    continue
                seen.add(app)
                buckets[int(app) % SHARDS].setdefault(app, {}).setdefault(key, []).append(f"{cc}:{i + 1}")
        if ccs:
            coverage.append(key + " " + " ".join(ccs))
    apps_dir = os.path.join(day_dir, "apps")
    os.makedirs(apps_dir)
    for n, bucket in enumerate(buckets):
        lines = [
            app + "\t" + "\t".join(f"{k}={','.join(v)}" for k, v in sorted(keys.items()))
            for app, keys in sorted(bucket.items(), key=lambda kv: int(kv[0]))
        ]
        with open(os.path.join(apps_dir, f"{n:02x}.txt"), "w") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    with open(os.path.join(day_dir, "coverage.txt"), "w") as f:
        f.write("\n".join(coverage) + "\n")


def publish(partials: list[dict], out: str, keep_days: int, legacy_days: int = 14) -> int:
    results: dict[str, dict[str, list[str]]] = {}
    stats: dict[str, int] = {}
    fetched = max(p["fetchedAt"] for p in partials)
    for p in partials:
        for key, by_cc in p["results"].items():
            results.setdefault(key, {}).update(by_cc)
        for k, v in p["stats"].items():
            stats[k] = max(stats.get(k, 0), v) if k == "seconds" else stats.get(k, 0) + v
    day = fetched[:10]

    total = stats.get("rss_ok", 0) + stats.get("rss_fail", 0) + stats.get("web_ok", 0) + stats.get("web_fail", 0)
    ok = stats.get("rss_ok", 0) + stats.get("web_ok", 0)
    print(f"[{day}] merged {len(partials)} shard(s): files={len(results)} stats={stats}", flush=True)
    # 大部分都失敗就讓 workflow 標紅，不要用壞資料蓋掉今天
    if not total or ok / total < 0.6:
        print("too many failures, not publishing", file=sys.stderr)
        return 1

    days_root = os.path.join(out, "days")
    day_dir = os.path.join(days_root, day)
    if os.path.isdir(day_dir):
        shutil.rmtree(day_dir)  # 同一天重跑：整天覆蓋
    os.makedirs(day_dir)
    for key, by_cc in sorted(results.items()):
        lines = [f"{cc} " + " ".join(ids) for cc, ids in sorted(by_cc.items())]
        with open(os.path.join(day_dir, key + ".txt"), "w") as f:
            f.write("\n".join(lines) + "\n")

    write_shards(day_dir, results)

    all_days = sorted(d for d in os.listdir(days_root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
    for old in all_days[:-keep_days]:
        shutil.rmtree(os.path.join(days_root, old))
    kept = all_days[-keep_days:]
    # 舊格式（每個榜一個大檔）只留最近 legacy_days 天，給還沒更新的 App 用；更舊的日子只留分包
    legacy_pruned: set[str] = set()
    for d in (kept[:-legacy_days] if legacy_days > 0 else kept):
        ddir = os.path.join(days_root, d)
        if not os.path.isdir(os.path.join(ddir, "apps")):
            shutil.rmtree(ddir)  # 分包上線前的日子：沒有分包，大檔又過期，整天拿掉
            continue
        for f in legacy_files(ddir):
            os.remove(os.path.join(ddir, f + ".txt"))
        legacy_pruned.add(d)
    kept = [d for d in kept if os.path.isdir(os.path.join(days_root, d))]

    mpath = os.path.join(out, "manifest.json")
    old_days: dict[str, dict] = {}
    if os.path.exists(mpath):
        try:
            for d in json.load(open(mpath)).get("days", []):
                old_days[d["date"]] = d
        except Exception:
            pass
    old_days[day] = {"date": day, "fetchedAt": fetched, "files": sorted(results.keys()), "stats": stats}
    days_meta = []
    for d in kept:
        ddir = os.path.join(days_root, d)
        # 檔案在但 manifest 沒記到：從資料夾補
        meta = old_days.get(d) or {"date": d, "fetchedAt": d + "T00:00:00Z", "stats": {}}
        meta["files"] = legacy_files(ddir)  # 以實際還在的檔案為準（過期的大檔已經刪掉）
        if os.path.isdir(os.path.join(ddir, "apps")):
            meta["shards"] = SHARDS
        days_meta.append(meta)
    manifest = {"version": MANIFEST_VERSION, "updatedAt": fetched, "days": days_meta}
    with open(mpath, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"published {day}: {len(results)} files, keeping {len(kept)} day(s)", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SnapshotRank 排名收集器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="抓一個分片的國家，存成 partial JSON")
    c.add_argument("--shard", type=int, default=0)
    c.add_argument("--shards", type=int, default=1)
    c.add_argument("--partial", required=True, help="輸出的 partial JSON 路徑")
    c.add_argument("--countries", help="只抓這些國家，逗號分隔（測試用，會忽略分片）")
    c.add_argument("--iphone-genres", help="只抓這些 iPhone 分類；'none' 不抓分類")
    c.add_argument("--mac-genres", help="只抓這些 Mac 分類；'none' 不抓 Mac")
    c.add_argument("--rss-workers", type=int, default=4)
    c.add_argument("--web-workers", type=int, default=3)

    p = sub.add_parser("publish", help="合併 partial JSON，寫進資料目錄")
    p.add_argument("partials", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--keep-days", type=int, default=30)
    p.add_argument("--legacy-days", type=int, default=14, help="舊格式（每個榜一個大檔）留幾天；0 = 不留")

    args = ap.parse_args()
    if args.cmd == "collect":
        countries = args.countries.split(",") if args.countries else shard_countries(args.shard, args.shards)
        ig = IPHONE_GENRES if not args.iphone_genres else ([] if args.iphone_genres == "none" else args.iphone_genres.split(","))
        mg = MAC_GENRES if not args.mac_genres else ([] if args.mac_genres == "none" else args.mac_genres.split(","))
        data = collect(countries, ig, mg, args.rss_workers, args.web_workers)
        os.makedirs(os.path.dirname(os.path.abspath(args.partial)), exist_ok=True)
        with open(args.partial, "w") as f:
            json.dump(data, f)
        return 0
    partials = [json.load(open(x)) for x in args.partials]
    return publish(partials, args.out, args.keep_days, args.legacy_days)


if __name__ == "__main__":
    sys.exit(main())
