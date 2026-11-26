#!/bin/bash
# 观察脚本：每 60 秒采样一次，把两边状态追加到 watch.log
cd "$(dirname "$0")" || exit 1
export XDG_CACHE_HOME="$PWD/.ghcache"
OUT="watch.log"
for i in $(seq 1 "${1:-20}"); do
  {
    echo "---- $(date '+%H:%M:%S') 第 ${i} 次 ----"
    echo -n "云端最近运行: "
    gh run list -R AlexZeng-eng/eqn-feishu --limit 3 --json event,conclusion,createdAt \
      -q '.[] | "\(.event):\(.conclusion // "running")"' 2>&1 | tr '\n' ' '
    echo
    echo -n "Mac 进程: "; pgrep -f EQNAlert >/dev/null && echo "在跑" || echo "❌停了"
    echo -n "Mac 日志尾: "; tail -1 "$HOME/Library/Application Support/EQNAlert/run.log" 2>/dev/null
    echo -n "命中总数: "; wc -l < "$HOME/Library/Application Support/EQNAlert/hits.jsonl" 2>/dev/null
  } >> "$OUT"
  sleep 60
done
echo "DONE" >> "$OUT"
