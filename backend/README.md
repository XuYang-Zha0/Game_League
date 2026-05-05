# Game League Backend

只需要启动一个统一后端服务：

```bash
cd /Users/dragonmaid/Downloads/Game_League/backend
.venv/bin/python api_server.py
```

或：

```bash
cd /Users/dragonmaid/Downloads/Game_League/backend
.venv/bin/uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
```

当前统一服务包含：

```text
/api/cs2/...
/api/lol/...
```

API 入口统一在 `backend/api_server.py`。各游戏的接口分别放在自己的模块里：

```text
backend/counter_strike/cs_api_server.py
backend/lol/lol_api_server.py
```

MySQL 统一使用 `backend/.env` 里的 `CS_DB_NAME=esports`。CS2、LoL、Valorant 数据都放在这个库里，用不同表名前缀区分，例如 `lol_*`、`valorant_*` 或 CS2 现有表。

后续 Valorant 也应该挂到同一个 FastAPI app 下，例如：

```text
/api/valorant/...
```

前端 `fronted/vite.config.js` 已经把 `/api` 代理到 `http://127.0.0.1:8000`，所以不用为每个游戏单独起后端。
