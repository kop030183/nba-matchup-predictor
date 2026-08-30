# NBA 對戰預測小工具

輸入兩支球隊（中文隊名），自動抓取 NBA.com 與 ESPN 的最新數據，
整理成戰績、效率、近期手感等比較表，並估算兩隊預測比分。

---

## 1. 取得專案原始碼

```bash
git clone https://github.com/kop030183/nba-matchup-predictor.git
cd nba-matchup-predictor
```

## 2. 環境準備

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

---

## 3. 執行方式

```powershell
python nba_matchup.py
```

### 第一步：選擇查詢模式

```
請選擇查詢模式
  1. 例行賽
  2. 季後賽
請輸入 1 或 2:
```

- **輸入 `1`（例行賽）**：抓取例行賽數據，會額外顯示「🎯 五成球隊戰況」表格。
- **輸入 `2`（季後賽）**：抓取季後賽數據（僅在季後賽期間有資料）。
  季後賽對手勝率必然 ≥ 五成，所以不會顯示「🎯 五成球隊戰況」表格。

選好模式後，程式會花約 20~30 秒載入聯盟整體數據（NBA.com 進階數據，含限流保護的延遲），
接著會看到提示：

```
請輸入對戰組合 (例：(客隊):(主隊)，輸入q退出):
```

輸入兩隊名稱，**後面輸入的那隊視為主場**（例：`尼克:馬刺` = 尼克客場、馬刺主場），中間用以下任一種寫法分隔都可以：

- `尼克:馬刺`
- `尼克：馬刺`（全形冒號）
- `尼克vs馬刺` / `尼克VS馬刺`
- `尼克 對上 馬刺` / `尼克 對決 馬刺`
- `尼克v馬刺`

輸入 `q` 結束程式（不需重新選擇模式即可繼續查詢下一組對戰）。

### 支援的球隊（30隊）
全部 NBA 30 支球隊的中文俗名，例如：
湖人、塞爾提克、勇士、公牛、騎士、金塊、雷霆、馬刺、暴龍、七六人、尼克、籃網、活塞、溜馬、公鹿、老鷹、黃蜂、熱火、魔術、巫師、
灰狼、拓荒者、爵士、快艇、太陽、國王、獨行俠（小牛）、火箭、灰熊、鵜鶘。

---

## 3.5 用 FastAPI 跑成 API 服務

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000
```

啟動後開瀏覽器到 `http://localhost:8000/docs` 看 Swagger UI，呼叫 `/matchup?teamA=尼克&teamB=馬刺` 這類端點，回傳結構化 JSON 預測結果（近10場平均法、PACE調整法兩種預測分數）。`teamA` 為客隊、`teamB` 為主隊，主隊預測分已包含主場優勢加成。

⚠️ 目前只能本機存取（`localhost` 只代表「這台電腦自己」），要讓其他人也連得到，需要部署到有公開 IP 的雲端主機（例如 AWS EC2），或用 ngrok 之類的工具做臨時穿透分享。

⚠️ 伺服器啟動後約需 20~30 秒才會開始接受請求（啟動時會先抓取整季 NBA/ESPN 數據快取起來，之後每次呼叫 `/matchup` 才會快），這段時間屬正常現象，終端機顯示 `Application startup complete` 才代表真正就緒。

---

## 3.6 用 Docker 執行

```bash
docker build -t nba-api .
docker run -p 8000:8000 nba-api
```

⚠️ **`Dockerfile` 的 `CMD` 啟動的是 FastAPI 服務（`uvicorn api:app`），不是互動式 CLI**——容器跑起來後一樣是連到 `http://localhost:8000/docs` 使用，跟上面「用 FastAPI 跑成 API 服務」是同一個服務、只是換成容器化的方式啟動。如果想要互動式 CLI 版本，直接用「3. 執行方式」教的 `python nba_matchup.py`（不透過 Docker）。

---

## 4. 輸出內容說明

每次查詢會印出多張表格：

| 表格 | 內容 |
|---|---|
| 📋 整季戰績 | 兩隊整季勝負、主場/客場戰績|
| ⚡ 進攻/防守效率 | 本賽季 Offensive/Defensive Rating 與聯盟排名 |
| 🎯 五成球隊戰況 | 對戰績五成以上/以下球隊的勝負紀錄 |
| 🔥 近期 10 場 | 近10場戰績、平均得分/失分 |
| 🔮 預測比分 | 「近10場平均」與「PACE調整法（攻守效率 x 兩隊整季官方PACE）」兩種預測得分，PACE 取自 NBA.com Advanced 數據的官方欄位，不是自行估算；主場那隊的預測分已加上主場優勢加成（回測驗證值 +1 分） |

---

## 5. 常見問題

- **第一次查詢要等很久**：程式會先呼叫 NBA.com API 取得整季進階數據，
  為了避免被限流，呼叫前會固定等待約 10 秒，這是正常現象，只有第一次需要等。
- **季後賽模式「整季戰績」顯示 0-0**：不會發生，不論例行賽或季後賽模式，「整季戰績」一律抓真實整季數據顯示（舊版曾有此bug，已修正）。
- **季後賽模式「近期戰況」是空的或 0**：正常現象，代表該隊尚未打季後賽（已淘汰或還沒開打），季後賽賽程資料本來就是空的。
- **某些數據顯示 N/A 或 0.0**：通常是該隊本賽季比賽場次太少（例如剛開季）或 ESPN API 暫時沒有資料，
  屬於正常的防呆顯示，不會讓程式中斷。
- **網路偶發錯誤**：NBA.com 數據抓取已內建自動重試機制，遇到限流會自動等待後重試最多 5 次。

---

## 6. CI/CD

`.github/workflows/ci.yml` 在每次 push 到 `main` 時自動執行：安裝套件 → 匯入檢查 `nba_matchup.py` 與 `api.py` → 建置並推送 Docker 映像檔到 `ghcr.io/kop030183/nba-matchup-predictor`。

兩支主要程式（CLI 邏輯 `nba_matchup.py`、API 服務 `api.py`）都有被 CI 的匯入檢查覆蓋到，語法錯誤或缺少套件會直接讓 CI 失敗，不會被忽略。
