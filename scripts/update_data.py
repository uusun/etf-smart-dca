#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 半后端版数据生成器。

做什么：
1) 读取 data/ndx_history.csv 与 data/spx_history.csv 作为长期历史种子；
2) 通过 Twelve Data / Yahoo 后端接口补最近行情；
3) 根据定投规则生成 data/latest.json；
4) 前端 GitHub Pages 只读取 latest.json 展示每日建议。

注意：
- 这里生成的是“建议/计划账本”，不是券商真实成交账本。
- 场内候选金额默认参与预算估算，但不会假定一定成交；你可在 data/config.json 里把 budget_count_mode 改为 outside_only。
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_PATH = DATA / "config.json"
OUT_PATH = DATA / "latest.json"
TZ_BJ = timezone(timedelta(hours=8))

SYMBOLS = {
    "ndx": {
        "display": "纳斯达克100",
        "twelve_symbols": ["NDX", "NASDAQ:NDX", "QQQ"],
        "yahoo_symbols": ["^NDX", "QQQ"],
        "history": DATA / "ndx_history.csv",
    },
    "spx": {
        "display": "标普500",
        "twelve_symbols": ["SPX", "INDEX:SPX", "SPY"],
        "yahoo_symbols": ["^GSPC", "SPY"],
        "history": DATA / "spx_history.csv",
    },
}


def now_bj() -> str:
    return datetime.now(TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).replace(",", "").replace("%", "").strip()
        if not s or s.lower() in {"nan", "none", "null", "--"}:
            return None
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_history(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d = (r.get("date") or "").strip()[:10]
            close = safe_float(r.get("close"))
            pct = safe_float(r.get("pct"))
            if not d or close is None or pct is None:
                continue
            rows.append({
                "date": d,
                "open": safe_float(r.get("open")),
                "high": safe_float(r.get("high")),
                "low": safe_float(r.get("low")),
                "close": close,
                "pct": pct,
                "source": r.get("source") or "seed",
            })
    rows.sort(key=lambda x: x["date"])
    return dedupe_rows(rows)


def write_history(path: Path, rows: List[Dict[str, Any]]) -> None:
    rows = dedupe_rows(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "pct", "source"])
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in ["date", "open", "high", "low", "close", "pct", "source"]})


def dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # 同日期以后获取的数据优先覆盖种子数据。
    mp: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.get("date"):
            mp[r["date"]] = r
    out = list(mp.values())
    out.sort(key=lambda x: x["date"])
    return out


def compute_pcts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows.sort(key=lambda x: x["date"])
    prev_close: Optional[float] = None
    for r in rows:
        close = safe_float(r.get("close"))
        if close is None:
            continue
        if r.get("pct") is None and prev_close and prev_close > 0:
            r["pct"] = round((close / prev_close - 1) * 100, 4)
        if r.get("open") is None:
            r["open"] = prev_close if prev_close else close
        if r.get("high") is None:
            r["high"] = max(close, safe_float(r.get("open")) or close)
        if r.get("low") is None:
            r["low"] = min(close, safe_float(r.get("open")) or close)
        prev_close = close
    return [r for r in rows if r.get("pct") is not None]


def fetch_twelve_data(symbols: List[str], api_key: str) -> Tuple[List[Dict[str, Any]], str]:
    if not api_key:
        raise RuntimeError("TWELVE_DATA_API_KEY 未配置")
    errors = []
    for sym in symbols:
        try:
            url = "https://api.twelvedata.com/time_series"
            params = {"symbol": sym, "interval": "1day", "outputsize": "1000", "apikey": api_key, "format": "JSON"}
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "error" or data.get("code"):
                raise RuntimeError(str(data.get("message") or data)[:220])
            values = data.get("values") or []
            if not values:
                raise RuntimeError("空 values")
            rows = []
            for v in values:
                d = str(v.get("datetime") or "")[:10]
                close = safe_float(v.get("close"))
                if not d or close is None:
                    continue
                rows.append({
                    "date": d,
                    "open": safe_float(v.get("open")),
                    "high": safe_float(v.get("high")),
                    "low": safe_float(v.get("low")),
                    "close": close,
                    "pct": None,
                    "source": f"TwelveData:{sym}",
                })
            rows.sort(key=lambda x: x["date"])
            rows = compute_pcts(rows)
            if len(rows) >= 2:
                return rows, f"TwelveData:{sym}"
            raise RuntimeError("有效行不足")
        except Exception as e:
            errors.append(f"{sym}: {e}")
            time.sleep(0.6)
    raise RuntimeError("; ".join(errors))


def fetch_yahoo(symbols: List[str]) -> Tuple[List[Dict[str, Any]], str]:
    errors = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym, safe='')}"
            params = {"range": "2y", "interval": "1d", "includePrePost": "false", "events": "history"}
            r = requests.get(url, params=params, headers=headers, timeout=25)
            r.raise_for_status()
            data = r.json()
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                raise RuntimeError("无 result")
            ts = result.get("timestamp") or []
            quote_obj = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
            closes = quote_obj.get("close") or []
            opens = quote_obj.get("open") or []
            highs = quote_obj.get("high") or []
            lows = quote_obj.get("low") or []
            rows = []
            for i, t in enumerate(ts):
                close = safe_float(closes[i] if i < len(closes) else None)
                if close is None:
                    continue
                d = datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")
                rows.append({
                    "date": d,
                    "open": safe_float(opens[i] if i < len(opens) else None),
                    "high": safe_float(highs[i] if i < len(highs) else None),
                    "low": safe_float(lows[i] if i < len(lows) else None),
                    "close": close,
                    "pct": None,
                    "source": f"Yahoo:{sym}",
                })
            rows = compute_pcts(rows)
            if len(rows) >= 2:
                return rows, f"Yahoo:{sym}"
            raise RuntimeError("有效行不足")
        except Exception as e:
            errors.append(f"{sym}: {e}")
            time.sleep(0.6)
    raise RuntimeError("; ".join(errors))


def merge_recent(seed: List[Dict[str, Any]], recent: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not recent:
        return dedupe_rows(seed)
    combined = seed + recent
    combined = dedupe_rows(combined)
    combined = compute_pcts(combined)
    return combined


def cum_pct(rows: List[Dict[str, Any]]) -> float:
    v = 1.0
    for r in rows:
        v *= 1 + float(r["pct"]) / 100.0
    return (v - 1.0) * 100.0


def last_n(history: List[Dict[str, Any]], date_: str, n: int) -> List[Dict[str, Any]]:
    xs = [r for r in history if r["date"] <= date_]
    return xs[-n:]


def ndx_base(pct: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    r = cfg["rules"]["ndx"]
    if pct >= 1.5:
        return {"outside": r["up_big"], "inside": 0, "base_label": "大涨少投"}
    if pct <= -2:
        return {"outside": r["down_big_outside"], "inside": r["down_big_inside_candidate"], "base_label": "大跌：场外8000+场内候选4000"}
    if pct <= -1:
        return {"outside": r["down_medium"], "inside": 0, "base_label": "有效回调"}
    return {"outside": r["base"], "inside": 0, "base_label": "正常基准"}


def spx_base(pct: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    r = cfg["rules"]["spx"]
    if pct <= -3:
        return {"outside": r["outside_fixed"], "inside": r["inside_down_3"], "base_label": "标普极端大跌"}
    if pct <= -2:
        return {"outside": r["outside_fixed"], "inside": r["inside_down_2"], "base_label": "标普大跌"}
    if pct <= -1:
        return {"outside": r["outside_fixed"], "inside": r["inside_down_1"], "base_label": "标普有效回调"}
    return {"outside": r["outside_fixed"], "inside": 0, "base_label": "标普固定场外"}


def counted_amount(decision: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    mode = cfg.get("budget_count_mode", "recommended")
    if mode == "outside_only":
        return float(decision.get("outside", 0) or 0)
    return float(decision.get("outside", 0) or 0) + float(decision.get("inside", 0) or 0)


def rolling_sum(prev_decisions: List[Dict[str, Any]], days: int, cfg: Dict[str, Any]) -> float:
    xs = prev_decisions[-days:] if days > 0 else []
    return sum(float(d.get("counted_amount", counted_amount(d, cfg)) or 0) for d in xs)


def decide_one(symbol: str, history: List[Dict[str, Any]], idx: int, cfg: Dict[str, Any], prev_decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    row = history[idx]
    pct = float(row["pct"])
    date_ = row["date"]
    win5 = last_n(history[:idx + 1], date_, 5)
    win10 = last_n(history[:idx + 1], date_, 10)
    win20 = last_n(history[:idx + 1], date_, 20)
    cum5 = cum_pct(win5) if len(win5) >= 1 else 0.0
    cum10 = cum_pct(win10) if len(win10) >= 1 else 0.0
    cum20 = cum_pct(win20) if len(win20) >= 1 else 0.0
    count_le2_5 = sum(1 for r in win5 if float(r["pct"]) <= -2)
    no_le2_5 = count_le2_5 == 0
    mechanisms: List[str] = []

    if symbol == "ndx":
        rec = ndx_base(pct, cfg)
        r = cfg["rules"]["ndx"]
        if len(win5) >= 5 and cum5 <= r["yin_die_5d_threshold_pct"] and no_le2_5 and rec["outside"] + rec["inside"] < 8000:
            rec["outside"] = 8000
            rec["inside"] = 0
            mechanisms.append("阴跌补丁：5日累计≤-3%，且无单日≤-2%，提升至场外8000")
        if pct <= r["big_down_threshold_pct"] and count_le2_5 >= 3 and rec["inside"] > 0:
            rec["inside"] = 0
            mechanisms.append("大跌簇：5日内第3次≤-2%，暂停场内4000")
        for d_str, cap in r["budget_caps"].items():
            d = int(d_str)
            prev_sum = rolling_sum(prev_decisions, d - 1, cfg)
            if prev_sum + rec["outside"] + rec["inside"] > cap and rec["inside"] > 0:
                rec["inside"] = 0
                mechanisms.append(f"{d}日预算上限{cap}：优先削减场内候选")
        theory = "纳指固定5000基准四档；阴跌补丁补火力，大跌簇和滚动预算保弹药。"
    else:
        rec = spx_base(pct, cfg)
        r = cfg["rules"]["spx"]
        if len(win5) >= 5 and cum5 <= r["yin_die_5d_threshold_pct"] and no_le2_5 and rec["outside"] + rec["inside"] < r["yin_die_total_candidate"]:
            rec["inside"] = max(0, r["yin_die_total_candidate"] - rec["outside"])
            mechanisms.append("标普阴跌补丁：5日累计≤-3%，且无单日≤-2%，总候选提升至5000")
        if pct <= r["big_down_threshold_pct"] and count_le2_5 >= 3 and rec["outside"] + rec["inside"] > r["big_down_cluster_total_candidate"]:
            rec["inside"] = max(0, r["big_down_cluster_total_candidate"] - rec["outside"])
            mechanisms.append("标普大跌簇：5日内第3次≤-2%，总候选降至5000")
        for d_str, cap in r["budget_caps"].items():
            d = int(d_str)
            prev_sum = rolling_sum(prev_decisions, d - 1, cfg)
            if prev_sum + rec["outside"] + rec["inside"] > cap and rec["inside"] > 0:
                # 标普预算超限时，不把总候选压到0，而是压到温和补仓5000。
                rec["inside"] = max(0, r["yin_die_total_candidate"] - rec["outside"])
                mechanisms.append(f"标普{d}日预算上限{cap}：削减大额场内候选")
        theory = "标普三机制轻量化：场外2000固定，压力机制主要管理场内候选金额。"

    decision = {
        "date": date_,
        "symbol": symbol,
        "close": round(float(row["close"]), 4),
        "pct": round(pct, 4),
        "outside": float(rec["outside"]),
        "inside": float(rec["inside"]),
        "total_candidate": float(rec["outside"] + rec["inside"]),
        "counted_amount": 0.0,  # below
        "base_label": rec.get("base_label", ""),
        "mechanisms": mechanisms,
        "windows": {
            "cum5": round(cum5, 4),
            "cum10": round(cum10, 4),
            "cum20": round(cum20, 4),
            "count_le2_5": count_le2_5,
            "days5": len(win5),
            "days10": len(win10),
            "days20": len(win20),
        },
        "rolling_before": {
            "5": round(rolling_sum(prev_decisions, 4, cfg), 2),
            "10": round(rolling_sum(prev_decisions, 9, cfg), 2),
            "20": round(rolling_sum(prev_decisions, 19, cfg), 2),
        },
        "theory": theory,
    }
    decision["counted_amount"] = counted_amount(decision, cfg)
    return decision


def build_plan_decisions(symbol: str, history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    构建“计划账本”：只从 plan_start_date 之后开始计入仓位进度和预算。
    这个列表用于 progress 计算；不是页面里的近80日展示列表。
    """
    start = cfg.get("plan_start_date", "2026-05-20")
    decisions: List[Dict[str, Any]] = []
    for idx, row in enumerate(history):
        if row["date"] < start:
            continue
        d = decide_one(symbol, history, idx, cfg, decisions)
        d["counted_in_plan"] = True
        decisions.append(d)
    # 如果计划开始日晚于历史最后一天，也仍然给出最后一天观察建议，但不计入计划进度。
    if not decisions and history:
        idx = len(history) - 1
        d = decide_one(symbol, history, idx, cfg, [])
        d["pre_plan_observation"] = True
        d["counted_in_plan"] = False
        return [d]
    return decisions


def build_display_decisions(symbol: str, history: List[Dict[str, Any]], cfg: Dict[str, Any], n: int = 80) -> List[Dict[str, Any]]:
    """
    构建页面展示用的“最近N个交易日建议记录”。

    v2 的问题是 recent_decisions 直接取自计划账本；如果 plan_start_date 是今天，
    页面就只能看到今天一条。这里改为：无论计划从哪天开始，都回溯计算最近N个
    交易日的策略建议，并用 counted_in_plan 标记是否计入仓位进度。

    为了让滚动预算在展示区间开头也有足够上下文，实际会从最近 max(n+40, 140)
    个交易日开始预热，最后只返回最近N个。
    """
    if not history:
        return []
    start = cfg.get("plan_start_date", "2026-05-20")
    warm_n = max(n + 40, 140)
    start_idx = max(0, len(history) - warm_n)
    decisions: List[Dict[str, Any]] = []
    for idx in range(start_idx, len(history)):
        row = history[idx]
        d = decide_one(symbol, history, idx, cfg, decisions)
        d["counted_in_plan"] = row["date"] >= start
        if not d["counted_in_plan"]:
            d["display_only"] = True
        decisions.append(d)
    return decisions[-n:]


def progress(symbol: str, decisions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    t = cfg["targets"][symbol]
    start_cost = float(t["current_cost_at_start"])
    target_cost = float(t["target_cost"])
    plan_items = [d for d in decisions if d.get("counted_in_plan") is not False and not d.get("pre_plan_observation")]
    counted = sum(float(d.get("counted_amount", 0) or 0) for d in plan_items)
    current_plan_cost = start_cost + counted
    remaining = max(0.0, target_cost - current_plan_cost)
    recent = plan_items[-20:]
    avg20 = sum(float(d.get("counted_amount", 0) or 0) for d in recent) / len(recent) if recent else 0.0
    return {
        "start_cost": start_cost,
        "target_cost": target_cost,
        "planned_counted_since_start": round(counted, 2),
        "current_plan_cost": round(current_plan_cost, 2),
        "remaining": round(remaining, 2),
        "progress_pct": round((current_plan_cost / target_cost * 100) if target_cost else 0, 2),
        "avg20_counted": round(avg20, 2),
        "estimated_trading_days_left": math.ceil(remaining / avg20) if avg20 > 0 else None,
    }


def update_symbol(symbol: str, cfg: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    info = SYMBOLS[symbol]
    seed = read_history(info["history"])
    attempts = []
    recent: List[Dict[str, Any]] = []
    source = "seed"
    try:
        recent, source = fetch_twelve_data(info["twelve_symbols"], api_key)
        attempts.append({"source": source, "ok": True})
    except Exception as e:
        attempts.append({"source": "TwelveData", "ok": False, "error": str(e)[:400]})
        try:
            recent, source = fetch_yahoo(info["yahoo_symbols"])
            attempts.append({"source": source, "ok": True})
        except Exception as e2:
            attempts.append({"source": "Yahoo", "ok": False, "error": str(e2)[:400]})
            recent = []
            source = "seed"
    history = merge_recent(seed, recent)
    # 为了页面加载速度，只写入最近几年合并历史；源 CSV 不回写，避免 Actions artifact 与仓库状态混淆。
    plan_decisions = build_plan_decisions(symbol, history, cfg)
    display_decisions = build_display_decisions(symbol, history, cfg, n=80)
    latest = plan_decisions[-1] if plan_decisions else (display_decisions[-1] if display_decisions else None)
    return {
        "symbol": symbol,
        "name": info["display"],
        "data_source": source,
        "attempts": attempts,
        "history_last_date": history[-1]["date"] if history else None,
        "latest": latest,
        "recent_decisions": display_decisions,
        "plan_decisions_count": len([d for d in plan_decisions if d.get("counted_in_plan") is not False and not d.get("pre_plan_observation")]),
        "progress": progress(symbol, plan_decisions, cfg),
    }


def main() -> int:
    cfg = load_json(CONFIG_PATH, {})
    if not cfg:
        print("ERROR: data/config.json missing", file=sys.stderr)
        return 2
    api_key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    result = {
        "generated_at_beijing": now_bj(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "config": cfg,
        "note": "本文件由 GitHub Actions 自动生成。金额为策略建议/计划账本，不等同券商真实成交。",
        "symbols": {},
    }
    for symbol in ["ndx", "spx"]:
        result["symbols"][symbol] = update_symbol(symbol, cfg, api_key)
    save_json(OUT_PATH, result)
    print(f"Wrote {OUT_PATH}")
    for s, obj in result["symbols"].items():
        print(s, obj["history_last_date"], obj["data_source"], obj["latest"]["pct"], obj["latest"]["outside"], obj["latest"]["inside"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
