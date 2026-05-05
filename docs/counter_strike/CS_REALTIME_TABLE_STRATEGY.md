# CS2 全表实时更新策略（实验版）

## 目标
- 覆盖 CS2 前端依赖的全部核心表，而不是只更新比赛主表。
- 以「过去 10 天 + 未来 90 天」作为比赛窗口。
- 支持循环运行（实时）+ 定时回补（纠偏）。

## 时间基准
- 当前基准：`T = now()`
- 比赛窗口：
  - 回看：`T-10d`
  - 前瞻：`T+90d`

---

## 一、比赛主链路（高频）

| 表名 | 频率 | 窗口 | 更新方式 | 说明 |
|---|---:|---|---|---|
| `match_schedule` | 30s | `T-10d ~ T+90d` | upsert | 赛程主表，未来比赛主要来源 |
| `match_result` | 30s | `T-10d ~ T` | upsert | 赛果主表，已完赛比分与状态 |
| `match_result_detail` | 1-2min | `T-10d ~ T` | upsert | 比赛详情主表（analysis/data/event_log 状态） |
| `match_result_player_stats` | 1-2min | `T-10d ~ T` | `delete by match_id + insert` | 整场选手数据 |
| `match_result_map_stats` | 1-2min | `T-10d ~ T` | `delete by match_id + insert` | 分图数据 |
| `match_result_map_player_stats` | 1-2min | `T-10d ~ T` | `delete by match_id + insert` | 分图选手数据 |

---

## 二、赛事与战队基础（中频）

| 表名 | 频率 | 窗口 | 更新方式 | 说明 |
|---|---:|---|---|---|
| `event_basic` | 5min | 活动赛事集合 | upsert | 赛事名、时间区间、logo |
| `team_basic` | 5min | 窗口内出现队伍 | upsert | 队伍基础档案与 logo |
| `team_player_relation` | 15min | 活跃战队 | upsert | 战队与选手关系，成员变更 |
| `team_rank_snapshot` | 30min | 全量或活跃队伍 | append snapshot / upsert latest | 战队排名 |
| `team_stat_snapshot` | 30min | 全量或活跃队伍 | append snapshot / upsert latest | 战队统计 |

---

## 三、选手详情链路（分层中低频）

### 3.1 高频（15-30min）
| 表名 | 频率 | 更新方式 | 说明 |
|---|---:|---|---|
| `player_basic` | 30min | upsert | 选手基础资料 |
| `player_stats_summary` | 30min | upsert | 聚合统计（给详情页卡片） |
| `player_teammates` | 30min | refresh/upsert | 队友关系 |
| `player_performance_metrics` | 30min | refresh/upsert | 能力指标 |
| `player_games` | 30min | upsert | 选手比赛维度统计 |

### 3.2 中频（1-2h）
| 表名 | 频率 | 更新方式 | 说明 |
|---|---:|---|---|
| `player_recent_matches` | 1h | refresh window | 近期比赛 |
| `player_rating_chart` | 1h | append/upsert | 评分走势 |
| `player_maps` | 2h | refresh window | 地图表现 |
| `player_equipment` | 2h | upsert | 外设信息 |
| `player_mouse_config` | 2h | upsert | 鼠标参数 |
| `player_monitor_config` | 2h | upsert | 显示器参数 |
| `player_milestones` | 2h | upsert | 生涯里程碑 |

### 3.3 低频（6-12h）
| 表名 | 频率 | 更新方式 | 说明 |
|---|---:|---|---|
| `player_history_honor` | 6h | append/upsert | 荣誉历史（变化慢） |

---

## 四、一致性规则（必须）

1. `match_result` 与 `match_schedule` 同步规则  
   - 若 `match_result.status=2`，则 `match_schedule.status` 必须向 2 收敛。  
   - 若详情已得出比分/分图，允许回填 `match_result.score1/score2/bout_details`。

2. 详情写入规则  
   - `match_result_*` 三张子表采用 `delete by match_id + insert`，避免脏行残留。

3. 防降级  
   - 新抓到 `TBD/空队名` 不覆盖已有有效队名。  
   - 新抓到空比分不覆盖已有非空比分。  

4. 失败重试  
   - 接口失败：指数退避（1s/2s/4s），单批最多 3 次。  
   - 本轮失败不清空旧数据，只记录 `last_error`。

---

## 五、执行顺序（每轮）

1. 主链路：`match_schedule` + `match_result`  
2. 比赛详情链路：`match_result_detail` + 3 张子表  
3. 一致性修复：状态/比分回填  
4. 中低频任务按调度器触发（team/player 各表）

---

## 六、验收指标（建议）

- 比赛窗口内：  
  - `match_result` 比分缺失率 < 5%  
  - `match_result_detail` 覆盖率 > 90%  
  - `match_result_map_stats` 覆盖率 > 85%  
  - `match_result_map_player_stats` 覆盖率 > 80%  
- 前端关键页面（首页/赛事/战队/选手/赛程/赛果）不出现长期空白。

