#!/usr/bin/env python3
"""方程式新闻（EquationNews）快讯 → 飞书推送。给常开的机器/云上用，不需要 macOS。

只依赖 Python 3.8+ 标准库。用法：

  只看会推什么、不真的推：   python3 eqn_feishu.py --dry-run
  真推（webhook 方式）：      FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx python3 eqn_feishu.py
  真推（自建应用方式）：      FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx FEISHU_USER_ID=ou_xxx python3 eqn_feishu.py

每次运行只推「上次运行之后新出现的、命中关键词的」快讯，状态存在 .state.json。
适合放到 cron / GitHub Actions / 任何容器里，每隔几分钟跑一次。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".state.json"
KEYWORDS = HERE / "keywords.json"
REST = "https://api.eqn.news/public/v1/news"
CHANNELS = ["tradfi", "main"]
SEEN_KEEP = 400
MAX_AGE_H = 12


def log(msg):
    print(datetime.now().strftime("%m-%d %H:%M:%S"), msg, flush=True)


def http_json(url, data=None, headers=None, timeout=20):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "eqn-feishu/1.0",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("HTTP %s %s -> %s" % (e.code, url.split("?")[0], detail)) from None


# ---------------------------------------------------------------- 过滤规则

CJK = re.compile(r"[\u3400-\u9fff]")


def compile_words(words):
    out = []
    for w in words:
        if CJK.search(w):
            out.append(re.compile(re.escape(w), re.I))
        else:
            out.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I))
    return out


def load_matcher(path=KEYWORDS):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cats = {k: compile_words(v) for k, v in cfg["categories"].items()}
    excl = compile_words(cfg.get("exclude_keywords", []))
    exsrc = [s.lower() for s in cfg.get("exclude_sources", [])]
    return cfg, cats, excl, exsrc


def classify(text, source, cats, excl, exsrc):
    s = (source or "").lower()
    if any(x in s for x in exsrc):
        return []
    if any(p.search(text) for p in excl):
        return []
    return [name for name, pats in cats.items() if any(p.search(text) for p in pats)]


# ---------------------------------------------------------------- 抓快讯

def fetch(channel, limit=30):
    url = "%s?channel=%s&limit=%d" % (REST, channel, limit)
    last = None
    for attempt in range(3):
        try:
            return http_json(url).get("items", [])
        except Exception as e:
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError("抓 %s 失败：%s" % (channel, last))


def item_text(it):
    return ((it.get("zh") or "") + "\n" + (it.get("en") or "")).strip()


def overview_of(it):
    ov = ((it.get("ai") or {}).get("overview") or {})
    if ov.get("status") != "ready":
        return None
    paras = ((ov.get("zh") or {}).get("paragraphs") or [])
    return paras[0]["text"] if paras else None


# ---------------------------------------------------------------- 状态

def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"no": 0, "seen": []}


def save_state(state):
    state["seen"] = state["seen"][-SEEN_KEEP:]
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 推送

def push_webhook(url, text):
    r = http_json(url, {"msg_type": "text", "content": {"text": text}})
    code = (r.get("code") if isinstance(r, dict) else None)
    if code not in (0, None):
        raise RuntimeError("飞书 webhook 返回 %s" % json.dumps(r, ensure_ascii=False)[:200])


_tat = {"token": None, "exp": 0}


def tenant_token(app_id, app_secret):
    if _tat["token"] and time.time() < _tat["exp"] - 60:
        return _tat["token"]
    r = http_json("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                  {"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise RuntimeError("取 tenant_access_token 失败：%s" % r)
    _tat["token"] = r["tenant_access_token"]
    _tat["exp"] = time.time() + int(r.get("expire", 7200))
    return _tat["token"]


def push_app(app_id, app_secret, user_id, text):
    last = None
    for attempt in range(3):
        try:
            tok = tenant_token(app_id, app_secret)
            body = {"receive_id": user_id, "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False)}
            r = http_json("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
                          body, {"Authorization": "Bearer " + tok})
            if r.get("code") != 0:
                raise RuntimeError("飞书返回 %s" % json.dumps(r, ensure_ascii=False)[:200])
            return
        except Exception as e:
            last = e
            wait = 2 ** attempt
            log("推送第 %d 次失败：%s（%d 秒后重试）" % (attempt + 1, e, wait))
            time.sleep(wait)
    raise RuntimeError("飞书推送三次都失败：%s" % last)


def format_message(hits):
    lines = ["EQN 快讯 · 新命中 %d 条" % len(hits), ""]
    for it, cats in hits:
        t = datetime.fromisoformat(it["time"].replace("Z", "+00:00")).astimezone()
        head = "【%s】%s" % ("·".join(cats), t.strftime("%m-%d %H:%M"))
        lines.append(head)
        lines.append((it.get("zh") or it.get("en") or "").strip())
        ov = overview_of(it)
        if ov:
            lines.append("↳ " + ov)
        if it.get("url"):
            lines.append(it["url"])
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > 3800:
        text = text[:3800] + "\n…（内容过长，已截断）"
    if "EQN" not in text:
        text = "EQN 快讯\n" + text
    return text


# ---------------------------------------------------------------- 主流程

def main():
    dry = "--dry-run" in sys.argv
    cfg, cats, excl, exsrc = load_matcher()
    state = load_state()
    seen = set(state.get("seen", []))
    max_no = int(state.get("no", 0))
    cutoff = time.time() - MAX_AGE_H * 3600

    fresh = []
    for ch in CHANNELS:
        try:
            items = fetch(ch)
        except Exception as e:
            log("跳过 %s：%s" % (ch, e))
            continue
        for it in items:
            no = it.get("no")
            if no is None or no in seen:
                continue
            ts = datetime.fromisoformat(it["time"].replace("Z", "+00:00")).timestamp()
            if ts < cutoff:
                continue
            hit = classify(item_text(it), it.get("source"), cats, excl, exsrc)
            if hit:
                fresh.append((it, hit))
            seen.add(no)
            max_no = max(max_no, no)

    fresh.sort(key=lambda x: x[0]["time"])
    if not fresh:
        log("没有新的命中快讯（记录到 #%d）" % max_no)
    else:
        log("新命中 %d 条" % len(fresh))
        text = format_message(fresh)
        if dry:
            print("\n---- 会推送的内容 ----\n" + text + "\n----------------------")
        else:
            hook = os.environ.get("FEISHU_WEBHOOK")
            aid = os.environ.get("FEISHU_APP_ID")
            secret = os.environ.get("FEISHU_APP_SECRET")
            uid = os.environ.get("FEISHU_USER_ID")
            if hook:
                push_webhook(hook, text)
                log("已通过群机器人 webhook 推送")
            elif aid and secret and uid:
                push_app(aid, secret, uid, text)
                log("已通过自建应用推送到 %s" % uid)
            else:
                log("没配推送方式：需要 FEISHU_WEBHOOK，或 FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_USER_ID")
                return 2

    state["no"] = max_no
    state["seen"] = list(seen)
    if not dry:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
