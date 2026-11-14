# snapshotrank-data

SnapshotRank（快照排名）App 的排名收集器。每 3 小時收集一次 174 國 App Store 排行榜，每天保留最後一次、只留最近 30 天；
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
manifest.json                         有哪些日子、收集時間、每天有沒有分包（shards）
days/2026-11-14/apps/00.txt … ff.txt  依 App ID 分包（App 讀這個），留 30 天
days/2026-11-14/coverage.txt          這次每個榜抓到了哪些國家
days/2026-11-14/iphone-free.txt       舊格式：每個榜一個大檔，只留 14 天（給舊版 App）
days/2026-11-14/6002-mac-free.txt     舊格式：Mac 工具程式（6002）免費榜
```

**分包**：`apps/NN.txt` 的 NN 是「App ID 除以 256 的餘數」（兩位十六進位）。一行一個 App：

```
6830033261<TAB>6002-mac-free=ae:139,tw:37<TAB>mac-free=tw:180
```

也就是這個 App 在「榜=國:名次,…」的全部名次。手機只下載自己追蹤的 App 所在的那一包（壓縮後約 37 KB），
不用下載整份榜。`coverage.txt` 一行一個榜：`榜 國 國 …`，App 用它分辨「查過、沒上榜」和「這國這榜沒抓到」。
分包數 256 寫死在 App 裡，不能改。

**舊格式**：每個 `.txt` 一行一個國家：`國碼 第1名ID 第2名ID …`，名次就是排列順序。
所有 App 都更新到讀分包的版本後，把 workflow 的 `--legacy-days` 改成 0 就不再產生。

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
