# LoL 电竞赛事数据流水线

这个目录现在分成两类脚本：

- `lol_esports_gol.py`：抓职业赛事数据，输出 CSV 到 `lol_data/`。
- `import_lol_to_mysql.py`：把 `lol_data/*.csv` 导入 MySQL。
- `run_lol_pipeline.py`：先抓 CSV，再导入 MySQL。
- `fetch_lol_sample.py` / `riot_client.py`：保留给 Riot 个人账号/排位 API 测试，不作为赛事页面主数据源。

## 1. 数据源

当前职业赛事数据源组合使用：

```text
https://lolesports.com/
https://gol.gg/
```

LoL Esports 接口用于拉 Worlds、MSI、LCK、LPL、LEC 等大范围赛程、比分、未来几个月赛程和队伍 Logo。Games of Legends 用于补充详细单局/选手 KDA、CS 数据，并补齐 LoL Esports schedule 翻页不到的 2023 年初赛区比赛。

原因是 Riot Developer Portal 的 `RGAPI-...` key 主要用于玩家账号、排位、个人对局，并不直接提供 Worlds、MSI、LPL、LCK 这类职业赛事的完整比赛数据库。

## 2. 输出 CSV

运行后会生成：

```text
backend/lol/lol_data/lol_event_basic.csv
backend/lol/lol_data/lol_team_basic.csv
backend/lol/lol_data/lol_player_basic.csv
backend/lol/lol_data/lol_match_result.csv
backend/lol/lol_data/lol_game_basic.csv
backend/lol/lol_data/lol_game_player_stats.csv
```

字段含义：

- `lol_event_basic`：赛事，例如 Worlds 2025 Main Event。
- `lol_match_result`：BO 级别比赛，例如 T1 vs KT Rolster，比分 3-2。
- `lol_game_basic`：单局游戏，例如 BO5 里的 Game 1、Game 2。
- `lol_game_player_stats`：每局 10 名选手的英雄、KDA、CS。
- `lol_team_basic`：队伍基础信息。
- `lol_player_basic`：选手基础信息。

## 3. MySQL 配置

默认复用 `backend/.env` 里的数据库配置，并把 LoL、CS2、Valorant 表都放进同一个 `esports` 库里。LoL 表名统一使用 `lol_` 前缀：

```env
CS_DB_HOST=127.0.0.1
CS_DB_PORT=3306
CS_DB_USER=root
CS_DB_PASSWORD=你的密码
CS_DB_NAME=esports
```

`import_lol_to_mysql.py` 不再读取 `LOL_DB_NAME`，避免把 LoL 导入到独立数据库。

## 4. 运行

默认抓：

```text
LoL Esports: worlds,msi,first_stand,lck,lpl,lec,lcp,lcs,cblol-brazil,vcs,pcs,lla
GOL 详细数据: Worlds 2025 Main Event 前 20 个 BO
GOL 2023 补源: LCK/LPL/LEC/LCS/CBLOL/PCS/VCS/LLA 春季或冬季赛区比赛
```

默认时间范围从 `2023-01-01` 开始，并抓取未来 4 个月赛程。运行：

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/lol/run_lol_pipeline.py
```

抓指定赛事：

```bash
LOL_TOURNAMENTS="Worlds 2025 Main Event,MSI 2025" backend/.venv/bin/python backend/lol/run_lol_pipeline.py
```

抓全量赛事页：

```bash
LOL_MATCH_LIMIT=0 backend/.venv/bin/python backend/lol/run_lol_pipeline.py
```

调整赛程范围：

```bash
LOL_SCHEDULE_START_DATE=2023-01-01 LOL_SCHEDULE_FUTURE_MONTHS=4 backend/.venv/bin/python backend/lol/run_lol_pipeline.py
```

调整 2023 年初补源赛事：

```bash
LOL_GOL_BACKFILL_TOURNAMENTS="LCK Spring 2023,LPL Spring 2023" backend/.venv/bin/python backend/lol/run_lol_pipeline.py
```

## 5. 前端数据方向

`lol_data/*.csv` 只是中间文件。前端页面后续应该调用后端 API，由后端读 MySQL 的 `lol_*` 表返回数据，不直接读取 CSV。

## 6. 实时增量同步

全量导入完成后，用实时脚本持续更新近期完赛、单局选手数据、未来赛程和官方队伍名单。这个脚本不会 truncate 历史表，而是按主键/唯一键 upsert 到 MySQL。

单次运行：

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/lol/run_lol_realtime_sync.py --once
```

持续运行：

```bash
backend/.venv/bin/python backend/lol/run_lol_realtime_sync.py --interval-seconds 300
```

常用参数：

```bash
# 只同步最近 14 天，限制每轮最多补 120 个 game 的选手 live stats
backend/.venv/bin/python backend/lol/run_lol_realtime_sync.py --lookback-days 14 --livestats-limit 120 --once

# 指定赛区
backend/.venv/bin/python backend/lol/run_lol_realtime_sync.py --leagues "lck,lpl,lec,lcp,lcs" --once
```
