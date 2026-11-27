// Cloudflare Worker：方程式新闻快讯 → 飞书群机器人
// 定时（cron）抓 EquationNews，命中关键词的新快讯推送到飞书 webhook。
// 去重靠 KV：记录已推送过的快讯编号。

const REST = "https://api.eqn.news/public/v1/news";
const CHANNELS = ["tradfi", "main"];
const SEEN_KEY = "seen_nos";
const SEEN_KEEP = 600;

const EXCLUDE_KEYWORDS = [
  "永续合约", "保证金", "监控标签", "Launchpool", "HODLer", "空投", "韩元市场",
  "矿工", "挖矿", "哈希率", "hashrate", "矿机", "算力难度",
];

const EXCLUDE_SOURCES = ["binance", "bithumb", "upbit", "listing", "okx", "bybit", "coinbase"];

const CATEGORIES = {
  政治: ["特朗普", "拜登", "万斯", "白宫", "国会", "参议院", "众议院", "选举", "大选", "制裁", "关税",
    "外交部", "总统", "总理", "首相", "战争", "停火", "北约", "普京", "习近平", "泽连斯基",
    "以色列", "伊朗", "乌克兰", "俄罗斯", "实体清单", "出口管制", "行政令", "最高法院",
    "Trump", "Biden", "Vance", "White House", "Congress", "Senate", "election",
    "sanction", "sanctions", "tariff", "tariffs", "president", "prime minister", "ceasefire", "NATO",
    "Putin", "Xi", "Zelensky", "Israel", "Iran", "Ukraine", "Russia", "entity list",
    "export control", "export controls", "executive order", "Supreme Court"],
  经济: ["美联储", "降息", "加息", "利率", "通胀", "CPI", "PCE", "PPI", "非农", "就业", "失业率", "GDP",
    "国债", "收益率", "财政部", "鲍威尔", "央行", "衰退", "美元指数", "黄金", "原油", "美股",
    "标普", "纳斯达克", "纳指", "道指", "人民币", "日元",
    "Fed", "FOMC", "rate cut", "rate hike", "interest rate", "inflation", "nonfarm", "payrolls",
    "jobless", "unemployment", "Treasury", "Treasuries", "yield", "yields", "Powell", "ECB", "BOJ",
    "PBOC", "recession", "DXY", "gold", "oil", "crude", "S&P", "Nasdaq", "Dow"],
  人工智能: ["人工智能", "大模型", "智能体", "开源模型", "深度求索", "具身智能", "人形机器人",
    "AI", "AGI", "LLM", "OpenAI", "ChatGPT", "Anthropic", "Claude", "Gemini", "DeepSeek", "xAI",
    "Grok", "Hugging Face", "Mistral", "Perplexity", "open source model", "open-weight"],
  算力: ["英伟达", "黄仁勋", "芯片", "半导体", "台积电", "英特尔", "博通", "美光", "数据中心", "算力",
    "超算", "昇腾", "光刻", "甲骨文", "显卡",
    "Nvidia", "NVDA", "Jensen Huang", "Huang", "GPU", "GPUs", "chip", "chips", "semiconductor",
    "TSMC", "AMD", "Intel", "Broadcom", "AVGO", "Micron", "HBM", "data center", "data centers",
    "compute", "ASML", "CoreWeave", "Oracle", "Blackwell", "Rubin", "H100", "H200", "H20"],
  产业: ["收购", "并购", "合并", "IPO", "财报", "营收", "净利润", "裁员", "融资", "估值", "首席执行官",
    "CEO", "特斯拉", "苹果", "微软", "谷歌", "亚马逊", "比亚迪", "马斯克", "华为", "小米", "腾讯", "阿里",
    "earnings", "revenue", "acquire", "acquires", "acquisition", "merger", "layoffs", "valuation",
    "funding", "Tesla", "TSLA", "Apple", "AAPL", "Microsoft", "MSFT", "Google", "Alphabet",
    "Amazon", "AMZN", "Meta", "Musk", "SpaceX", "Huawei"],
};

const CJK = /[\u3400-\u9fff]/;

function makeMatcher(words) {
  return words.map((w) => {
    const esc = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = CJK.test(w) ? esc : `(?<![A-Za-z0-9])${esc}(?![A-Za-z0-9])`;
    return new RegExp(pattern, "i");
  });
}

const COMPILED = Object.fromEntries(
  Object.entries(CATEGORIES).map(([k, v]) => [k, makeMatcher(v)])
);
const EXCL = makeMatcher(EXCLUDE_KEYWORDS);

function classify(text, source) {
  const src = (source || "").toLowerCase();
  if (EXCLUDE_SOURCES.some((s) => src.includes(s))) return [];
  if (EXCL.some((re) => re.test(text))) return [];
  return Object.keys(COMPILED).filter((cat) => COMPILED[cat].some((re) => re.test(text)));
}

function overviewOf(item) {
  const ov = item?.ai?.overview;
  if (!ov || ov.status !== "ready") return null;
  return ov.zh?.paragraphs?.[0]?.text || null;
}

function formatMessage(hits) {
  const lines = [`EQN 快讯 · 新命中 ${hits.length} 条`, ""];
  for (const { item, cats } of hits) {
    const t = new Date(item.time).toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
    lines.push(`【${cats.join("·")}】${t}`);
    lines.push((item.zh || item.en || "").trim());
    const ov = overviewOf(item);
    if (ov) lines.push("↳ " + ov);
    if (item.url) lines.push(item.url);
    lines.push("");
  }
  const text = lines.join("\n").trim();
  return text.length > 3800 ? text.slice(0, 3800) + "\n…（已截断）" : text;
}

async function fetchChannel(channel) {
  const url = `${REST}?channel=${channel}&limit=30`;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, { headers: { "User-Agent": "eqn-worker/1.0" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data.items || [];
    } catch (e) {
      if (attempt === 2) {
        console.log(`抓 ${channel} 失败：${e}`);
        return [];
      }
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
    }
  }
  return [];
}

async function run(env) {
  const seenRaw = await env.STATE.get(SEEN_KEY);
  const seen = new Set(seenRaw ? JSON.parse(seenRaw) : []);
  const cutoff = Date.now() - 12 * 3600 * 1000;

  const all = [];
  for (const ch of CHANNELS) {
    for (const item of await fetchChannel(ch)) {
      if (item.no == null || seen.has(item.no)) continue;
      seen.add(item.no);
      if (new Date(item.time).getTime() < cutoff) continue;
      const text = `${item.zh || ""}\n${item.en || ""}`;
      const cats = classify(text, item.source);
      if (cats.length) all.push({ item, cats });
    }
  }

  all.sort((a, b) => new Date(a.item.time) - new Date(b.item.time));
  console.log(`命中 ${all.length} 条`);

  let pushed = false;
  if (all.length) {
    const body = { msg_type: "text", content: { text: formatMessage(all) } };
    const res = await fetch(env.FEISHU_WEBHOOK, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await res.text();
    console.log(`飞书返回 ${res.status} ${out.slice(0, 200)}`);
    pushed = res.ok && out.includes('"code":0');
  }

  const kept = [...seen].slice(-SEEN_KEEP);
  await env.STATE.put(SEEN_KEY, JSON.stringify(kept));
  return { hits: all.length, pushed };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(env));
  },
  async fetch(request, env) {
    if (new URL(request.url).pathname === "/run") {
      const result = await run(env);
      return Response.json(result);
    }
    return new Response("EQN 快讯 worker：定时任务在跑；手动触发访问 /run", { status: 200 });
  },
};
