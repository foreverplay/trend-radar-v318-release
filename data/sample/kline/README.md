# A股全市场日线 / 分时分钟线 K 线

## 日线
```bash
cd /workspace/ashare-kline
source .venv/bin/activate
python scripts/download_all_daily.py          # 断点续传
python scripts/download_all_daily.py --force  # 强制重拉
python scripts/download_all_daily.py --hfq    # 同时后复权
python scripts/status.py                      # 进度
```

默认 6 workers + sleep，前复权写入 `raw/daily/{code}.parquet`。

## 分时 / 分钟线（新浪，约最多 ~1970 根/次，非全历史）
```bash
python scripts/download_all_minute.py              # 默认先 1m 再 5m
python scripts/download_all_minute.py --period 1   # 仅 1 分钟
python scripts/download_all_minute.py --period 5   # 仅 5 分钟
python scripts/status_minute.py                    # 进度
```

- `raw/minute_1m/{code}.parquet` / `raw/minute_5m/{code}.parquet`
- 状态：`meta/minute_download_state.json`
- 字段说明：`meta/schema_minute.md`（含历史长度限制）
- 建议 workers 4~6 + sleep，优先 qfq
