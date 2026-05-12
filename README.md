# Game League

多游戏电竞数据聚合平台，目前支持 **CS2**、**Valorant**、**LoL**。

## 功能

- 赛事一览、比赛详情、战队/选手排行榜
- CS2 回合级事件记录与可视化
- B 站官方赛事直播控制台（多路切换）
- B 站 CSGO官方赛事 视频回放自动匹配（含 BO3/BO5 分 P 选集）
- AI 助手（DeepSeek），可分析比赛回合数据
- 实时数据抓取（赛程、比分、详情、选手）

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Vue 3 + Vite |
| 后端 | FastAPI (Python) |
| 数据库 | MySQL |
| AI | DeepSeek API |

## 项目结构

```
Game_League/
├── fronted/                  # Vue 3 前端
│   └── src/
│       ├── App.vue           # 主布局（首页、赛事、战队、选手）
│       └── components/       # 赛事详情、比赛详情、AI 助手等组件
├── backend/
│   ├── api_server.py         # 统一入口（挂载各游戏路由）
│   ├── ai_assistant.py       # DeepSeek AI 助手
│   ├── .env                  # 环境变量（DB、API Key）
│   ├── counter_strike/       # CS2 模块
│   │   ├── cs_api_server.py  # CS2 API 服务
│   │   ├── run_cs_realtime_sync.py  # 实时数据抓取
│   │   └── scripts/
│   │       └── bilibili_video_sync.py  # B 站回放同步
│   ├── lol/                  # LoL 模块
│   └── valorant/             # Valorant 模块
└── .gitignore
```

## 快速启动

### 环境要求

- Python 3.11+
- Node.js 18+
- MySQL 8.0+

### 1. 配置环境变量

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入数据库连接信息
```

### 2. 安装后端依赖

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pymysql requests urllib3
```

### 3. 安装前端依赖

```bash
cd fronted
npm install
```

### 4. 启动

```bash
# 终端 1 — 启动 API 服务
cd backend
.venv/bin/python api_server.py

# 终端 2 — 启动实时数据抓取
cd backend
.venv/bin/python counter_strike/run_cs_realtime_sync.py

# 终端 3 — 启动前端开发服务器
cd fronted
npm run dev
```

访问 `http://localhost:5173`

### 5. 生产构建

```bash
cd fronted
npm run build
# 静态文件输出到 fronted/dist/
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `CS_DB_HOST` | MySQL 主机 |
| `CS_DB_PORT` | MySQL 端口 |
| `CS_DB_USER` | MySQL 用户 |
| `CS_DB_PASSWORD` | MySQL 密码 |
| `CS_DB_NAME` | 数据库名 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（AI 助手） |
| `CS_BILIBILI_OFFICIAL_VIDEO_INDEX` | B 站官方视频索引 JSON（可选，用于回放匹配） |

## License

MIT
