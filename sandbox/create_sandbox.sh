#!/bin/bash
# 用网关 API 建一个跑本模板的沙箱，并把凭据注入进去
set -euo pipefail
: "${PAIGOD_TOKEN:?需要 PAIGOD_TOKEN}"
: "${FEISHU_WEBHOOK:?需要 FEISHU_WEBHOOK}"
: "${TEMPLATE_ID:?需要 TEMPLATE_ID（在 paigod 控制台建好模板后得到）}"

BASE="${GATEWAY_BASE:-https://apiproxy.paigod.work}"
CHANNEL="${GATEWAY_CHANNEL:-ppio-sandbox-internal}"

BODY=$(python3 - "$FEISHU_WEBHOOK" "$PAIGOD_TOKEN" "$TEMPLATE_ID" <<'PY'
import json, sys
hook, token, tpl = sys.argv[1:4]
print(json.dumps({
    "templateID": tpl,
    "timeout": 3600,
    "autoPause": False,
    "metadata": {"role": "eqn-worker"},
    "envs": {
        "FEISHU_WEBHOOK": hook,
        "GATEWAY_TOKEN": token,
        "GATEWAY_BASE": "https://apiproxy.paigod.work",
        "GATEWAY_CHANNEL": "ppio-sandbox-internal",
        "POLL_INTERVAL": "60",
    },
}, ensure_ascii=False))
PY
)

echo "创建沙箱…"
curl -sS -X POST "$BASE/sandbox/c/$CHANNEL/sandboxes" \
  -H "Authorization: Bearer $PAIGOD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" | tee /tmp/eqn_sandbox.json
echo
echo "沙箱列表："
curl -sS "$BASE/sandbox/c/$CHANNEL/sandboxes" -H "Authorization: Bearer $PAIGOD_TOKEN"
