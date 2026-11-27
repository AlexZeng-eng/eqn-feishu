#!/usr/bin/env python3
"""沙箱内的常驻进程：抓方程式新闻快讯 → 筛选 → 推飞书，并定期给自己续命。

需要的环境变量（在创建沙箱时通过 envs 传入，不要写进镜像）：
  FEISHU_WEBHOOK   飞书群机器人 webhook（必填）
  POLL_INTERVAL    轮询间隔秒，默认 60
  SANDBOX_ID       本沙箱 ID（填了才会自动续期）
  GATEWAY_TOKEN    调用网关的 token（续期用；和 SANDBOX_ID 一起才生效）
  GATEWAY_CHANNEL  默认 ppio-sandbox-internal
  GATEWAY_BASE     默认 https://apiproxy.paigod.work
"""
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("STATE_FILE", str(HERE / ".state.json")))
REST = "https://api.eqn.news/public/v1/news"
CHANNELS = ["tradfi", "main"]
SEEN_KEEP = 600
MAX_AGE_H = 12
RENEW_EVERY = 20 * 60          # 每 20 分钟给自己续一次期
RENEW_TIMEOUT = 3600           # 每次续到 1 小时

running = True


def stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def log(msg):
    print(datetime.now(timezone.utc).strftime("%m-%d %H:%M:%SZ"), msg, flush=True)


def http_json(url, data=None, headers=None, timeout=25):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "eqn-sandbox/1.0",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s %s -> %s" % (e.code, url.split("?")[0],
                                                 e.read().decode("utf-8", "replace")[:200])) from None


# ---------------------------------------------------------------- 过滤

CJK = re.compile(r"[\u3400-\u9fff]")


def compile_words(words):
    out = []
    for w in words:
        if CJK.search(w):
            out.append(re.compile(re.escape(w), re.I))
        else:
            out.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I))
    return out


def load_matcher():
    cfg = json.loads((HERE / "keywords.json").read_text(encoding="utf-8"))
    cats = {k: compile_words(v) for k, v in cfg["categories"].items()}
    return cats, compile_words(cfg.get("exclude_keywords", []))


CATS, EXCL = load_matcher()


def classify(text, source):
    src = (source or "").lower()
    if any(p.search(text) for p in EXCL):
        return []
    return [name for name, pats in CATS.items() if any(p.search(text) for p in pats)]


def overview_of(item):
    ov = ((item.get("ai") or {}).get("overview") or {})
    if ov.get("status") != "ready":
        return None
    paras = ((ov.get("zh") or {}).get("paragraphs") or [])
    return paras[0]["text"] if paras else None


def format_message(hits):
    lines = ["EQN 快讯 · 新命中 %d 条" % len(hits), ""]
    for item, cats in hits:
        t = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        lines.append("【%s】%s" % ("·".join(cats), t.astimezone().strftime("%m-%d %H:%M")))
        lines.append((item.get("zh") or item.get("en") or "").strip())
        ov = overview_of(item)
        if ov:
            lines.append("↳ " + ov)
        if item.get("url"):
            lines.append(item["url"])
        lines.append("")
    text = "\n".join(lines).strip()
    return text[:3800] + "\n…（已截断）" if len(text) > 3800 else text


# ---------------------------------------------------------------- 抓取与状态

def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": []}


def save_state(state):
    try:
        STATE.write_text(json.dumps({"seen": state["seen"][-SEEN_KEEP:]}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log("状态写入失败：%s" % e)


def fetch_channel(channel):
    url = "%s?channel=%s&limit=30" % (REST, channel)
    for attempt in range(3):
        try:
            return http_json(url).get("items", [])
        except Exception as e:
            if attempt == 2:
                log("抓 %s 失败：%s" % (channel, e))
                return []
            time.sleep(1 + attempt)
    return []


def one_round(state):
    seen = set(state["seen"])
    cutoff = time.time() - MAX_AGE_H * 3600
    hits = []
    for ch in CHANNELS:
        for item in fetch_channel(ch):
            no = item.get("no")
            if no is None or no in seen:
                continue
            seen.add(no)
            ts = datetime.fromisoformat(item["time"].replace("Z", "+00:00")).timestamp()
            if ts < cutoff:
                continue
            cats = classify(((item.get("zh") or "") + "\n" + (item.get("en") or "")), item.get("source"))
            if cats:
                hits.append((item, cats))
    state["seen"] = list(seen)
    hits.sort(key=lambda h: h[0]["time"])
    return hits


def push(text):
    hook = os.environ.get("FEISHU_WEBHOOK")
    if not hook:
        log("没配 FEISHU_WEBHOOK，跳过推送")
        return False
    for attempt in range(3):
        try:
            r = http_json(hook, {"msg_type": "text", "content": {"text": text}})
            if r.get("code") == 0:
                return True
            raise RuntimeError(json.dumps(r, ensure_ascii=False)[:200])
        except Exception as e:
            log("推送第 %d 次失败：%s" % (attempt + 1, e))
            time.sleep(2 ** attempt)
    return False


def _gateway():
    base = os.environ.get("GATEWAY_BASE", "https://apiproxy.paigod.work")
    ch = os.environ.get("GATEWAY_CHANNEL", "ppio-sandbox-internal")
    tok = os.environ.get("GATEWAY_TOKEN")
    return base, ch, tok


def _find_own_id():
    """沙箱创建时还拿不到自己的 ID，第一次续期时通过列表反查（按 metadata 标记或唯一性）。"""
    base, ch, tok = _gateway()
    if not tok:
        return None
    auth = {"Authorization": "Bearer " + tok}
    try:
        data = http_json("%s/sandbox/c/%s/sandboxes" % (base, ch), headers=auth)
    except Exception as e:
        log("查沙箱列表失败：%s" % e)
        return None
    items = data if isinstance(data, list) else (data.get("sandboxes") or [])
    mine = [x for x in items if isinstance(x, dict) and
            str((x.get("metadata") or {}).get("role", "")) == "eqn-worker"]
    pick = mine[0] if mine else (items[0] if len(items) == 1 else None)
    if not pick:
        return None
    for k in ("sandboxID", "sandbox_id", "sandboxId", "id"):
        if pick.get(k):
            log("认出自己的沙箱：%s" % pick[k])
            return str(pick[k])
    return None


def renew():
    base, ch, tok = _gateway()
    if not tok:
        return
    sid = os.environ.get("SANDBOX_ID") or _find_own_id()
    if not sid:
        log("还没认出自己的沙箱 ID，跳过续期")
        return
    url = "%s/sandbox/c/%s/sandboxes/%s/timeout" % (base, ch, sid)
    try:
        http_json(url, {"timeout": RENEW_TIMEOUT}, {"Authorization": "Bearer " + tok})
        log("已续期 %d 秒" % RENEW_TIMEOUT)
    except Exception as e:
        log("续期失败：%s" % e)


def main():
    log("启动，轮询间隔 %ss" % os.environ.get("POLL_INTERVAL", "60"))
    state = load_state()
    log("状态里已有 %d 条已读" % len(state["seen"]))
    interval = max(20, int(os.environ.get("POLL_INTERVAL", "60")))
    if os.environ.get("EQN_ONCE") == "1":
        hits = one_round(state)
        log("单次模式：命中 %d 条" % len(hits))
        if hits and push(format_message(hits)):
            log("已推送到飞书")
        save_state(state)
        return
    last_renew = 0.0
    while running:
        started = time.time()
        try:
            hits = one_round(state)
            if hits:
                log("命中 %d 条" % len(hits))
                if push(format_message(hits)):
                    log("已推送到飞书")
                save_state(state)
            if time.time() - last_renew > RENEW_EVERY:
                renew()
                last_renew = time.time()
        except Exception as e:
            log("本轮出错：%s" % e)
        elapsed = time.time() - started
        time.sleep(max(5, interval - elapsed))
    save_state(state)
    log("收到停止信号，退出")


if __name__ == "__main__":
    main()
