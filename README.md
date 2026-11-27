# EQN 快讯 → 飞书

定时抓「方程式新闻」快讯，命中政治 / 经济 / 人工智能 / 算力 / 产业关键词的，推送到飞书私聊。

- `eqn_feishu.py`：单文件、零依赖。抓 tradfi + main，过滤、去重、推送。
- `keywords.json`：关键词与排除词，和 macOS 菜单栏版共用同一套规则。
- 在 GitHub Actions 里**手动点一下**就跑一次（定时触发器在这个账号不工作，已移除）。
  日常推送由 Mac 上的菜单栏应用负责，云端这个是备份。

## 本地试跑

```sh
python3 eqn_feishu.py --dry-run      # 只看会推什么
cp .env.example .env                 # 填好凭据后
set -a; . ./.env; set +a
python3 eqn_feishu.py                # 真的推
```

## 凭据

推送走飞书自建应用（`tenant_access_token`），需要三个仓库 Secrets：
`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_USER_ID`。
也可以用群自定义机器人：设置 `FEISHU_WEBHOOK` 即可。
