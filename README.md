# Game League

**Game League** 是一个面向电竞赛事的数据聚合与可视化平台，当前覆盖 **CS2**、**Valorant**、**LoL** 三个项目。项目将赛程、比分、比赛详情、战队/选手榜单、CS2 回合级事件、B 站官方直播入口、官方赛后回放和 AI 比赛分析整合到同一套 Web 应用中。

> 目标：让用户在一个页面里完成“看赛程、查赛果、看分图、读回合、找回放、问 AI”的完整观赛链路。

## 核心能力

### 多项目电竞数据中心

- CS2、Valorant、LoL 赛事数据聚合
- 赛事列表、赛事详情、比赛详情、战队榜、选手榜
- 首页情报流、热门赛事雷达、队伍与选手模块交互展示
- 正在进行的比赛优先展示，重要赛事与可直播赛事突出显示

### CS2 深度比赛详情

- BO1 / BO3 / BO5 比赛详情展示
- 分图战报、团队表现对比、选手数据表
- MVP、地图胜负、总比分、胜者等核心信息汇总
- 回合级事件可视化：击杀、助攻、下包、拆包、阵营切换、回合胜者
- 没有准确回合数据时不伪造内容，直接提示暂无回合记录

### B 站官方直播与回放

- S 级赛事支持 B 站直播控制台
- 多直播间切换：主舞台、服务台、主播二路等
- 赛事列表中区分：
  - `正在直播`：当前前后 1 小时窗口内存在未结束或即将开始的比赛
  - `有直播`：赛事支持直播入口，但当前没有临近比赛
- 赛后回放只匹配 B 站官方账号 **CSGO官方赛事**（UID：`474595627`）
- 自动匹配赛事、日期、双方战队与视频标题
- 支持 BO3 / BO5 分 P 选集，按地图切换对应回放页
- 实时同步脚本会定期拉取官方视频索引，并写入共享缓存供 API 服务加载

### AI 比赛助手

- 接入 DeepSeek API
- 支持基于比赛详情、选手数据、地图数据、回合事件进行分析
- 可回答关键击杀、回合走势、选手表现、队伍优劣势等问题
- 前端 AI 窗口支持拖拽、缩放与折叠

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3、Vite、JavaScript、CSS |
| 后端 | Python、FastAPI、PyMySQL |
| 数据库 | MySQL 8.0+ |
| AI | DeepSeek API |
| 数据同步 | Python 定时抓取脚本 |
| 第三方数据 | HLTV / Liquipedia / Bilibili 等公开赛事信息源 |

## 项目结构

```text
Game_League/
├── fronted/                         # Vue 3 前端项目
│   ├── src/
│   │   ├── App.vue                  # 主页面、首页、赛事/战队/选手入口
│   │   ├── style.css                # 全局样式
│   │   └── components/              # 赛事详情、比赛详情、AI 助手等组件
│   └── package.json
├── backend/
│   ├── api_server.py                # FastAPI 统一入口
│   ├── ai_assistant.py              # DeepSeek AI 助手接口
│   ├── counter_strike/              # CS2 数据与接口模块
│   │   ├── cs_api_server.py         # CS2 API、直播、回放、比赛详情
│   │   ├── run_cs_realtime_sync.py  # CS2 实时赛程/比分/回放同步
│   │   └── scripts/                 # CS2 数据处理脚本
│   ├── scripts/
│   │   └── bilibili_video_sync.py   # CSGO官方赛事回放索引同步脚本
│   ├── lol/                         # LoL 数据模块
│   ├── valorant/                    # Valorant 数据模块
│   └── .env.example                 # 环境变量示例
├── .gitignore
└── README.md
```

## 数据流说明

```text
赛事公开数据源 / 官方视频源
        │
        ▼
后端同步脚本
        │
        ├── 写入 MySQL：赛程、赛果、详情、选手、回合事件
        └── 写入 JSON 缓存：B 站官方回放视频索引
        │
        ▼
FastAPI 服务
        │
        ├── 提供赛事/比赛/战队/选手接口
        ├── 匹配 B 站直播与官方回放
        └── 汇总比赛上下文给 AI 助手
        │
        ▼
Vue 前端页面
```

## 快速启动

### 环境要求

- Python 3.11+
- Node.js 18+
- MySQL 8.0+

### 1. 克隆项目

```bash
git clone https://github.com/XuYang-Zha0/Game_League.git
cd Game_League
```

### 2. 配置后端环境变量

```bash
cp backend/.env.example backend/.env
```

然后编辑 `backend/.env`，填入本地数据库和 API Key 配置。

```env
CS_DB_HOST=127.0.0.1
CS_DB_PORT=3306
CS_DB_USER=root
CS_DB_PASSWORD=your_password
CS_DB_NAME=esports

DEEPSEEK_API_KEY=your_deepseek_api_key
```

可选配置：

```env
CS_BILIBILI_OFFICIAL_VIDEO_INDEX=[...]
```

`CS_BILIBILI_OFFICIAL_VIDEO_INDEX` 可用于手动提供 B 站官方回放索引；正常情况下，实时同步脚本会生成 `backend/scripts/bilibili_synced_index.json` 供后端自动加载。

### 3. 安装后端依赖

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pymysql requests urllib3
```

### 4. 安装前端依赖

```bash
cd ../fronted
npm install
```

### 5. 启动服务

打开三个终端分别运行：

```bash
# 终端 1：启动 API 服务
cd backend
.venv/bin/python api_server.py
```

```bash
# 终端 2：启动 CS2 实时数据与 B 站回放同步
cd backend
.venv/bin/python counter_strike/run_cs_realtime_sync.py
```

```bash
# 终端 3：启动前端开发服务器
cd fronted
npm run dev
```

前端默认访问地址：

```text
http://localhost:5173
```

## 常用命令

### 前端构建

```bash
cd fronted
npm run build
```

### 后端语法检查

```bash
python3 -m py_compile backend/api_server.py
python3 -m py_compile backend/counter_strike/cs_api_server.py
python3 -m py_compile backend/counter_strike/run_cs_realtime_sync.py
```

### 手动同步 B 站官方回放索引

```bash
cd backend
.venv/bin/python scripts/bilibili_video_sync.py --keywords "2026PGL阿斯塔纳" "2026IEM亚特兰大" --pretty
```

### 查看同步报告

```bash
cd backend
.venv/bin/python scripts/bilibili_video_sync.py --report
```

## 主要页面

| 页面 | 功能 |
|---|---|
| 首页 | 近期比赛、热门赛事、战队情报、明星选手、交互式模块 |
| 赛事一览 | 多项目赛事列表、S 级赛事、可直播赛事置顶 |
| 赛事详情 | 赛事赛程、已结束比赛、B 站直播控制台 |
| 比赛详情 | 分图战报、团队对比、选手数据、回合事件、官方回放 |
| 战队榜 | 战队排名、胜率、近期表现 |
| 选手榜 | 选手数据、评分、击杀、影响力指标 |
| AI 助手 | 基于当前比赛上下文进行问答分析 |

## B 站官方回放匹配规则

项目不会把非官方视频作为默认回放来源。CS2 回放匹配遵循以下原则：

1. 只接受官方账号 **CSGO官方赛事**（UID：`474595627`）的视频。
2. 使用赛事名、比赛日期、双方战队名进行匹配。
3. 拉取视频详情中的 `pages`，生成分 P 选集。
4. 前端根据地图顺序自动切换对应选集。
5. 实时脚本定期更新官方视频索引，API 服务按缓存文件热加载。

相关文件：

- `backend/scripts/bilibili_video_sync.py`
- `backend/counter_strike/run_cs_realtime_sync.py`
- `backend/counter_strike/cs_api_server.py`
- `fronted/src/components/MatchDetailPage.vue`

## 环境变量

| 变量 | 必填 | 说明 |
|---|---:|---|
| `CS_DB_HOST` | 是 | MySQL 主机 |
| `CS_DB_PORT` | 是 | MySQL 端口 |
| `CS_DB_USER` | 是 | MySQL 用户名 |
| `CS_DB_PASSWORD` | 是 | MySQL 密码 |
| `CS_DB_NAME` | 是 | CS2 数据库名 |
| `DEEPSEEK_API_KEY` | 否 | AI 助手使用的 DeepSeek API Key |
| `CS_BILIBILI_OFFICIAL_VIDEO_INDEX` | 否 | 手动注入的 B 站官方回放索引 JSON |

## 数据与安全说明

- `backend/.env` 不应提交到 Git。
- 数据库密码、AI Key、Cookie、Token 等敏感信息只应保存在本地环境变量中。
- `backend/scripts/bilibili_synced_index.json` 是运行时缓存文件，不需要提交。
- RIOT Developer API Key 相关示例代码已从当前项目中移除，LoL 模块保留项目自身的数据处理能力。

## 当前重点能力覆盖

- CS2 大型赛事实时赛程与比分同步
- PGL 阿斯塔纳、IEM 亚特兰大等 S 级赛事直播/回放链路
- 英雄亚冠 ACL 赛事直播入口支持
- CS2 回合级事件展示与 AI 可读上下文
- B 站官方回放自动索引、自动匹配、自动分 P

## License

MIT
