# Backend — 趋势分析雷达 V3.1.8

## 布局

- `trend_breakout/` — 核心 Python 算法（V2 扫描 + V3.1.8 引擎/发布）
- `api/` — FastAPI，读取 `data/sample` 四榜 JSON
- `scripts/` — 收盘流水线 / API / smoke

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `TREND_BREAKOUT_ROOT` | 算法包根目录 | `backend/trend_breakout` |
| `ASHARE_KLINE_ROOT` | K 线根（含 `raw/daily`） | `data/sample/kline` |
| `API_HOST` / `API_PORT` | API 监听 | `127.0.0.1:8765` |
| `ALLOW_PUBLISH` | 允许 API 触发重跑 | 关闭 |

## 启动 API

```bash
bash backend/scripts/run_api.sh
# GET http://127.0.0.1:8765/api/boards/latest
```

## 全量收盘（需完整 K 线）

```bash
export ASHARE_KLINE_ROOT=/path/to/ashare-kline
export TREND_BREAKOUT_ROOT=$(pwd)/backend/trend_breakout
bash backend/scripts/run_close_scan.sh
```

观察用途；禁止荐买 / 预测必涨。
