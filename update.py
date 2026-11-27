#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数缠论看板（上证指数 / 创业板指 / 科创50）
每天收盘后抓日线 + 30 分钟，按"包含处理 → 分型 → 笔 → 笔中枢"机械划分，
给出：位置 / 三买三卖（只认第一次回试）/ 同向笔力度对比（背驰近似）/ 关键位 / 条件表，
并把《教你炒股票》第 16、20 课的退出规则内置成风控块，另附走查回测。
依赖：标准库 + pillow（仅画图）。
"""
import json
import os
import sys
import urllib.request
from statistics import median
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(BASE, "site")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer": "https://finance.sina.com.cn/"}

INDICES = [("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000688", "科创50")]
FAST = os.environ.get("CHAN_FAST") == "1"


# ---------------- 数据 ----------------
def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_sina(symbol, scale, datalen):
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
           "?symbol=%s&scale=%d&ma=no&datalen=%d" % (symbol, scale, datalen))
    txt = _get(url)
    for k in ("day", "open", "high", "low", "close", "volume"):
        txt = txt.replace("%s:" % k, '"%s":' % k)
    data = json.loads(txt)
    return [{"date": d["day"], "o": float(d["open"]), "h": float(d["high"]),
             "l": float(d["low"]), "c": float(d["close"]),
             "v": float(d.get("volume") or 0)} for d in data]


def fetch_tencent_min(symbol, period, count):
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=%s,%s,,%d" % (symbol, period, count)
    raw = json.loads(_get(url))
    arr = (raw.get("data", {}).get(symbol) or {}).get(period) or []
    out = []
    for r in arr:
        t = str(r[0])
        if len(t) < 12:
            continue
        out.append({"date": "%s-%s-%s %s:%s:00" % (t[0:4], t[4:6], t[6:8], t[8:10], t[10:12]),
                    "o": float(r[1]), "c": float(r[2]), "h": float(r[3]), "l": float(r[4]),
                    "v": float(r[5]) if len(r) > 5 else 0.0})
    return out


def fetch_tencent_day(symbol, count):
    url = ("https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (symbol, count))
    raw = json.loads(_get(url))
    d = raw["data"][symbol]
    arr = d.get("day") or d.get("qfqday")
    return [{"date": r[0], "o": float(r[1]), "c": float(r[2]), "h": float(r[3]), "l": float(r[4]),
             "v": float(r[5]) if len(r) > 5 else 0.0} for r in arr]


def get_daily(symbol):
    for fn, args in ((fetch_sina, (symbol, 240, 3000)), (fetch_tencent_day, (symbol, 1200))):
        try:
            bars = fn(*args)
            if len(bars) > 200:
                return bars
        except Exception as e:
            print("fetch fail:", symbol, fn.__name__, type(e).__name__, str(e)[:100], file=sys.stderr)
    return []


def get_30m(symbol):
    for fn, args in ((fetch_sina, (symbol, 30, 1800)), (fetch_tencent_min, (symbol, "m30", 1200))):
        try:
            bars = fn(*args)
            if len(bars) > 200:
                return bars
        except Exception as e:
            print("fetch fail 30m:", symbol, fn.__name__, type(e).__name__, str(e)[:100], file=sys.stderr)
    return []


def get_5m(symbol):
    for fn, args in ((fetch_sina, (symbol, 5, 2000)), (fetch_tencent_min, (symbol, "m5", 1200))):
        try:
            bars = fn(*args)
            if len(bars) > 200:
                return bars
        except Exception as e:
            print("fetch fail 5m:", symbol, fn.__name__, type(e).__name__, str(e)[:100], file=sys.stderr)
    return []


def to_monthly(daily):
    groups, order = {}, []
    for b in daily:
        ym = b["date"][:7]
        if ym not in groups:
            groups[ym] = {"date": ym, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
            order.append(ym)
        else:
            g = groups[ym]
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
    return [groups[k] for k in order]


# ---------------- 缠论引擎 ----------------
def merge_inclusion(bars):
    merged = []
    for i, b in enumerate(bars):
        nb = {"h": b["h"], "l": b["l"], "date": b["date"], "src": [i]}
        if not merged:
            merged.append(nb)
            continue
        last = merged[-1]
        inc = (nb["h"] >= last["h"] and nb["l"] <= last["l"]) or (nb["h"] <= last["h"] and nb["l"] >= last["l"])
        if not inc:
            merged.append(nb)
            continue
        up = (last["h"] > merged[-2]["h"]) if len(merged) >= 2 else (nb["h"] > last["h"])
        if up:
            last["h"], last["l"] = max(last["h"], nb["h"]), max(last["l"], nb["l"])
        else:
            last["h"], last["l"] = min(last["h"], nb["h"]), min(last["l"], nb["l"])
        last["src"] += nb["src"]
    return merged


def find_fx(merged):
    out = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"] and b["l"] > a["l"] and b["l"] > c["l"]:
            out.append({"mi": i, "type": "top", "price": b["h"]})
        elif b["h"] < a["h"] and b["h"] < c["h"] and b["l"] < a["l"] and b["l"] < c["l"]:
            out.append({"mi": i, "type": "bottom", "price": b["l"]})
    return out


def attach_extreme(bars, merged, fx):
    for f in fx:
        src = merged[f["mi"]]["src"]
        if f["type"] == "top":
            k = max(src, key=lambda i: bars[i]["h"])
            f["price"] = bars[k]["h"]
        else:
            k = min(src, key=lambda i: bars[i]["l"])
            f["price"] = bars[k]["l"]
        f["oi"], f["date"] = k, bars[k]["date"]
    return fx


def build_bi(fx, min_gap=4):
    bis, last = [], None
    for f in fx:
        if last is None:
            last = f
            continue
        if f["type"] == last["type"]:
            better = f["price"] > last["price"] if f["type"] == "top" else f["price"] < last["price"]
            if better:
                if bis:
                    b = bis[-1]
                    b["e"] = f
                    b["high"] = max(b["s"]["price"], f["price"])
                    b["low"] = min(b["s"]["price"], f["price"])
                last = f
            continue
        if f["mi"] - last["mi"] >= min_gap:
            bis.append({"s": last, "e": f, "dir": "up" if f["type"] == "top" else "down",
                        "high": max(last["price"], f["price"]), "low": min(last["price"], f["price"])})
            last = f
    return bis


def build_zs(bis, mode="range", max_bi=None):
    """笔中枢：连续三笔的价格重叠区 [max(low), min(high)]。

    延伸判据两种（2026-11-24 分级别启用）：
    - mode="range"：后续笔的**整笔区间**与 [zd, zg] 有交集即延伸。
      这是《第20课》"任意 [dn,gn] 与 [ZD,ZG] 有重叠"的字面口径，
      用在 30min / 5min——笔相对中枢够短，笔≈次级别走势类型的近似成立。
    - mode="endpoint"：后续笔的**端点**回到 [zd, zg] 才延伸（保留一次越界回归）。
      用在日线 / 月线：级别越高一笔越长，整笔区间会"贯穿"中枢造成无限延伸
      （实测月线上证会把上沿从 3419 撑到 3709，直接把大级别闸门卡死）。
    max_bi 是防跑飞的安全上限，0/None 表示不截断。
    """
    zs, i = [], 0
    while i + 2 < len(bis):
        three = bis[i:i + 3]
        zd = max(b["low"] for b in three)
        zg = min(b["high"] for b in three)
        if zd >= zg:
            i += 1
            continue
        j = i + 2
        while j + 1 < len(bis):
            if max_bi and (j + 1 - i + 1) > max_bi:
                break
            nb = bis[j + 1]
            if mode == "endpoint":
                if zd <= nb["e"]["price"] <= zg:
                    j += 1
                    continue
                if (j + 2 < len(bis) and (not max_bi or (j + 2 - i + 1) <= max_bi)
                        and zd <= bis[j + 2]["e"]["price"] <= zg):
                    j += 2
                    continue
                break
            if nb["high"] >= zd and nb["low"] <= zg:
                j += 1
                continue
            break
        zs.append({"zd": zd, "zg": zg, "start": bis[i]["s"]["date"], "end": bis[j]["e"]["date"],
                   "start_oi": bis[i]["s"]["oi"], "end_oi": bis[j]["e"]["oi"], "nbi": j - i + 1})
        i = j + 1
    return zs


# ---------------- 结构补充（2026-11-24 新增） ----------------
def gaps(bars, min_pts=1.0, keep=6):
    """日线跳空缺口（前收 vs 今开），只返回**未回补**的部分，最近的在前。"""
    out = []
    for i in range(1, len(bars)):
        pc, op = bars[i - 1]["c"], bars[i]["o"]
        d = op - pc
        if abs(d) < min_pts:
            continue
        lo, hi = (pc, op) if d > 0 else (op, pc)
        after = bars[i:]
        mx = max(b["h"] for b in after)
        mn = min(b["l"] for b in after)
        if d > 0:
            # 向上跳空：缺口区 [pc, op]，靠下跌回补；已跌到的部分算补掉，剩 [pc, mn]
            if mn <= lo:
                continue
            hi = min(hi, mn)
        else:
            # 向下跳空：缺口区 [op, pc]，靠上涨回补；已涨到的部分算补掉，剩 [mx, pc]
            if mx >= hi:
                continue
            lo = max(lo, mx)
        if lo >= hi:
            continue
        out.append({"date": bars[i]["date"][:10], "lo": round(lo, 2), "hi": round(hi, 2),
                    "dir": "up" if d > 0 else "down", "pts": round(hi - lo, 2)})
    return out[-keep:][::-1]


def vol_state(bars, n=5):
    """量价配合四象限。指数是加权合成，不设「缩量=好」的单边门槛。"""
    if len(bars) < n + 2:
        return None
    v = [float(b.get("v") or 0) for b in bars]
    if v[-1] <= 0:
        return None
    base = sum(v[-1 - n:-1]) / n
    if base <= 0:
        return None
    ratio = v[-1] / base
    chg = (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
    if chg >= 0 and ratio >= 1.15:
        q, txt, cls = "涨放量", "主动买盘进场，健康", "p-green"
    elif chg >= 0 and ratio < 0.85:
        q, txt, cls = "涨缩量", "无量反抽，持续性弱", "p-amber"
    elif chg < 0 and ratio < 0.85:
        q, txt, cls = "跌缩量", "卖压衰竭，不是坏事", "p-amber"
    elif chg < 0 and ratio >= 1.15:
        q, txt, cls = "跌放量", "出清与破位加速难分，看次日", "p-red"
    else:
        q, txt, cls = "量价平稳", "无明显量能信号", "p-grey"
    return {"ratio": round(ratio, 2), "chg": round(chg, 2), "quad": q, "note": txt, "cls": cls}


def freshness(bars):
    """数据新鲜度：最后一根日线是否就是最近一个交易日（15:35 那次跑常拿到 T-1）。"""
    last = bars[-1]["date"][:10]
    now = datetime.now(timezone(timedelta(hours=8)))
    d = now if now.hour >= 15 else now - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    want = d.strftime("%Y-%m-%d")
    lag = 0
    if last != want:
        try:
            a = datetime.strptime(last, "%Y-%m-%d")
            b = datetime.strptime(want, "%Y-%m-%d")
            lag = sum(1 for k in range(max(0, (b - a).days))
                      if (a + timedelta(days=k)).weekday() < 5)
        except Exception:
            lag = 1
    return {"last": last, "want": want, "lag": lag, "ok": lag == 0}


def zs_stability(bars, k=3, mode="endpoint", max_bi=9):
    """中枢稳定性自检：用 T-1..T-k 的数据重算最后中枢，看上下沿漂多少。"""
    if len(bars) < 400:
        return None
    base = analyze(bars, mode=mode, max_bi=max_bi)["zs"]
    if not base:
        return None
    z0 = base[-1]
    rows = []
    for back in range(1, k + 1):
        zs = analyze(bars[:-back], mode=mode, max_bi=max_bi)["zs"]
        if not zs:
            continue
        z = zs[-1]
        if z["start"][:10] != z0["start"][:10]:
            rows.append({"back": back, "new": True, "dzd": None, "dzg": None, "start": z["start"][:10]})
        else:
            rows.append({"back": back, "new": False, "dzd": round(z["zd"] - z0["zd"], 2),
                         "dzg": round(z["zg"] - z0["zg"], 2), "start": z["start"][:10]})
    drift = max([max(abs(r["dzd"] or 0), abs(r["dzg"] or 0)) for r in rows] or [0])
    return {"rows": rows, "drift": round(drift, 2), "unstable": drift > 5,
            "zd": round(z0["zd"], 2), "zg": round(z0["zg"], 2),
            "end": z0["end"][:10], "bars_since_end": max(0, len(bars) - 1 - z0["end_oi"])}


def level_ladder(level, m30, m5):
    """级别梯：日线 / 30min / 5min 各自最后一个中枢 + 现价相对位置。"""
    close = level["bars"][-1]["c"]
    rows = []
    for nm, lv in (("日线", level), ("30min", m30), ("5min", m5)):
        if not lv or not lv.get("zs"):
            rows.append({"name": nm, "ok": False})
            continue
        z = lv["zs"][-1]
        if close > z["zg"]:
            pos, dist = "中枢上方", (z["zg"] / close - 1) * 100
        elif close < z["zd"]:
            pos, dist = "中枢下方", (z["zd"] / close - 1) * 100
        else:
            pos, dist = "中枢内", 0.0
        rows.append({"name": nm, "ok": True, "zd": round(z["zd"], 2), "zg": round(z["zg"], 2),
                     "pos": pos, "dist": round(dist, 2), "nbi": z.get("nbi"),
                     "start": z["start"][:10], "end": z["end"][:10]})
    return {"close": round(close, 2), "rows": rows}


def upgrade_state(m5, m30):
    """级别升级状态机：5min 向上笔 → 5min 向上段 → 回踩不破 → 30min 中枢。"""
    if not m5 or not m5.get("bars") or not m5.get("bis"):
        return None
    bars, bis = m5["bars"], m5["bis"]
    close = bars[-1]["c"]
    # 起点 = 最近一个"创新低"的向下笔的终点（不是全窗口最低，否则会拿半个月前的低点当现在的起点）
    # 2026-11-27：改用 legs() 的向下段（含末端延伸），并排除"未成笔"的合成段，
    # 与日线/30min 用同一套笔端口径。
    dsegs = [s for s in legs(m5, "down") if not s.get("syn")]
    if not dsegs:
        return None
    start = dsegs[-1]
    for i in range(len(dsegs) - 1, 0, -1):
        if dsegs[i]["ext"] < dsegs[i - 1]["ext"]:
            start = dsegs[i]
            break
    li = start["e"]
    lo, lo_date = start["ext"], bars[li]["date"]
    ups = [b for b in bis if b["dir"] == "up" and b["e"]["oi"] >= li]
    dns = [s for s in dsegs if s["e"] >= li]
    steps = []
    steps.append({"name": "5min 向上笔", "ok": len(ups) >= 1,
                  "note": ("起点 %.2f（%s）还没有向上笔" % (lo, lo_date[:16])) if not ups
                          else ("已走出，高点 %.2f" % ups[0]["e"]["price"])})
    need = ups[0]["e"]["price"] if ups else None
    steps.append({"name": "5min 向上段", "ok": bool(len(ups) >= 2 and ups[-1]["e"]["price"] > ups[0]["e"]["price"]),
                  "note": ("需再破 %.2f" % need) if need else "—"})
    steps.append({"name": "回踩不破", "ok": bool(dns and dns[-1]["ext"] > lo),
                  "note": ("回踩 %.2f，起点 %.2f" % (dns[-1]["ext"], lo)) if dns else "尚未回踩"})
    ok4, n4 = False, "30min 尚未出现新中枢"
    if m30 and m30.get("zs"):
        z = m30["zs"][-1]
        # 必须是"起点之后形成的、且价格站住下沿"的 30min 中枢，才算级别升级
        ok4 = z["start"] >= lo_date and close >= z["zd"]
        n4 = "30min 中枢 %.2f~%.2f（%s 起）%s" % (
            z["zd"], z["zg"], z["start"][:10],
            "" if z["start"] >= lo_date else "，但早于本次起点")
    steps.append({"name": "30min 中枢", "ok": ok4, "note": n4})
    return {"lo": round(lo, 2), "lo_date": lo_date, "steps": steps,
            "done": sum(1 for s in steps if s["ok"])}


def ema(vals, n):
    k, out, e = 2.0 / (n + 1), [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd(closes):
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    return dif, dea, [2 * (a - b) for a, b in zip(dif, dea)]


def hist_area(hist, a, b, sign):
    return sum(x for x in hist[a:b + 1] if (x > 0) == (sign > 0))


def analyze(bars, name="", mode="range", max_bi=None):
    merged = merge_inclusion(bars)
    fx = attach_extreme(bars, merged, find_fx(merged))
    bis = build_bi(fx)
    dif, dea, hist = macd([b["c"] for b in bars])
    return {"name": name, "bars": bars, "bis": bis, "zs": build_zs(bis, mode, max_bi),
            "dif": dif, "dea": dea, "hist": hist, "mode": mode, "max_bi": max_bi}


def legs(level, direction):
    bars, bis = level["bars"], level["bis"]
    segs = [{"s": b["s"]["oi"], "e": b["e"]["oi"], "sp": b["s"]["price"],
             "ext": b["e"]["price"], "ongoing": False}
            for b in bis if b["dir"] == direction]
    if bis and bis[-1]["dir"] != direction:
        start = bis[-1]["e"]["oi"]
        idxs = list(range(start + 1, len(bars)))
        if idxs:
            if direction == "down":
                k = min(idxs, key=lambda i: bars[i]["l"])
                ext = bars[k]["l"]
            else:
                k = max(idxs, key=lambda i: bars[i]["h"])
                ext = bars[k]["h"]
            segs.append({"s": start, "e": k, "sp": bis[-1]["e"]["price"],
                         "ext": ext, "ongoing": True, "syn": True})
    # 2026-11-27：末端笔试穿校验。
    # 最后一笔之后若又创新低/新高，说明这个"已成立"的笔端已被后续走势否定：
    # 笔端延到极值那根并标 ongoing。否则会出现"笔已走完（≈一买）"连着几天
    # 挂在不断创新低的走势上（11-24~26 上证 / 创业板实测）。
    if segs and bis and bis[-1]["dir"] == direction:
        last = segs[-1]
        idxs = list(range(last["e"] + 1, len(bars)))
        if idxs:
            if direction == "down":
                k = min(idxs, key=lambda i: bars[i]["l"])
                if bars[k]["l"] < last["ext"]:
                    last["ext"], last["e"], last["ongoing"] = bars[k]["l"], k, True
            else:
                k = max(idxs, key=lambda i: bars[i]["h"])
                if bars[k]["h"] > last["ext"]:
                    last["ext"], last["e"], last["ongoing"] = bars[k]["h"], k, True
    return segs


def leg_state(level, direction):
    segs = legs(level, direction)
    if len(segs) < 2:
        return None
    a, b = segs[-2], segs[-1]
    sign = -1 if direction == "down" else 1
    aa = hist_area(level["hist"], a["s"], a["e"], sign)
    ab = hist_area(level["hist"], b["s"], b["e"], sign)
    da = min(level["dif"][a["s"]:a["e"] + 1]) if direction == "down" else max(level["dif"][a["s"]:a["e"] + 1])
    db = min(level["dif"][b["s"]:b["e"] + 1]) if direction == "down" else max(level["dif"][b["s"]:b["e"] + 1])
    new_ext = (b["ext"] < a["ext"]) if direction == "down" else (b["ext"] > a["ext"])
    return {"area_prev": round(aa, 1), "area_last": round(ab, 1),
            "dif_prev": round(da, 2), "dif_last": round(db, 2),
            "ext_prev": round(a["ext"], 2), "ext_last": round(b["ext"], 2),
            "new_extreme": new_ext,
            "area_shrink": abs(ab) < abs(aa) * 0.85,
            "dif_shrink": abs(db) < abs(da),
            "ongoing": b["ongoing"]}


def last_leg(level):
    """最后一笔（含末端延伸）：起点/终点/是否走完/终点后又走了几根 K 线。"""
    if not level["bis"]:
        return None
    b = level["bis"][-1]
    seg = legs(level, b["dir"])[-1]
    bars = level["bars"]
    return {"dir": b["dir"], "s": bars[seg["s"]]["date"], "sp": round(seg["sp"], 2),
            "e": bars[seg["e"]]["date"], "ep": round(seg["ext"], 2),
            "ongoing": seg["ongoing"], "since": max(0, len(bars) - 1 - seg["e"])}


# ---------------- 判定 ----------------
def find_exit_start(level, dz):
    """中枢最后一笔走完之后的那一笔的终点下标（用于三买/三卖与回试计数）。"""
    idx = None
    for k, b in enumerate(level["bis"]):
        if b["e"]["date"] == dz["end"]:
            idx = k
    if idx is not None and idx + 1 < len(level["bis"]):
        return level["bis"][idx + 1]["e"]["oi"], idx + 1
    return dz["end_oi"] + 1, (idx + 1 if idx is not None else len(level["bis"]))


def retests(level, dz, pos, bis_idx):
    """离开中枢后，第几次回试。返回 (次数, 第一次的价位)。"""
    after = level["bis"][bis_idx + 1:] if bis_idx + 1 < len(level["bis"]) else []
    if pos == "below":
        pts = [b["e"]["price"] for b in after if b["dir"] == "up" and b["e"]["price"] < dz["zd"]]
    elif pos == "above":
        pts = [b["e"]["price"] for b in after if b["dir"] == "down" and b["e"]["price"] > dz["zg"]]
    else:
        pts = []
    return len(pts), (round(pts[0], 2) if pts else None)


def judge(level, m30, monthly, m5=None):
    bars = level["bars"]
    close, date = bars[-1]["c"], bars[-1]["date"]
    out = {"close": close, "date": date}

    dz = level["zs"][-1] if level["zs"] else None
    if dz:
        pos = "above" if close > dz["zg"] else ("below" if close < dz["zd"] else "inside")
    else:
        pos, dz = "inside", {"zd": close, "zg": close, "start": date, "end": date, "end_oi": len(bars) - 2}
    out["zs"], out["pos"] = dz, pos

    exit_start, bis_idx = find_exit_start(level, dz)
    out["exit_date"] = bars[exit_start]["date"] if exit_start < len(bars) else None
    n_retest, first_retest = retests(level, dz, pos, bis_idx)
    out["retest_n"], out["retest_first"] = n_retest, first_retest
    out["sell3"] = first_retest if (pos == "below" and n_retest >= 1) else None
    out["buy3"] = first_retest if (pos == "above" and n_retest >= 1) else None

    after = bars[exit_start:]
    out["hi_after"] = round(max((b["h"] for b in after), default=close), 2)
    out["lo_after"] = round(min((b["l"] for b in after), default=close), 2)

    out["d_down"] = leg_state(level, "down")
    out["d_up"] = leg_state(level, "up")
    out["last_leg"] = last_leg(level)
    out["m_down"] = leg_state(m30, "down") if m30 and m30["bis"] else None
    out["m30_zs"] = m30["zs"] if (m30 and m30["zs"]) else []

    mz = monthly["zs"][-1] if monthly["zs"] else None
    out["monthly_zs"] = mz
    # 闸门口径：站上月线中枢上沿才算大级别转强（不是"跌破下沿"）。
    # 2026-11-24 备注：把"破位"改成"未转强"——价格在月线中枢内部属于大级别震荡，不叫破位。
    out["monthly_ok"] = (mz is None) or (close > mz["zg"])
    out["monthly_pos"] = ("无中枢" if mz is None
                          else ("上方" if close > mz["zg"] else ("下方" if close < mz["zd"] else "内")))

    ups = [b for b in monthly["bis"] if b["dir"] == "up"]
    fib = None
    if ups:
        u = ups[-1]
        lo, hi = u["s"]["price"], u["e"]["price"]
        if hi > lo:
            fib = {"lo": round(lo, 2), "hi": round(hi, 2),
                   "r382": round(hi - (hi - lo) * 0.382, 2), "r500": round(hi - (hi - lo) * 0.5, 2),
                   "r618": round(hi - (hi - lo) * 0.618, 2), "r786": round(hi - (hi - lo) * 0.786, 2),
                   "ret": round((hi - close) / (hi - lo) * 100, 1)}
    out["fib"] = fib
    out["ma20"] = round(sum(b["c"] for b in bars[-20:]) / 20, 2)
    out["light"], out["title"] = light_of_levels(level, dz, pos, out["d_down"], mz,
                                                 out["monthly_ok"], out["monthly_pos"])
    out["line"] = narrative(out, level)
    out["risk"] = risk_notes(out, level)

    # ---- 2026-11-24 新增：缺口 / 量价 / 新鲜度 / 中枢稳定性 / 级别梯 / 升级状态机 ----
    out["gaps"] = gaps(bars)
    out["vol"] = vol_state(bars)
    out["fresh"] = freshness(bars)
    out["stab"] = None if FAST else zs_stability(bars, mode=level.get("mode", "range"), max_bi=level.get("max_bi"))
    out["ladder"] = level_ladder(level, m30, m5)
    out["upgrade"] = upgrade_state(m5, m30)
    out["action"] = action_of(out)
    out["m5_zs"] = m5["zs"] if (m5 and m5["zs"]) else []
    return out


def action_of(j):
    """把「灯 + 级别升级」翻译成一句能执行的动作。黄灯不再只有「等」。

    2026-11-28 加：黄灯下三种可行动作分开——
    ① 日线双背驰买点已现 → 轻仓试探；
    ② 5min 升级 ≥3 关 → 小级别启动，轻仓试探（止损取 5min 起点）；
    ③ 其余 → 等底分型 / 中枢内不动。
    绿灯仍只是「持有 / 回踩加」，本页不给满仓建议。
    """
    light = j.get("light")
    title = j.get("title") or ""
    pos = j.get("pos")
    up = j.get("upgrade") or {}
    done = up.get("done", 0) or 0
    lo = up.get("lo")
    if light == "green":
        return {"k": "持有 / 回踩加", "why": "中枢上方，结构未坏"}
    if light == "red":
        return {"k": "空仓等", "why": "下跌未减速，或跌破月线中枢"}
    if "买点已现" in title or "≈一买" in title:
        return {"k": "轻仓试探（日线≈一买）", "why": "日线下跌双背驰，笔已走完"}
    if done >= 3:
        why = "5min 升级 %d/4 关" % done
        if lo:
            why += "，止损 %.2f" % lo
        return {"k": "轻仓试探（小级别启动）", "why": why}
    if pos == "inside":
        return {"k": "等方向（中枢内不动）", "why": "价格在中枢内，无买卖点"}
    if done >= 1:
        return {"k": "观察：5min 刚起步", "why": "5min 升级 %d/4 关" % done}
    return {"k": "等底分型确认", "why": "减速不等于背驰"}


def light_of_levels(level, dz, pos, dd, mz, monthly_ok, monthly_pos="内"):
    # 大级别只在"跌破月线中枢下沿"时一票否决（真破位）。
    # 价格停在月线中枢内部＝大级别震荡，交给日线结构判灯，只在"月线（大级别）"那行说明。
    # 2026-11-24 修订：旧版用"跌破上沿"当破位，与"中枢内震荡"混淆；若让"内部"也覆盖信号灯，
    # 信号会长期卡在黄灯（实测上证 80% 的交易日落在月线中枢内）。
    if mz is not None and monthly_pos == "下方":
        return "red", "月线破位，宁可空仓"
    if pos == "below":
        if dd and dd["new_extreme"] and dd["dif_shrink"] and dd["area_shrink"] and not dd["ongoing"]:
            return "amber", "力度双缩且笔已走完（≈一买）"
        if dd and dd["new_extreme"] and dd["dif_shrink"]:
            return "amber", "下跌在减速，等底分型"
        if dd and dd["new_extreme"] and dd["area_shrink"]:
            # DIF 还在创新低时，单看绿柱面积缩小不足以叫"减速"，措辞降一档
            return "amber", "面积缩小但 DIF 仍走低（减速待确认）"
        return "red", "下跌未减速，空仓等"
    if pos == "inside":
        return "amber", "中枢震荡，等方向"
    du = leg_state(level, "up")
    if du and du["new_extreme"] and (du["dif_shrink"] or du["area_shrink"]):
        return "amber", "上方力度双缩，减仓"
    return "green", "中枢上方，持有"


def light_of_bars(bars):
    lv = analyze(bars, mode="endpoint", max_bi=9)
    if not lv["zs"]:
        return None
    dz = lv["zs"][-1]
    close = bars[-1]["c"]
    pos = "above" if close > dz["zg"] else ("below" if close < dz["zd"] else "inside")
    mo = analyze(to_monthly(bars), mode="endpoint", max_bi=9)
    mz = mo["zs"][-1] if mo["zs"] else None
    ok = (mz is None) or (close > mz["zg"])
    return light_of_levels(lv, dz, pos, leg_state(lv, "down"), mz, ok)[0]


def narrative(j, level):
    dz, pos = j["zs"], j["pos"]
    where = {"above": "上方", "below": "下方", "inside": "内"}[pos]
    s = "日线在 %.0f~%.0f 中枢%s" % (dz["zd"], dz["zg"], where)
    if j.get("sell3"):
        s += "；离开后反弹第一次只到 %.2f（三卖）" % j["sell3"]
        if j["retest_n"] > 1:
            s += "，现已第 %d 次回试" % j["retest_n"]
    if j.get("buy3"):
        s += "；离开后回踩第一次到 %.2f（三买）" % j["buy3"]
        if j["retest_n"] > 1:
            s += "，现已第 %d 次回试" % j["retest_n"]
    s += "。"
    dd = j["d_down"]
    if dd:
        s += "本轮下跌 %.0f→%.0f：" % (dd["ext_prev"], dd["ext_last"])
        if dd["new_extreme"] and dd["dif_shrink"] and dd["area_shrink"]:
            s += "价新低、DIF 抬高（%.1f→%.1f）、绿柱面积缩小" % (dd["dif_prev"], dd["dif_last"])
        elif dd["new_extreme"] and dd["dif_shrink"]:
            s += "价新低但 DIF 抬高（%.1f→%.1f）" % (dd["dif_prev"], dd["dif_last"])
        elif dd["new_extreme"]:
            s += "价新低、DIF 也走低（%.1f→%.1f），力度没减弱" % (dd["dif_prev"], dd["dif_last"])
        else:
            s += "还没跌破前低 %.2f" % dd["ext_prev"]
        s += "；这一笔还没走完。" if dd["ongoing"] else "；这一笔已走完。"
    if j["m30_zs"]:
        z = j["m30_zs"][-1]
        s += "30 分钟最后中枢 %.0f~%.0f。" % (z["zd"], z["zg"])
    return s


def risk_notes(j, level):
    """把第16课、第20课的退出规则变成可触发的提示。"""
    notes = []
    dz, pos = j["zs"], j["pos"]
    if j.get("buy3"):
        # 三买后是否已走出第二个同级别中枢（成趋势）
        zs_after = [z for z in level["zs"] if z["start_oi"] > dz["end_oi"]]
        if len(zs_after) == 0:
            notes.append(("第20课", "三买之后还没有走出第二个同级别中枢（未成趋势）——一旦反弹走成盘整，按规则在盘整高点出掉。"))
    if pos == "inside" and j.get("retest_n", 0) == 0:
        notes.append(("第16课", "价格回到中枢内＝盘整。若你在前一笔买点入场，按规则应当退出（盘整不参与，且盘整后有一半概率继续下跌）。"))
    if j["d_down"] and j["d_down"]["ongoing"] and not j["d_down"]["dif_shrink"]:
        notes.append(("第16课", "跌势未尽的风险尚未排除：这一段下跌 DIF 还在走低，不满足'跌势已尽'的前提。"))
    dd = j["d_down"] or {}
    if dd and dd.get("new_extreme") and dd.get("dif_shrink") and dd["ongoing"]:
        notes.append(("第16课", "力度已衰减但笔未走完（缺底分型）。此时按一买入场＝在\'跌势未尽\'处接刀，原文明确属于要躲的两大风险之一，应当等确认。"))
    if j.get("sell3") and j["retest_n"] > 1:
        notes.append(("第20课", "已经是第 %d 次回试。原文只认第一次，后续回抽不再构成三卖，不要按\'三卖\'加空。" % j["retest_n"]))
    if not notes:
        notes.append(("无", "当前没有触发原文的退出条件；信号是\'等待/空仓\'，不构成买入依据。"))
    return notes


# ---------------- 回测（走查） ----------------
def backtest(bars, step=3, min_hist=260):
    buckets = {k: {"n": 0, "r5": [], "r10": [], "r20": [], "win5": 0, "dd20": []}
               for k in ("red", "amber", "green")}
    trans = {k: {"n": 0, "r5": [], "r10": [], "r20": [], "win5": 0, "dd20": []}
             for k in ("red", "amber", "green")}
    n = len(bars)
    prev = None
    for t in range(min_hist, n - 20, step):
        try:
            light = light_of_bars(bars[:t + 1])
        except Exception:
            continue
        if not light:
            continue
        c0 = bars[t]["c"]
        r5 = bars[t + 5]["c"] / c0 - 1
        r10 = bars[t + 10]["c"] / c0 - 1
        r20 = bars[t + 20]["c"] / c0 - 1
        dd = min(x["l"] for x in bars[t + 1:t + 21]) / c0 - 1
        for target in ([buckets[light]] + ([trans[light]] if (prev and light != prev) else [])):
            target["n"] += 1
            target["r5"].append(r5)
            target["r10"].append(r10)
            target["r20"].append(r20)
            target["win5"] += 1 if r5 > 0 else 0
            target["dd20"].append(dd)
        prev = light

    def pack(src):
        out = {}
        for k, v in src.items():
            if not v["n"]:
                continue
            out[k] = {"n": v["n"], "r5": round(median(v["r5"]) * 100, 2),
                      "r10": round(median(v["r10"]) * 100, 2), "r20": round(median(v["r20"]) * 100, 2),
                      "win5": round(v["win5"] / v["n"] * 100, 0),
                      "dd20": round(median(v["dd20"]) * 100, 2)}
        return out

    return {"state": pack(buckets), "trans": pack(trans),
            "from": bars[min_hist]["date"], "to": bars[n - 21]["date"]}


# ---------------- 画图 ----------------
def draw_chart(level, path, levels=None, n=250):
    from PIL import Image, ImageDraw, ImageFont

    bars = level["bars"][-n:]
    lo_i = len(level["bars"]) - len(bars)
    W, H = 1080, 760
    img = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(img)

    def font(size):
        for p in (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size, index=0)
                except Exception:
                    continue
        return ImageFont.load_default()

    f_s, f_m = font(19), font(22)
    L, R, T, B = 55, W - 85, 40, 470
    MT, MB = 520, 720
    pmin, pmax = min(b["l"] for b in bars), max(b["h"] for b in bars)
    for p_, _lab, _col in (levels or []):
        if p_:
            pmin, pmax = min(pmin, p_), max(pmax, p_)
    pad = (pmax - pmin) * 0.06
    pmin, pmax = pmin - pad, pmax + pad

    def x_of(i):
        return L + (R - L) * i / max(len(bars) - 1, 1)

    def y_of(p):
        return B - (B - T) * (p - pmin) / (pmax - pmin)

    for k in range(5):
        p = pmin + (pmax - pmin) * k / 4
        y = y_of(p)
        d.line([(L, y), (R, y)], fill=(230, 230, 226))
        d.text((R + 6, y - 8), "%.0f" % p, font=f_s, fill=(120, 120, 120))
    step = max(1, len(bars) // 6)
    for i in range(0, len(bars), step):
        d.text((x_of(i) - 22, B + 6), bars[i]["date"][2:], font=f_s, fill=(120, 120, 120))

    shown = [z for z in level["zs"] if z["end_oi"] >= lo_i]
    for k, z in enumerate(shown):
        x1 = x_of(max(0, z["start_oi"] - lo_i))
        x2 = x_of(min(len(bars) - 1, z["end_oi"] - lo_i))
        newest = (k == len(shown) - 1)
        d.rectangle([x1, y_of(z["zg"]), x2, y_of(z["zd"])],
                    outline=(216, 126, 16) if newest else (233, 206, 176), width=2 if newest else 1)

    for i, b in enumerate(bars):
        x = x_of(i)
        col = (198, 40, 40) if b["c"] >= b["o"] else (20, 128, 66)
        d.line([(x, y_of(b["h"])), (x, y_of(b["l"]))], fill=col, width=1)
        y1, y2 = y_of(max(b["o"], b["c"])), y_of(min(b["o"], b["c"]))
        d.rectangle([x - 2, y1, x + 2, max(y2, y1 + 1)], fill=col)

    pts = []
    for b in level["bis"]:
        if b["e"]["oi"] < lo_i:
            continue
        if not pts:
            pts.append((x_of(b["s"]["oi"] - lo_i), y_of(b["s"]["price"])))
        pts.append((x_of(b["e"]["oi"] - lo_i), y_of(b["e"]["price"])))
    if len(pts) > 1:
        d.line(pts, fill=(38, 86, 186), width=2)
        for (px, py) in pts:
            d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=(38, 86, 186))

    lx, ly = L, T - 6
    for p_, lab, col in (levels or []):
        if p_ is None or not (pmin < p_ < pmax):
            continue
        y = y_of(p_)
        for xx in range(L, R, 18):
            d.line([(xx, y), (xx + 9, y)], fill=col, width=2)
        try:
            w = d.textlength(lab, font=f_s)
        except Exception:
            w = 9 * len(lab)
        if lx + w > R:
            lx, ly = L, ly + 26
        d.text((lx, ly), lab, font=f_s, fill=col)
        lx += w + 20

    hist = level["hist"][lo_i:]
    span = max(max(abs(x) for x in hist), 1) * 1.2
    zero = (MT + MB) / 2

    def yh(v):
        return zero - (MB - MT) / 2 * v / span

    d.line([(L, zero), (R, zero)], fill=(140, 140, 140))
    for i in range(len(hist)):
        d.line([(x_of(i), zero), (x_of(i), yh(hist[i]))],
               fill=(198, 40, 40) if hist[i] >= 0 else (20, 128, 66))
    d.line([(x_of(i), yh(level["dif"][lo_i + i])) for i in range(len(bars))], fill=(35, 35, 35), width=2)
    d.line([(x_of(i), yh(level["dea"][lo_i + i])) for i in range(len(bars))], fill=(160, 150, 40), width=2)
    img.save(path, optimize=True)


# ---------------- 页面 ----------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f4f5f7;color:#1c1c1e;
     -webkit-text-size-adjust:100%;padding:12px 12px 34px;max-width:580px;margin:0 auto;line-height:1.6}
h1{font-size:18px;font-weight:600}
.sub{font-size:12.5px;color:#8a8a8e;margin-top:3px}
.tabs{display:flex;gap:8px;margin-top:12px}
.tab{flex:1;border:0;background:#fff;border-radius:12px;padding:9px 4px;font-size:13.5px;color:#5a5a5e;
     box-shadow:0 1px 3px rgba(0,0,0,.05);display:flex;flex-direction:column;align-items:center;gap:3px}
.tab.on{background:#1c1c1e;color:#fff}
.tab .px{font-size:12px;opacity:.75;font-variant-numeric:tabular-nums}
.tab .dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px}
.d-red{background:#e05b4a}.d-amber{background:#e0a13a}.d-green{background:#37a06b}
.panel{display:none}
.panel.on{display:block}
.card{background:#fff;border-radius:14px;padding:14px 15px;margin-top:12px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.verdict{border-radius:16px;padding:16px;margin-top:12px;color:#fff}
.verdict.red{background:linear-gradient(135deg,#c0392b,#e05b4a)}
.verdict.amber{background:linear-gradient(135deg,#c77c14,#e0a13a)}
.verdict.green{background:linear-gradient(135deg,#1f7a4d,#37a06b)}
.verdict .tag{font-size:12px;opacity:.85;letter-spacing:3px}
.verdict .big{font-size:21px;font-weight:700;margin-top:6px;line-height:1.4}
.verdict .line{font-size:13px;opacity:.95;margin-top:8px}
.verdict .act{margin-top:10px;background:rgba(255,255,255,.2);border-radius:10px;padding:8px 11px;display:flex;flex-direction:column;gap:2px}
.verdict .act b{font-size:15.5px}
.verdict .act .why{font-size:12px;opacity:.88}
.alert{background:#fff7e6;border:1px solid #f0d9a8;color:#7a4f00;border-radius:12px;padding:10px 12px;margin-top:12px;font-size:13px;line-height:1.55}
.alert b{color:#8a5a00}
.kv{display:flex;justify-content:space-between;gap:10px;font-size:14px;padding:8px 0;border-bottom:1px dashed #ececef}
.kv:last-child{border-bottom:0}
.kv .k{color:#76767a;white-space:nowrap}
.kv .v{font-weight:600;text-align:right}
h2{font-size:14px;font-weight:600;margin-bottom:8px;color:#3a3a3c}
.pill{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:9px}
.p-red{background:#fdeaea;color:#c0392b}.p-amber{background:#fdf2e2;color:#b3720c}
.p-green{background:#e8f5ee;color:#1f7a4d}.p-grey{background:#f0f0f2;color:#6f6f74}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 4px;border-bottom:1px solid #f2f2f4;vertical-align:top}
th{color:#8a8a8e;font-weight:500;font-size:12px}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}
img{width:100%;border-radius:10px;display:block}
.risk{font-size:13px;color:#3a3a3c;padding:8px 0;border-bottom:1px dashed #ececef}
.rule{font-size:12.5px;color:#5a5a5e;background:#f7f7f9;border-radius:9px;padding:9px 11px;margin-bottom:7px;line-height:1.55}
.rule b{color:#3a3a3c}
.risk:last-child{border-bottom:0}
.risk b{color:#c0392b;font-weight:600}
details{font-size:13px;color:#5a5a5e}
summary{cursor:pointer;color:#3a3a3c;font-weight:600;font-size:14px}
.foot{font-size:11.5px;color:#9a9aa0;margin-top:16px;line-height:1.7}
"""

SOURCE_TABLE = """
<table>
<tr><th>本站口径</th><th>原文（《教你炒股票》）</th></tr>
<tr><td>用<b>笔</b>做中枢的最小零件</td><td>第18/20课：中枢由"三个连续<b>次级别走势类型</b>"重叠确定，区间 <code>[max(a2,b2,c2), min(a1,b1,c1)]</code>，简化为 <code>[max(a2,c2), min(a1,c1)]</code>。笔≠走势类型，简化了一级。</td></tr>
<tr><td>延伸：<b>整笔区间</b>与中枢区间有重叠即延伸，不设笔数截断</td><td>第20课中心定理一：中枢延伸 ⟺ 任意 <code>[dn,gn]</code> 与 <code>[ZD,ZG]</code> 有重叠。<b>2026-11-24 已对齐</b>（旧版用"笔端点落入区间"+9 笔截断，实测会让中枢上沿偏高、触发价随数据漂移）。</td></tr>
<tr><td>月线闸门三态</td><td>站上月线中枢上沿=放行；在中枢内部=只做小级别；跌破下沿=大级别破位。本站把"内部"当作震荡而非破位，这是本站的读法，原文没有这条量化规则。</td></tr>
<tr><td>缺口只作一行<b>旁证</b>，不进判定</td><td>缺口不是缠论要素。原文里缺口只出现在<b>线段划分的特征序列</b>（有无缺口决定线段破坏要不要第三元素确认），是分类判据、不是目标位。本站连线段都没自动识别，所以缺口只作参考，信号灯 / 升级状态机 / 触发表一概不用它。</td></tr>
<tr><td>量价配合四象限</td><td>不用"缩量=好"的单边门槛：指数是加权合成，低位放量可能是出清也可能是破位加速，本站只做描述不做判定。</td></tr>
<tr><td>三买/三卖只认<b>第一次</b>回试</td><td>第20课：<i>"并不是任何回调回抽都是第三类买卖点，必须是第一次。"</i> —— 一致。</td></tr>
<tr><td>叫"<b>同向笔力度</b>（背驰近似）"</td><td>第16课：<i>"没有趋势，没有背驰；背驰是前后趋势间的比较。"</i> 本站比的是同向笔，不是两个趋势，所以只能叫近似。口径：<b>DIF 抬高</b>才算"减速"；只有绿柱面积缩小、DIF 仍创新低时，只标"减速待确认"。</td></tr>
<tr><td>末端笔<b>击穿校验</b>（本站新增，2026-11-27）</td><td>原文没有"笔已成立、但被后续新低否定"的显式条款。本站按笔的定义补一条：最后一笔之后若再创新低/新高，笔端延到极值那根、视为仍未走完——否则会把"底分型成立"误读成"跌完了"。</td></tr>
<tr><td>风控块</td><td>第16课：入场后一旦出现盘整，坚决退出；第20课：三买后不成趋势，在盘整高点出掉。</td></tr>
</table>
"""


def render(j, level, m30, monthly, hist_rows, gen_at, bt, sym, chart):
    dz, fib, mz = j["zs"], j["fib"] or {}, j["monthly_zs"] or {}
    act = j.get("action") or {"k": "—", "why": ""}
    pos_txt = "%s（%.0f~%.0f）" % ({"above": "中枢上方", "below": "中枢下方", "inside": "中枢内"}[j["pos"]], dz["zd"], dz["zg"])

    if j.get("sell3"):
        retest = "三卖 %.2f（第 1 次回试）" % j["sell3"]
        if j["retest_n"] > 1:
            retest += "，已第 %d 次" % j["retest_n"]
    elif j.get("buy3"):
        retest = "三买 %.2f（第 1 次回试）" % j["buy3"]
        if j["retest_n"] > 1:
            retest += "，已第 %d 次" % j["retest_n"]
    else:
        retest = "未出现"

    ll = j.get("last_leg") or {}
    if ll:
        llk = "最后一笔（日线·%s）" % ("向上" if ll["dir"] == "up" else "向下")
        lltxt = "%s %.2f → %s %.2f（%s）" % (
            ll["s"][5:10], ll["sp"], ll["e"][5:10], ll["ep"],
            "未走完" if ll["ongoing"] else "已走完")
        if ll.get("since"):
            lltxt += "，终点后又走 %d 根" % ll["since"]
    else:
        llk, lltxt = "最后一笔（日线）", "—"

    dd = j["d_down"] or {}
    if dd.get("new_extreme") and dd.get("dif_shrink") and dd.get("area_shrink"):
        dcls, dtxt = "p-amber", "价新低 + DIF 抬高 + 面积缩（≈底背驰）"
    elif dd.get("new_extreme") and dd.get("dif_shrink"):
        dcls, dtxt = "p-amber", "价新低但 DIF 抬高（有背离迹象）"
    elif dd.get("new_extreme") and dd.get("area_shrink"):
        dcls, dtxt = "p-amber", "价新低、DIF 仍新低（面积缩，减速待确认）"
    elif dd.get("new_extreme"):
        dcls, dtxt = "p-red", "价新低、DIF 与面积都没缩（比前段更猛）"
    else:
        dcls, dtxt = "p-grey", "未跌破前低"

    rows_res, rows_sup = [], []
    rows_res.append(("MA20", j["ma20"]))
    if j.get("sell3"):
        rows_res.append(("三卖 / 第一次反弹高点", j["sell3"]))
    rows_res.append(("日线中枢下沿", dz["zd"]))
    if fib.get("r618"):
        rows_sup.append(("0.618 回撤", fib["r618"]))
    if mz and mz.get("zg"):
        rows_sup.append(("月线中枢上沿", mz["zg"]))
    if fib.get("r786"):
        rows_sup.append(("0.786 回撤", fib["r786"]))
    rows_res.sort(key=lambda x: x[1])
    rows_sup.sort(key=lambda x: -x[1])

    conds = []
    if j["m30_zs"]:
        z = j["m30_zs"][-1]
        conds.append(("30 分钟收盘站回 <b>%.2f</b>（最后中枢下沿）并写出底分型" % z["zd"],
                      "短线止跌，可小仓做差，目标先看 MA20 %.2f" % j["ma20"]))
    if dd:
        conds.append(("再跌破 <b>%.2f</b> 时，绿柱面积仍小于 %.0f" % (dd.get("ext_last", 0), dd.get("area_prev", 0)),
                      "力度双缩且笔走完 → 一买近似"))
    if mz and mz.get("zg"):
        conds.append(("收盘跌破 <b>%.2f</b> 且一周收不回" % mz["zg"],
                      "大级别转弱，空间下移到 0.786（%.2f）" % fib.get("r786", 0)))
    conds.append(("反弹站上 <b>%.2f</b>" % dz["zd"], "回到中枢内，下跌结构才算被破坏"))

    rule_static = (
        '<div class="rule"><b>第16课</b>　入场后一旦出现盘整走势，坚决退出（盘整不参与，且盘整后有一半概率继续下跌）。</div>'
        '<div class="rule"><b>第20课</b>　三类买卖点必须是<b>第一次</b>回试；三买后若走不出第二个同级别中枢（不成趋势），就在盘整高点出掉。</div>'
        '<div class="rule"><b>第20课</b>　中枢延伸期间只做震荡，不做方向判断（价格在中枢内来回＝没有方向）。</div>'
    )
    trig = "".join('<div class="risk">【%s】%s</div>' % (a, b) for a, b in j["risk"])
    risk_html = rule_static + '<div style="margin-top:10px;font-size:12.5px;color:#8a8a8e">当前触发</div>' + trig

    def bt_rows(dct, labels):
        out = ""
        for k, lab, cls in labels:
            if k not in dct:
                continue
            v = dct[k]
            note = ""
            if v["n"] < 30:
                note = ' <span style="font-size:11px;color:#9a9aa0">样本不足</span>'
            elif k == "amber" and v["r20"] < 0:
                note = ' <span style="font-size:11px;color:#c0392b">20日期望为负</span>'
            out += ('<tr><td><span class="pill %s">%s</span>%s</td><td class="n">%d</td><td class="n">%+.2f%%</td>'
                    '<td class="n">%+.2f%%</td><td class="n">%+.2f%%</td><td class="n">%.0f%%</td><td class="n">%+.2f%%</td></tr>'
                    % (cls, lab, note, v["n"], v["r5"], v["r10"], v["r20"], v["win5"], v["dd20"]))
        return out

    labels = (("red", "红灯（空仓等）", "p-red"), ("amber", "黄灯（观察）", "p-amber"),
              ("green", "绿灯（持有）", "p-green"))
    btr = bt_rows((bt or {}).get("state", {}), labels) or '<tr><td colspan="7">样本不足</td></tr>'
    btt = bt_rows((bt or {}).get("trans", {}), labels) or '<tr><td colspan="7">样本不足</td></tr>'
    bt_period = ("%s ~ %s" % (bt["from"], bt["to"])) if bt and bt.get("from") else "—"

    for k, lab, cls in ():
        v = {}
        btr += ('<tr><td><span class="pill %s">%s</span></td><td class="n">%d</td><td class="n">%+.2f%%</td>'
                '<td class="n">%+.2f%%</td><td class="n">%+.2f%%</td><td class="n">%.0f%%</td><td class="n">%+.2f%%</td></tr>'
                % (cls, lab, v["n"], v["r5"], v["r10"], v["r20"], v["win5"], v["dd20"]))
    if not btr:
        btr = '<tr><td colspan="7">样本不足，暂不显示</td></tr>'

    hist_tbl = "".join(
        '<tr><td class="n">%s</td><td class="n">%.2f</td><td><span class="pill p-%s">%s</span></td></tr>'
        % (h.get("date", ""), h.get("close", 0),
           {"red": "red", "amber": "amber", "green": "green"}.get(h.get("light"), "grey"), h.get("title", ""))
        for h in reversed(hist_rows[-8:]))

    m30_txt = "—"
    if j["m30_zs"]:
        z = j["m30_zs"][-1]
        m30_txt = "最后中枢 %.0f~%.0f" % (z["zd"], z["zg"])
    if mz:
        if j["close"] > mz["zg"]:
            month_txt = "月线中枢 %.0f~%.0f（在上方）" % (mz["zd"], mz["zg"])
        elif j["close"] < mz["zd"]:
            month_txt = "月线中枢 %.0f~%.0f（跌破下沿，大级别转弱）" % (mz["zd"], mz["zg"])
        else:
            month_txt = "月线中枢 %.0f~%.0f（在内，大级别震荡）" % (mz["zd"], mz["zg"])
    else:
        month_txt = "月线中枢不足"

    # ---------- 2026-11-24 新增渲染块 ----------
    fresh = j.get("fresh") or {}
    fresh_html = ""
    if fresh and not fresh.get("ok"):
        fresh_html = ('<div class="card" style="border-left:4px solid #c0392b">'
                      '<h2>数据滞后</h2><div class="line">最后一根日线是 <b>%s</b>，按交易日应为 <b>%s</b>'
                      '（落后约 %d 个交易日）。15:35 那次常拿到 T-1，等 16:35 那次覆盖；'
                      '动手前先看这里的日期。</div></div>' % (fresh.get("last"), fresh.get("want"), fresh.get("lag")))

    lad = j.get("ladder") or {}
    lad_rows = ""
    for r in lad.get("rows", []):
        if not r.get("ok"):
            lad_rows += '<tr><td>%s</td><td colspan="3" class="n">结构不足</td></tr>' % r["name"]
            continue
        need = "需涨" if r["pos"] == "中枢下方" else ("需跌" if r["pos"] == "中枢上方" else "")
        lad_rows += ('<tr><td>%s</td><td class="n">%.2f~%.2f</td><td>%s</td>'
                     '<td class="n">%s %+.1f%%</td></tr>'
                     % (r["name"], r["zd"], r["zg"], r["pos"], need, r["dist"]))
    ladder_html = ('<div class="card"><h2>级别梯 <span class="pill p-grey">现价 %.2f</span></h2>'
                   '<table><tr><th>级别</th><th class="n">最后中枢</th><th>位置</th>'
                   '<th class="n">距最近边界</th></tr>%s</table>'
                   '<div style="font-size:11.5px;color:#9a9aa0;margin-top:8px">'
                   '大级别定方向、小级别定动作。日线中枢只用来判“结构坏没坏”，'
                   '能不能动手看 5min / 30min。</div><!--GAP--></div>' % (lad.get("close", j["close"]), lad_rows))

    upg = j.get("upgrade")
    upg_rows = ""
    if upg:
        for s in upg["steps"]:
            upg_rows += ('<tr><td>%s</td><td><span class="pill %s">%s</span></td><td>%s</td></tr>'
                         % (s["name"], "p-green" if s["ok"] else "p-grey",
                            "已过" if s["ok"] else "未过", s["note"]))
    upg_html = ""
    if upg:
        upg_html = ('<div class="card"><h2>级别升级状态机 <span class="pill %s">%d/4</span></h2>'
                    '<div class="line" style="margin:0 0 10px">5min 起点 <b>%.2f</b>（%s）。'
                    '“5min 段成立 → 回踩不破 → 做出 30min 中枢”三件事依次完成，'
                    '才算 30min 级别启动；在此之前每次反弹都可能是最后一次。</div>'
                    '<table><tr><th>关口</th><th>状态</th><th>读数</th></tr>%s</table></div>'
                    % ("p-green" if upg["done"] >= 4 else "p-amber", upg["done"],
                       upg["lo"], upg["lo_date"][:16], upg_rows))

    # 缺口：非缠论口径，只作一行旁证挂在级别梯下面，不占卡片、不进判定
    gp = j.get("gaps") or []
    if gp:
        gp_txt = "、".join("%.2f~%.2f" % (g["lo"], g["hi"]) for g in gp[:3])
        gap_note = ('<div style="font-size:11.5px;color:#9a9aa0;margin-top:6px;line-height:1.6">'
                    '旁证（<b>非缠论口径</b>，不进判定）：上方另有 %d 道跳空未补 —— %s%s。'
                    '缠论里缺口只用于线段划分的特征序列，不是目标位；本站判定只看中枢。</div>'
                    % (len(gp), gp_txt, " 等" if len(gp) > 3 else ""))
    else:
        gap_note = ""
    ladder_html = ladder_html.replace("<!--GAP-->", gap_note)

    vol = j.get("vol") or {}
    vol_html = "—（数据源无成交量）"
    if vol:
        vol_html = '<span class="pill %s">%s</span> 量比 %.2fx，当日 %+.2f%%　%s' % (
            vol["cls"], vol["quad"], vol["ratio"], vol["chg"], vol["note"])

    stab = j.get("stab")
    stab_html = ""
    if stab:
        srows = "".join('<tr><td class="n">T-%d</td><td class="n">%s</td><td class="n">%s</td></tr>'
                        % (r["back"], r.get("start", ""),
                           "中枢起点变了" if r.get("new")
                           else "%+.2f / %+.2f" % (r.get("dzd") or 0, r.get("dzg") or 0))
                        for r in stab["rows"])
        stab_html = ('<div class="card"><h2>中枢稳定性自检 '
                     '<span class="pill %s">最大漂移 %.2f 点</span></h2>'
                     '<div class="line" style="margin:0 0 10px">当前日线中枢 <b>%.2f~%.2f</b>，'
                     '结束于 <b>%s</b>（距今 %d 根 K 线）。中枢若已结束多日，边界自然不会漂移；'
                     '真正要盯的是它还在延伸时会不会被尾部数据带跑。</div>'
                     '<table><tr><th class="n">回退</th><th class="n">中枢起点</th>'
                     '<th class="n">下沿/上沿漂移</th></tr>%s</table>'
                     '<div style="font-size:11.5px;color:#9a9aa0;margin-top:8px">'
                     '同一段行情少用最后几根 K 线重算，中枢边界应当基本不动。'
                     '漂移超过 5 点，说明边界依赖尾部数据，触发价要打折看。</div></div>'
                     % ("p-red" if stab["unstable"] else "p-green", stab["drift"],
                        stab["zd"], stab["zg"], stab.get("end", ""), stab.get("bars_since_end", 0), srows))

    return """%(fresh)s<div class="verdict %(light)s">
  <div class="tag">今 日 信 号</div>
  <div class="big">%(title)s</div>
  <div class="line">%(line)s</div>
  <div class="act"><b>动作｜%(act)s</b><span class="why">%(why)s</span></div>
</div>
<div class="card">
  <h2>结构状态</h2>
  <div class="kv"><span class="k">日线位置</span><span class="v">%(pos)s</span></div>
  <div class="kv"><span class="k">三买 / 三卖</span><span class="v">%(retest)s</span></div>
  <div class="kv"><span class="k">同向笔力度（背驰近似）</span><span class="v"><span class="pill %(dcls)s">%(dtxt)s</span></span></div>
  <div class="kv"><span class="k">%(llk)s</span><span class="v">%(ll)s</span></div>
  <div class="kv"><span class="k">30 分钟</span><span class="v">%(m30)s</span></div>
  <div class="kv"><span class="k">量价配合</span><span class="v">%(vol)s</span></div>
  <div class="kv"><span class="k">月线（大级别）</span><span class="v">%(month)s</span></div>
</div>
%(ladder)s
%(upg)s
%(stab)s
<div class="card">
  <h2>风控提示（原文规则）</h2>
  %(risk)s
</div>
<div class="card">
  <h2>关键位</h2>
  <table><tr><th>压力</th><th class="n">点位</th></tr>%(res)s</table>
  <table style="margin-top:8px"><tr><th>支撑</th><th class="n">点位</th></tr>%(sup)s</table>
</div>
<div class="card">
  <h2>出现什么才动手</h2>
  <table><tr><th>如果出现</th><th>对应动作</th></tr>%(conds)s</table>
</div>
<div class="card">
  <h2>日线结构图 <span class="pill p-grey">蓝线=笔 橙框=中枢</span></h2>
  <img src="%(chart)s" loading="lazy" alt="结构图">
</div>
<div class="card">
  <h2>信号灯走查回测 <span class="pill p-grey">以当日收盘计</span></h2>
  <table>
    <tr><th>信号状态</th><th class="n">样本</th><th class="n">5日</th><th class="n">10日</th><th class="n">20日</th><th class="n">5日胜率</th><th class="n">20日回撤</th></tr>
    %(bt)s
  </table>
  <table style="margin-top:10px">
    <tr><th>信号切换日</th><th class="n">样本</th><th class="n">5日</th><th class="n">10日</th><th class="n">20日</th><th class="n">5日胜率</th><th class="n">20日回撤</th></tr>
    %(btt)s
  </table>
  <div style="font-size:11.5px;color:#9a9aa0;margin-top:8px">样本期 %(btp)s，中位数收益；“信号切换日”指灯由别的颜色切到该颜色的那一天。</div>
  <div style="font-size:12px;color:#b3720c;background:#fdf2e2;border-radius:9px;padding:9px 11px;margin-top:10px;line-height:1.55">
    ⚠ 这张表是对本站自己的检验：<b>信号灯本身没有稳定的边际</b>（有的指数上红灯之后反而是涨的）。
    所以请把它当“状态描述”，不要当择时指标；真正有信息量的是三卖/中枢下沿这些结构位，以及上面的风控规则。
  </div>
</div>
<div class="card">
  <h2>最近 8 次收盘结论</h2>
  <table><tr><th>日期</th><th class="n">收盘</th><th>结论</th></tr>%(hist)s</table>
</div>
<details class="card"><summary>口径 vs 原文（点开核对）</summary><div style="margin-top:10px">%(src)s</div></details>
""" % {
        "light": j["light"], "title": j["title"], "line": j["line"],
        "act": act["k"], "why": act["why"],
        "pos": pos_txt, "retest": retest, "dcls": dcls, "dtxt": dtxt, "llk": llk, "ll": lltxt,
        "m30": m30_txt, "month": month_txt, "risk": risk_html,
        "fresh": fresh_html, "ladder": ladder_html, "upg": upg_html,
        "stab": stab_html, "vol": vol_html,
        "res": "".join('<tr><td>%s</td><td class="n">%.2f</td></tr>' % (a, b) for a, b in rows_res),
        "sup": "".join('<tr><td>%s</td><td class="n">%.2f</td></tr>' % (a, b) for a, b in rows_sup),
        "conds": "".join("<tr><td>%s</td><td>%s</td></tr>" % c for c in conds),
        "chart": chart, "bt": btr, "btt": btt, "btp": bt_period, "hist": hist_tbl, "src": SOURCE_TABLE,
    }


def main():
    os.makedirs(SITE, exist_ok=True)
    gen_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    hpath = os.path.join(SITE, "history.json")
    hist_all = {}
    if os.path.exists(hpath):
        try:
            hist_all = json.load(open(hpath, encoding="utf-8"))
        except Exception:
            hist_all = {}
    if isinstance(hist_all, list):   # 旧版单指数格式迁移
        hist_all = {"sh000001": hist_all}

    tabs, panels, brief = [], [], {}
    alerts = []
    for sym, name in INDICES:
        daily_bars = get_daily(sym)
        if len(daily_bars) < 300:
            print("skip", sym, "no data", file=sys.stderr)
            continue
        m30_bars = get_30m(sym)
        m5_bars = get_5m(sym)
        level = analyze(daily_bars, sym, mode="endpoint", max_bi=9)
        m30 = analyze(m30_bars, sym) if len(m30_bars) > 200 else None
        m5 = analyze(m5_bars, sym) if len(m5_bars) > 200 else None
        monthly = analyze(to_monthly(daily_bars), sym, mode="endpoint", max_bi=9)
        j = judge(level, m30, monthly, m5)

        bt = {} if FAST else backtest(daily_bars)
        fib_, mz_ = j["fib"] or {}, j["monthly_zs"] or {}
        levels = []
        if j.get("sell3"):
            levels.append((j["sell3"], "SELL3 %.0f" % j["sell3"], (20, 128, 66)))
        if j.get("buy3"):
            levels.append((j["buy3"], "BUY3 %.0f" % j["buy3"], (20, 128, 66)))
        if fib_.get("r618"):
            levels.append((fib_["r618"], "0.618 %.0f" % fib_["r618"], (216, 126, 16)))
        if mz_ and mz_.get("zg"):
            levels.append((mz_["zg"], "M-ZS %.0f" % mz_["zg"], (150, 60, 160)))
        chart = "chart_%s.png" % sym
        draw_chart(level, os.path.join(SITE, chart), levels=levels)

        rows = hist_all.get(sym, [])
        row = {"date": j["date"], "close": round(j["close"], 2), "light": j["light"], "title": j["title"]}
        rows = [h for h in rows if h.get("date") != row["date"]] + [row]
        hist_all[sym] = rows[-60:]

        idx = len(tabs)
        tabs.append('<button class="tab%s" data-i="%d">%s<span class="px"><span class="dot d-%s"></span>%.2f</span></button>'
                    % (" on" if idx == 0 else "", idx, name, j["light"], j["close"]))
        panels.append('<div class="panel%s" id="p%d">%s</div>'
                      % (" on" if idx == 0 else "", idx,
                         render(j, level, m30, monthly, rows, gen_at, bt, sym, chart)))
        upg = j.get("upgrade") or {}
        if (upg.get("done") or 0) >= 3:
            alerts.append("%s %d/4 关（起点 %.2f）" % (name, upg["done"], upg.get("lo") or 0))
        brief[sym] = {"name": name, "date": j["date"], "close": round(j["close"], 2),
                      "light": j["light"], "title": j["title"], "line": j["line"],
                      "action": j.get("action") or {},
                      "pos": j["pos"], "zs": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in j["zs"].items()},
                      "sell3": j["sell3"], "buy3": j["buy3"], "retest_n": j["retest_n"],
                      "gaps": j.get("gaps") or [], "vol": j.get("vol") or {},
                      "fresh": j.get("fresh") or {}, "stab": j.get("stab") or {},
                      "ladder": j.get("ladder") or {}, "upgrade": j.get("upgrade") or {},
                      "last_leg": j.get("last_leg") or {},
                      "bt": bt, "gen_at": gen_at}

    # 数据滞后闸门（2026-11-27）：15:35 那趟常拿到 T-1，只出红条仍会把线上覆盖成昨天。
    # 任一指数 lag>0 就不写任何文件、直接退出——保留上一版，发布权交给 16:35 那趟。
    stale = [(v.get("name", k), (v.get("fresh") or {}).get("lag", 0), v.get("date"))
             for k, v in brief.items() if (v.get("fresh") or {}).get("lag", 0) > 0]
    if stale and os.environ.get("CHAN_ALLOW_STALE") != "1":
        print("数据滞后（%s），本轮不发布，保留上一版；16:35 再跑。"
              % "；".join("%s 落后 %d 个交易日（最新 %s）" % (n, lag, d) for n, lag, d in stale),
              file=sys.stderr)
        return

    json.dump(hist_all, open(hpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(brief, open(os.path.join(SITE, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f4f5f7"><title>指数缠论看板</title><style>__CSS__</style></head><body>
<h1>指数缠论看板</h1>
<div class="sub">上证指数 · 创业板指 · 科创50　生成 __GEN__</div>
<div class="tabs">__TABS__</div>
__ALERT__
__PANELS__
<div class="foot">
  数据：日线/30 分钟来自新浪财经（备用腾讯），月线由日线合成；MACD(12,26,9)。<br>
  笔与中枢按"包含处理 → 分型 → 笔 → 笔中枢"机械划分，<b>线段未自动识别、用笔代替次级别走势类型</b>，
  所以"中枢""背驰"都是原文口径的近似，页面内已标注差异。<br>
  回测为每日走查（只用当日及以前数据算信号），以收盘计，未计交易成本；样本期见各页。不构成投资建议。
</div>
<script>
document.querySelectorAll('.tab').forEach(function(b){
  b.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on')});
    document.querySelectorAll('.panel').forEach(function(x){x.classList.remove('on')});
    b.classList.add('on');
    document.getElementById('p'+b.dataset.i).classList.add('on');
    window.scrollTo({top:0,behavior:'smooth'});
  });
});
</script>
</body></html>"""
    alert_html = ""
    if alerts:
        alert_html = ('<div class="alert"><b>5min 级别启动：%s</b><br>'
                      '四关＝5min 向上笔 / 5min 向上段 / 回踩不破 / 30min 中枢；'
                      '3 关＝可轻仓试探（止损取 5min 起点），4 关＝结构升级、才谈加仓。</div>'
                      % "；".join(alerts))
    html = (html.replace("__CSS__", CSS).replace("__GEN__", gen_at)
            .replace("__TABS__", "".join(tabs)).replace("__ALERT__", alert_html)
            .replace("__PANELS__", "".join(panels)))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("OK", gen_at, "|", " ; ".join("%s %s %.2f" % (v["name"], v["light"], v["close"]) for v in brief.values()))


if __name__ == "__main__":
    main()
