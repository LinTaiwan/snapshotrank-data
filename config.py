"""要收集的國家與分類。"""

# App Store 國家（ISO 3166-1 alpha-2，小寫）。用 tools/check_countries.py 驗證過 RSS 有資料。
COUNTRIES = """
ae af ag ai al am ao ar at au az ba bb be bf bg bh bj bm bn bo br bs bt bw by bz
ca cd cg ch ci cl cm cn co cr cv cy cz de dk dm do dz ec ee eg es fi fj fm fr
ga gb gd ge gh gm gr gt gw gy hk hn hr hu id ie il in iq is it jm jo jp ke kg
kh kn kr kw ky kz la lb lc lk lr lt lu lv ly ma md me mg mk ml mm mn mo mr ms
mt mu mv mw mx my mz na ne ng ni nl no np nr nz om pa pe pg ph pk pl pt pw py
qa ro rs ru rw sa sb sc se sg si sk sl sn sr st sv sz tc td th tj tm tn to tr
tt tw tz ua ug us uy uz vc ve vg vn vu xk ye za zm zw
""".split()

# iPhone 分類（RSS 的 genre id）。遊戲只抓 6014 這個大類，不抓子分類。
# 不抓已停用的 6021（報刊雜誌）、6022（型錄）和 6025（貼圖）。
IPHONE_GENRES = [
    "6000",  # 商業
    "6001",  # 天氣
    "6002",  # 工具程式
    "6003",  # 旅遊
    "6004",  # 運動
    "6005",  # 社交
    "6006",  # 參考
    "6007",  # 生產力工具
    "6008",  # 照片和影片
    "6009",  # 新聞
    "6010",  # 導航
    "6011",  # 音樂
    "6012",  # 生活風格
    "6013",  # 健康與健身
    "6014",  # 遊戲
    "6015",  # 財經
    "6016",  # 娛樂
    "6017",  # 教育
    "6018",  # 書籍
    "6020",  # 醫藥
    "6023",  # 美食佳飲
    "6024",  # 購物
    "6026",  # 開發者工具
    "6027",  # 圖像與設計
]

# Mac 分類（官網排行榜頁的 genre id）。36 是「所有類別」，也就是 Mac 總榜。
MAC_GENRES = [
    "36", "6000", "6001", "6002", "6003", "6004", "6005", "6006", "6007", "6008",
    "6009", "6011", "6012", "6013", "6014", "6015", "6016", "6017", "6020", "6026", "6027",
]
