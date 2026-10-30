# AI 日报三源融合（ai-daily-merged）

把三个公开 AI 信息源（何夕2077 / AIHOT / follow-builders）**交叉去重、合并**成一份确定性输出的中文 AI 日报 —— 纯 Python 标准库，零 API key，零第三方依赖。三源 → `prepare_merged_digest.py` → `digest.json` → 日报 Markdown + 封面 PNG。
## 为什么做这个

- **确定性优先**：不用泛化搜索、不做"看着像"的摘要。
- **每天必有产出**：任何单源失败都不阻塞出报；何夕2077 当日未发布时自动回溯。
- **零凭据、零依赖**：仅公开 HTTP 源 + Python 标准库。连封面图都是纯几何绘制，零素材版权风险。
## 快速开始

```bash
python scripts/prepare_merged_digest.py --out digest.json
python scripts/make_cover.py --date 2026-09-30 --out cover.png
python scripts/build_article.py --md "AI日报/YYYY-MM-DD.md" \
  --focus "焦点短语" --abstract "摘要" --cover "<封面直链>"
```
脚本参数用 `--help` 查看。第三源依赖 `follow-builders-lite` 的取数脚本，
可通过环境变量 `AI_DAILY_LITE_SCRIPT` 指定路径；未安装时用 `--no-follow-builders` 照常出报。

## 输出

日报按固定版式组织，覆盖 AI Coding / 具身智能 / 端侧 AI，详见 `SKILL.md`。
## 封面生成

封面脚本（`scripts/make_cover.py`）支持两种模式：**程序化模式**（纯几何绘制，零素材版权风险）和**模板模式**（自备品牌底图 + 每日日期条）。详细说明见 `SKILL.md`。

## 可选：推送到微信公众号

技能文档（`SKILL.md`）里有与发布服务对接的完整可选流程（授权 → 直连 MCP → 上传封面 → 建稿 → 推草稿箱）。
`build_article.py` 把日报 MD 转成发布章节 JSON，`yanxu_auth.py` / `yanxu_mcp.py` / `yanxu_publish.py` 实现独立授权与直连通道。
**红线**：只推草稿箱，群发永远由人工在公众号后台点击。

## 自动化

配合 cron / Windows 任务计划 / AI Agent 平台定时执行。

## 免责声明

本工具为信息聚合，著作权归原作者；产出不构成投资/法律决策依据。

MIT License —— 见仓库 `LICENSE` 文件。
