# Valorant 数据实验链路

当前 Valorant 数据源是本地 VLR 实验链路，暂时不代表最终长期数据源。

## 1. 抓取少量实验 CSV

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/valorant/vlr_experiment.py
```

采集脚本支持三档 preset：

```bash
# 快速冒烟：少量样本
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset quick

# 日常回填：较大批次，适合更新页面数据
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset daily

# 完整回填：赛果默认按日期回抓到 2023-01-01，0 表示详情/profile 不限制
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset full
```

`full` 预设会持续翻 VLR `/matches/results`，直到最早赛果日期到达 `2023-01-01`。如果只想改起点或加页数安全上限，可以这样跑：

```bash
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset full --result-start-date 2023-01-01 --max-result-pages 1000
```

`full` 预设默认会用较快参数：`detail_workers=12`、`profile_workers=6`、`sleep_seconds=0.12`。如果只想尽快补比赛、地图、选手数据，暂时不抓 VLR 个人主页头像，可以这样更快跑：

```bash
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset full --skip-player-profiles
```

更激进的最快模式是 `turbo`，它仍然回抓到 `2023-01-01`，但默认跳过 VLR 个人主页 profile：

```bash
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset turbo
```

如果 VLR 连接超时、SSL 断开或疑似限速，脚本会自动重试。仍然频繁失败时，用下面这个更稳的 turbo：

```bash
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset turbo --detail-workers 8 --sleep-seconds 0.15 --retry-attempts 5 --timeout-seconds 45
```

实时同步脚本不会使用这个全量日期回抓逻辑；它仍然通过 `--result-pages` 控制轻量抓取。

输出目录：

```text
backend/valorant/vlr_data/
```

脚本默认不再生成 HTML 缓存目录，因此不会创建 `backend/valorant/vlr_cache/`。如果临时需要缓存 VLR 页面，可以显式传入：

```bash
backend/.venv/bin/python backend/valorant/vlr_experiment.py --preset daily --cache-dir backend/valorant/vlr_cache
```

## 2. 导入 MySQL

导入脚本会创建并写入 `backend/.env` 中 `CS_DB_NAME=esports` 指向的数据库。

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/valorant/import_valorant_to_mysql.py
```

主要表：

```text
valorant_event_basic
valorant_team_basic
valorant_player_basic
valorant_team_player_relation
valorant_match_schedule
valorant_match_result
valorant_match_detail
valorant_match_map_stats
valorant_match_player_stats
valorant_player_stats_summary
valorant_player_agent_stats
valorant_team_rank_snapshot
```

## 3. 使用 Liquipedia 补选手头像

VLR 没有选手头像时，可以在导入 MySQL 后用 Liquipedia 做备用来源。脚本只按 Liquipedia 选手页里的 `vlr=` 字段精确匹配 VLR player_id，默认不会覆盖已有头像，也不会生成 `vlr_cache`。

建议先设置一个带联系信息的 User-Agent：

```bash
export LIQUIPEDIA_USER_AGENT="GameLeagueValorantAvatarBot/0.1 (your-contact@example.com)"
```

试跑单个选手：

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/valorant/enrich_liquipedia_avatars.py --player-id 37990 --dry-run
```

补全所有缺失头像：

```bash
backend/.venv/bin/python backend/valorant/enrich_liquipedia_avatars.py
```

如果想重新检查之前已经标记为 Liquipedia 缺失的选手：

```bash
backend/.venv/bin/python backend/valorant/enrich_liquipedia_avatars.py --retry-checked
```

## 4. 使用 Konect 补高排名缺失头像

部分新选手或二级联赛选手在 Liquipedia 没有 `vlr=` 精确字段，Konect 上可能有选手自建资料和头像。Konect 容易出现普通用户重名，所以脚本默认只预览、不写库，并使用较高置信阈值：

```bash
backend/.venv/bin/python backend/valorant/enrich_konect_avatars.py --top-missing 120
```

确认预览结果没问题后再写入 MySQL：

```bash
backend/.venv/bin/python backend/valorant/enrich_konect_avatars.py --top-missing 120 --apply
```

如果想扩大匹配范围，可以降低阈值，但误匹配风险会增加：

```bash
backend/.venv/bin/python backend/valorant/enrich_konect_avatars.py --top-missing 120 --min-score 85
```

## 5. 后端 API

统一后端启动后会暴露：

```text
/api/valorant/dataset
/api/valorant/matches
/api/valorant/match/{match_id}
/api/valorant/team/{team_key}
/api/valorant/player/{player_id}
```

## 6. 实时增量同步

全量导入完成后，用实时脚本持续更新近期 VLR 完赛、比赛详情、选手统计、选手 profile 和由近期比赛推导出的队伍成员关系。这个脚本不会 truncate 历史表，也不会生成 `vlr_cache`。

单次运行：

```bash
cd /Users/dragonmaid/Downloads/Game_League
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --once
```

持续运行：

```bash
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --interval-seconds 300
```

实时脚本默认是快循环：

```text
每轮：1 页赛程 + 2 页赛果 + 35 个比赛详情
默认跳过 player profile
VLR /stats：最多每 30 分钟刷新一次
player profile：最多每 6 小时刷新一次
详情页：默认 4 worker 受控并发
```

常用参数：

```bash
# 减小每轮抓取量
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --once --result-pages 3 --detail-limit 60 --player-profile-limit 120

# 极快轮询，只更新列表和少量近期详情
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --once --disable-stats-refresh --detail-limit 12

# 手动低频刷新选手 profile
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --once --force-profile-refresh --player-profile-limit 80

# 偶尔为新增缺失头像跑 Liquipedia 备用补全
backend/.venv/bin/python backend/valorant/run_valorant_realtime_sync.py --once --enable-liquipedia-avatars --liquipedia-limit 50
```
