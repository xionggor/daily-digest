# 今日视界 · 每日资讯简报

一个零成本、全自动的静态博客：每天定时抓取科技 / AI / 世界大事的公开 RSS，生成一份可直接访问的资讯页面。

## 目录结构

```
index.html                 # 生成出来的页面（数据已内联，单文件可直接打开）
template.html              # 页面模板，改样式改这里
fetch.py                   # 抓取 + 清洗 + 去重 + 渲染，只用标准库
sources.json               # 数据源配置：源地址、分类、站点信息
data.json                  # 生成的数据快照（方便二次开发 / 做接口）
archive/                   # 往期归档：每天一份 YYYY-MM-DD.json + 索引 index.json
.github/workflows/daily.yml# 每天定时执行并提交
```

## 本地预览

```bash
python fetch.py          # 抓取并生成 index.html
```

然后直接双击 `index.html` 或拖进浏览器即可。常用参数：

```bash
python fetch.py --days 3 --total 80 --per-source 5
```

- `--days` 只保留最近 N 天
- `--total` 总条数上限
- `--per-source` 单个源最多取几条

## 部署到线上（约 5 分钟）

只有一条硬要求：仓库必须是 **Public**。私有仓库开 Pages 需要付费的 GitHub Pro。

### 路线 A：只用 GitHub（推荐，不用注册第二个平台）

1. 新建一个空仓库（Public，**不要**勾选 Initialize with README），拿到地址。
2. 在本地项目目录执行：

```bash
git init -b main
git add .
git commit -m "init daily digest"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

首次 push 会弹出浏览器让你登录 GitHub，授权一次即可。

3. 仓库 Settings → Pages → Source 选 `Deploy from a branch`，Branch 选 `main`，目录选 `/ (root)`，保存。
4. 到 Actions 页签点 `Update daily digest` → `Run workflow` 手动跑一次，确认能正常提交。
5. 一两分钟后访问 `https://<你的用户名>.github.io/<仓库名>/`。

根目录那个 `.nojekyll` 空文件别删，它让 Pages 跳过 Jekyll 处理，直接发布原始文件。

没有装 Git 也能做：仓库首页 Add file → Upload files，把 6 个文件拖进去提交；然后 Create new file 手动建 `.github/workflows/daily.yml` 和 `.nojekyll` 两个文件（文件名直接输入带斜杠的路径即可）。

### 路线 B：GitHub 存代码 + Cloudflare Pages 发布

1. 按路线 A 的第 1、2 步把代码推上去。
2. Cloudflare Dashboard → Workers & Pages → Create → Pages → 连接 Git 仓库，选中这个仓库，
   框架预设选 **None**，构建命令留空，输出目录填 `/`，保存部署。
3. 之后每次 Actions 提交新的 `index.html`，Cloudflare Pages 自动重新部署。

## 定时时间

在 `.github/workflows/daily.yml` 里改 cron（UTC 时间）：

```yaml
- cron: "0 22 * * *"   # UTC 22:00，北京 06:00 / 纽约 17:00
```

北京时间想早上 7 点更新，就写 `0 23 * * *`。

## 增删数据源

编辑 `sources.json`，往 `sources` 数组里加一行：

```json
{ "name": "某站点", "category": "tech", "url": "https://example.com/feed" }
```

- `category` 只能是 `tech` / `ai` / `market` / `world`，对应页面上的四个分类。
- 站点没有 RSS 时，可以先用 [RSSHub](https://docs.rsshub.app/) 把页面转成 RSS 再加进来。
- 证书异常的源（如 arXiv）可以加 `"insecure": true` 跳过校验。

加完跑一次 `python fetch.py`，控制台会逐个打印每个源的成功 / 失败原因，按提示剔除即可。

## 开源精选（GitHub 项目栏）

页面头部的「开源精选」来自 GitHub Search API，取最近 N 天新建、star 超过阈值的热门仓库：

```json
"github": { "enabled": true, "days": 14, "min_stars": 60, "limit": 9 }
```

- `days` / `min_stars` 控制新鲜度和门槛，想更"冷门优质"就把 star 门槛调高，想更热闹就调低。
- 无需 API key；在 GitHub Actions 里会带上 `GITHUB_TOKEN`，配额更高。本地不配也能跑。
- 抓取失败只会让这个栏目消失，不影响资讯列表。

## 统一翻译

搜索框右下角有一个「译全文」按钮：点一下把当前视图里所有英文标题和摘要一次译成中文，按钮上显示百分比进度，完成后变成「原文」，再点恢复英文。

- 先尝试浏览器内置的 **Translator API**（Chrome 138+，本地模型，装好语言包后可离线）；2 秒内拿不到就回退在线接口，不会卡住页面；
- 在线接口走 Google 的免费翻译端点，批量 POST，无需 key；
- 译文按段落缓存在 `localStorage`，重复点击不会重复请求；
- 只翻译当前视图：在「GitHub」页签下译的是项目简介。

## 股票频道

「股票」页签分两块：

- **行情快照**：12 个标的（标普 500、纳斯达克、道琼斯、VIX、美债 10 年、恒生、上证、日经 225、黄金、原油、比特币、美元指数），数据来自 Yahoo Finance 的公开行情端点，构建页面时抓取。涨跌按国际惯例绿涨红跌。
- **财经资讯**：CNBC Markets、MarketWatch、Investing.com、Seeking Alpha 四个源，覆盖财报、个股异动、宏观政策。战争、地缘这类会冲击市场的消息在「世界大事」里，两个页签配合看。

改标的就编辑 `sources.json` 的 `quotes` 数组，`symbol` 用 Yahoo 的代码（如 `^GSPC`、`GC=F`、`BTC-USD`）。行情是构建时的快照，不是实时盘口。

### 影响市场标记

构建时会给「世界大事」里的地缘与宏观条目做关键词匹配（战争、袭击、制裁、关税、降息、石油供应、供应链中断等），命中的条目标上红色「影响市场」小标签，并**同时出现在股票页签里**——这就是「哪些大事会砸盘」的入口，不用来回切页签。

关键词表在 `fetch.py` 的 `MARKET_RE` / `MARKET_CN` 里，觉得误判就删词、漏判就加词：

```python
MARKET_CN = ["制裁", "关税", "降息", "加息", "战争", "冲突", "袭击", ...]
```

命中数量会在构建日志里打印（`完成：140 条（其中 5 条标记为影响市场）`），方便你判断阈值合不合适。

## 往期归档

每天构建时会把当天的条目另存一份 `archive/YYYY-MM-DD.json`，并更新 `archive/index.json` 的日期列表，默认保留最近 45 天。页面上因此会多出一个「往期」页签：

- 页签里出现一排日期胶囊，点哪天看哪天，内容按需加载（只拉那一份 JSON，不会一次载入全部历史）；
- 归档页签只能看历史，点「刷新」会提示「往期不可刷新」；
- 归档只在有归档文件时才出现，本地用 `file://` 打开看不到（浏览器禁止本地 fetch），要看就往本地起个服务：

```bash
python -m http.server 8000
```

然后访问 `http://localhost:8000/`。

## 手动刷新

右上角「刷新」按钮会按当前页签实时抓一遍源（走公共 CORS 代理 allorigins），新条目带红「新」标记，右上角时间同步更新。

- 「全部」页签会从每个分类各取 2 个源，避免只刷到科技；
- 在「股票」页签刷新时，行情卡也跟着拉一次最新价；
- 「往期」页签不联网刷新，只换日期看历史；
- 只改浏览器里的内存数据，不动仓库文件，重新载入页面就回到每日构建的版本；
- 代理限流或网络不通时按钮提示「刷新失败」，不影响已加载的内容。

## 已知说明

- 页面内容全部来自第三方公开 RSS，只保留标题与摘要，点击跳转原文，版权归原站点。
- GitHub Actions 的定时任务在仓库连续 60 天无提交后会被自动停用，收到邮件提醒去仓库点一下即可恢复。
- 抓取失败（网络波动、站点限流）不会影响线上内容：脚本内置重试，且当有效条目少于 10 条时保留上一版页面不覆盖。
