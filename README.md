# snapshotrank-data

SnapshotRank（快照排名）App 的排名收集器。每 3 小時收集一次 174 國 App Store 排行榜，每天保留最後一次、只留最近 14 天；
長期歷史存在每個使用者自己的手機上。

## 收集什麼

| 榜單 | 來源 | 深度 |
|---|---|---|
| iPhone 總榜、24 個分類榜（免費、付費） | App Store 官網排行榜頁 `apps.apple.com/{cc}/iphone/charts/{genre}` | 前 200 名 |
| Mac 總榜、20 個分類榜（免費、付費） | App Store 官網排行榜頁 `apps.apple.com/{cc}/mac/charts/{genre}` | 前 200 名 |
| iPhone 營收總榜 | 官方 RSS `itunes.apple.com/{cc}/rss/topgrossingapplications` | 前 100 名 |

官網排行榜頁的 HTML 裡內嵌了 `serialized-server-data`，免費、付費兩份各有前 25 名完整資料，
加上後面 175 個 App ID，所以一頁就有兩個榜的前 200 名。國家和分類在 `config.py`。

## 資料格式（`data` 分支）

```
manifest.json                         有哪些日子、每天有哪些檔案、收集時間
days/2026-11-14/iphone-free.txt       iPhone 免費總榜
days/2026-11-14/iphone-grossing.txt   iPhone 營收總榜
days/2026-11-14/6002-iphone-free.txt  iPhone 工具程式（6002）免費榜
days/2026-11-14/mac-paid.txt          Mac 付費總榜
days/2026-11-14/6002-mac-free.txt     Mac 工具程式免費榜
```

每個 `.txt` 一行一個國家：`國碼 第1名ID 第2名ID …`，名次就是排列順序。
App 從 `https://compoundwater.app/snapshotrank/data/` 讀取（主網站 vercel.json 把這個路徑轉到本 repo 的 data 分支）。

`data` 分支每次都強制覆蓋成只有一個 commit，所以 repo 不會因為每天的資料越長越大。

## 排程

`.github/workflows/collect.yml`：每 3 小時一次（UTC 00:17、03:17 …）。同一天的每次收集都覆蓋當天的檔案，
`manifest.json` 的 `fetchedAt` 跟著更新；App 看到收集時間變了就再同步一次，所以手機上會有一天內的變化。
GitHub 排程常延遲，App 以實際收集時間為準。

- `collect`：分成 8 台機器同時跑，每台負責約 22 個國家（每台的 IP 不同，對 Apple 的請求分散開）
- `publish`：合併 8 份結果，寫進 `data` 分支；成功率低於 60% 就不發佈，避免用壞資料蓋掉

Apple 被打太多時會回 403／429，`collect.py` 會讓那個請求退避重試，整體也自動放慢。

## 本機測試

```bash
python3 collect.py collect --countries tw,jp --iphone-genres 6002 --mac-genres 36,6002 --partial out/p.json
python3 collect.py publish out/p.json --out out/site
python3 -m http.server 8765 --directory out/site   # App 用 RANK_SERVER=http://localhost:8765/ 指過來
```

`tools/check_countries.py` 檢查 `config.COUNTRIES` 每個國家是否有資料。
