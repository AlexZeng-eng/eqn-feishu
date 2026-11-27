# EQN 快讯沙箱模板（PPIO / paigod 网关）

在 PPIO 沙箱里常驻运行：每 60 秒抓一次方程式新闻，命中关键词的新快讯推到飞书群。
沙箱自己给自己续期，不需要外部定时器。

## 文件

| 文件 | 作用 |
|---|---|
| `ppio.Dockerfile` | 模板镜像定义 |
| `worker_loop.py` | 常驻进程本体（只用 Python 标准库） |
| `keywords.json` | 关键词与排除词 |
| `create_sandbox.sh` | 模板建好后，用网关 API 建沙箱并注入凭据 |

## 第一步：建模板（这一步现在只能在 paigod 控制台做）

网关对模板创建是关闭的（`sandbox_gateway_provider_root_mutation_disabled`），要由控制台侧建。
把 `ppio.Dockerfile` 的内容贴进去，启动命令填：

```
python3 /app/worker_loop.py
```

建完会得到一个 **templateID**，形如 `tpl_xxxx` 或某个短名字。

## 第二步：建沙箱（我来做，或你自己跑）

```sh
export PAIGOD_TOKEN=...        # 网关 token
export FEISHU_WEBHOOK=...      # 飞书群机器人 webhook
export TEMPLATE_ID=...         # 第一步拿到的
./create_sandbox.sh
```

脚本会用 `envs` 把飞书 webhook、自身沙箱 ID、网关 token 传给容器，容器启动即开始工作，
并且每 20 分钟把自己的超时续到 1 小时，所以不会被回收。

## 运行体行为

- 每 60 秒拉一次 `tradfi` + `main`（约 2 请求/分钟，远低于限流）
- 去重：已推过的编号记在 `.state.json`，最多 600 条
- 只推 12 小时内、命中关键词、且不是纯虚拟币消息的快讯
- 飞书推送失败重试 3 次（1s / 2s / 4s 退避）
- 日志直接打到 stdout，可用 `sandbox logs` 或网关的日志接口看

## 为什么不用 GitHub Actions

实测该仓库的 `schedule` 触发器完全不触发（连最小的对照工作流 20 分钟都没跑），
手动触发正常，说明是定时器被账号级别限制。沙箱方案不依赖任何外部定时器。
