# AI 日报三源融合（ai-daily-merged）

把三个公开 AI 信息源**交叉去重、合并**成一份确定性输出的中文 AI 日报 —— 纯 Python 标准库，零 API key，零第三方依赖。

```
何夕2077 AI资讯日报  ┐
AIHOT 精选 RSS       ├──►  prepare_merged_digest.py  ──►  digest.json  ──►  中文日报（Markdown）
follow-builders feed ┘                                                            └──►  公众号封面 PNG
```

## 为什么做这个

- **确定性优先**：不用泛化搜索、不做"看着像"的摘要。三源抓取 → 结构化 JSON → 按固定版式组织，同样的输入永远得到同样的输出。
- **每天必有产出**：任何单源失败都不阻塞出报（`status=partial` + `errors` 如实报告）；何夕2077 当日未发布时自动回溯最近可用日期。
- **零凭据、零依赖**：仅公开 HTTP 源 + Python 标准库。连封面图都是纯几何绘制，零素材版权风险。
- **去重靠链接集合**：跨源合并同一事件时比对原文链接集合，而不是靠标题相似度猜。

## 三个来源

| 源 | 取数方式 | 内容 |
|---|---|---|
| **何夕2077** | `hex2077.dev` 日报页面（非 RSS） | 产品更新 / 前沿研究 / 行业影响 / 开源项目 / 社媒分享 |
| **AIHOT** | `aihot.news` 精选 RSS | 带分类的条目：模型发布 / 产品 / 行业动态 / 论文 / 观点 |
| **follow-builders** | 上游公开 feed（X / 播客 / 官方博客） | 顶级 AI builder 的一手动态与观点 |

> 三者都是公开发布的信息源，抓取不需要登录、不需要 key。

## 快速开始

```bash
# 1) 抓取三源，融合成 digest.json
python scripts/prepare_merged_digest.py --out digest.json

# 2) 生成当日封面（程序化模式，零素材）
python scripts/make_cover.py --date 2026-09-30 --out cover.png

# 3) 用自己的品牌底图（模板模式）
python scripts/make_cover.py --date 2026-09-30 --template brand.jpg --out cover.png

# 4)（可选）把日报转成发布服务的章节结构，并校验标题/摘要字数
python scripts/build_article.py --md "库目录/AI日报/YYYY-MM-DD.md" \
  --focus "焦点短语" --abstract "摘要" --cover "<封面直链>" --out article.json
```

`digest.json` 就是一个结构化的中间产物（`sources.hex2077` / `sources.aihot` / `sources.followBuilders`），
把它交给任意 LLM 或按 `SKILL.md` 里的版式手工整理，都能得到同一份日报。

### 常用参数

| 参数 | 作用 |
|---|---|
| `--date YYYY-MM-DD` | 目标日期（GMT+8），默认今天 |
| `--window-hours N` | AIHOT 条目时间窗，默认 28 小时 |
| `--hex-lookback-days N` | 何夕2077 回溯天数，默认 3 |
| `--no-follow-builders` | 跳过第三源（离线 / 快速模式） |
| `--out PATH` | 写出 JSON 文件；不传则打印到 stdout |

第三源依赖 [`follow-builders-lite`](https://github.com/zarazhangrui/follow-builders) 的取数脚本，
可通过环境变量 `AI_DAILY_LITE_SCRIPT` 指定路径；未安装时用 `--no-follow-builders` 即可照常出报。

## 输出

日报按固定版式组织（详见 `SKILL.md`）：

```
# AI日报 · 2026年9月30日

> 来源：何夕2077（9/29）· AIHOT（34 条）· follow-builders（18 位 builder / 34 条推文）

🔥 今日要点        （3-5 条一句话摘要 + 链接）
一、… 五、…        （主条目：事件内容 + 为什么值得关注）
今日速览           （低优先级但有价值的条目）
Builder 观点       （按 builder 分块）
来源说明与免责      （必带：来源清单 + 版权提示 + 侵权删除渠道）
```

重点覆盖 **AI Coding / 具身智能 / 端侧 AI**，但不遗漏重大行业事件（模型发布、融资/IPO、监管、智能体安全）。
单一来源或爆料类信息一律标注「待确认/单信源」。

## 封面生成（无版权）

- **程序化模式（默认）**：纯算法绘制渐变底 + 光斑 + 代码流装饰线，900×383（公众号 2.35:1），零第三方素材。
- **模板模式**：自备品牌底图 + 每日日期条（「9月30日 · 星期三」）+ 注脚，适合固定栏目视觉。
- 中文渲染优先用系统字体（Windows 微软雅黑 / macOS 苹方 / Linux Noto·文泉驿）；解释器缺 Pillow 时脚本会自动寻找带 PIL 的解释器重跑自己，实在不行退化为拉丁点阵（可接受但不含中文）。
- 若底图带 AI 生成角标，**请保留** —— 符合 AI 生成内容标识合规要求。

## 可选：推送到微信公众号

技能文档里包含与第三方排版/发布服务对接的可选流程（建稿 → 推送到**草稿箱**）。
它不是必需部分，且需要自备服务与公众号绑定（公众号 API 一般仅认证号可用）。

其中 `build_article.py` 负责把日报 Markdown 转成发布服务的章节 JSON，并硬性校验
标题 ≤30 字 / 摘要 ≤100 字（超限报错退出、不静默截断），同时产出待建稿状态文件防重复建稿。

**红线**：只推草稿箱，**群发永远由人工在公众号后台点击**。

## 自动化

配合 cron / Windows 任务计划 / AI Agent 平台定时执行，建议时间在三个源当日更新之后。
一次运行完成：抓取 → 整理日报 → 写文件 → 生成封面 →（可选）建稿推草稿箱。

## 数据与隐私

- 仓库仅包含**代码与文档**，不含任何个人数据：无账号 ID、无密钥、无 token、无个人路径。
- 生成物（`digest.json`、日报 Markdown、封面 PNG）已通过 `.gitignore` 排除，不会进入版本库。
- 抓取脚本通过环境变量 / 用户目录动态解析路径，不含硬编码用户名。

## 免责声明

本项目是**信息聚合工具**，不是内容生产方：

- 聚合内容的著作权归**原作者与原始媒体**所有；本工具仅做翻译、摘编与交叉比对，不用于商业用途。
- 请在产出的日报中保留原文链接与来源标注，并对单一来源信息标注「待确认」。
- 如权利人认为摘编超出合理使用范围，请通过仓库 issue 联系，我们会配合处理。
- 本工具产出的内容不构成任何投资、法律或其他决策依据。

## License

MIT —— 见 [LICENSE](LICENSE)。
