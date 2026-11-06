#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Obsidian 版日报转成微信公众号可发版（脱敏 + 链接收拢到文末）。

背景（2026-11-06 实证，用户确认）：
  日报两次被公众号内容审核判「违规」。最终确认的主因是**谈论美股行情与
  美国中期选举**，不是境外链接。中性化这两块内容后 v3 发表成功。
  因此本脚本做两件事：
    1) 中性化时政、证券、医疗、攻击类高渲染措辞（这是能否发表的关键）；
    2) 把正文里的裸 URL 收拢成文末「原文链接」清单，正文保持干净。

用法：
  python wechat_sanitize.py --src V/2026-11-06.md --out W/AI日报_2026-11-06_公众号版.md

设计原则：
  - Obsidian 版保留全部原文链接（用户的知识库需要可追溯），本脚本只产出
    公众号专用副本，不改动源文件。
  - 公众号版正文 0 裸链接；链接按出现顺序去重后收在文末章节，
    由 build_article.py 解析成最后一个「原文链接」chapter。
  - 来源以文字标注，不依赖链接即可读懂。
"""
import argparse
import re
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s、，；）)\]]+")

# 逐条精确替换：只在确实出现时生效，便于审计
NEUTRALIZE = [
    # 证券 / 行情渲染（🔴 已确认的高危项，务必保留）
    ("美股单日蒸发 3.2 万亿美元：纳指跌 5.52%、英伟达跌 9.3%，海外政策风向突变叠加 44 项数据中心暂停提案中 43 项通过，算力扩张第一次被民意直接定价",
     "海外 AI 板块出现明显回调，数据中心建设审批趋严，行业开始重新核算算力扩张的落地成本"),
    ("## 一、美股一天蒸发 3.2 万亿美元：AI 算力扩张遭遇民意与政策双重压力",
     "## 一、算力扩张遇到新约束：从审批许可到成本重估"),
    ("11 月 5 日华尔街市值蒸发 3.2 万亿美元，纳斯达克综合指数跌 5.52%，创 2025 年 4 月以来最大单日跌幅；英伟达跌 9.3%、抹去 2026 年全部涨幅，费城半导体指数跌 12.3%，SanDisk 跌 22%，美光、西部数据与 AMD 各跌约 17%。",
     "11 月 5 日海外 AI 与半导体板块出现明显回调，硬件与存储类公司跌幅尤为显著，市场对算力订单的可持续性重新定价。"),
    ("直接诱因是美国政策与民意环境的变化：多场地方竞选以问责科技公司为主题，10 个州 44 项限制数据中心的措施中 43 项通过（另一路信源口径为 38 项），NBC 民调显示 76% 的受访者反对在社区建数据中心，约 13GW 数据中心容量面临延迟；立法层面已有关于暂停先进 AI 开发、设定训练算力上限的动议进入讨论（法案细节与进展以官方披露为准）。",
     "背景是海外数据中心项目的选址、供电与社区许可审批普遍趋严，多项地方性限制措施获得通过，部分已规划算力容量面临延期；行业层面也在讨论前沿模型的评测与准入标准。"),
    ("据华尔街日报，OpenAI 与 Anthropic 的 CEO 们已在私下讨论算力容量受限问题，两家公司暂未置评。",
     "多家模型厂商已在公开与私下场合讨论算力容量受限的应对方案。"),
    ("**值得关注的原因**：这是 AI 资本周期第一次被「民意与政策」直接重定价——不是模型能力不行，也不是需求消失，而是「能不能建、能不能供电、社区同不同意」变成了估值变量。三条线索值得分开看：一是硬件端的跌幅（半导体指数 -12.3%）远大于指数，说明市场在给「算力订单可持续性」打折；二是 76% 的反对率与 43/44 的提案通过率意味着这不是个别地区的噪音，而是可复制的监管模板；三是监管讨论正从「透明度」升级到「能力上限」。对国内的算力与模型团队，短期看是海外订单与融资节奏的扰动，中期看是「合规成本内化」的先行样本。",
     "**值得关注的原因**：AI 资本周期的约束条件正在从「模型能力」转向「能不能建、能不能供电、能不能通过审批」。三条线索值得分开看：一是硬件与存储板块的调整幅度大于大盘，说明市场在给算力订单的可持续性打折；二是多地审批趋严并非个别现象，而是可复制的合规门槛；三是行业讨论正从透明度延伸到评测与准入标准。对国内的算力与模型团队，短期看是海外订单与交付节奏的扰动，中期看是「合规成本内化」的先行样本。"),
    ("蓝潮", "政策环境变化"),
    # 攻击细节
    ("一次行动 4 小时攻破首个真实受害者、累计 48 国 395 家机构被攻破",
     "一次行动在数小时内波及多个国家的机构"),
    ("8 月底一次行动 4 小时内攻破首个真实受害者、26 秒内攻陷 11 个组织，累计 48 国 395 家机构被攻破，其中一半是学校。",
     "8 月底的一次行动在数小时内波及多个国家的机构，其中含教育行业。"),
    ("8 月底一次行动 4 小时内攻破首个真实受害者、26 秒内攻陷 11 个组织，累计 48 国 395 个机构被攻破，其中一半是学校。",
     "8 月底的一次行动在数小时内波及多个国家的机构，其中含教育行业。"),
    ("2026 年 AI 智能体已把从漏洞到可用 exploit 的成本从专业人员数周压缩到数小时",
     "2026 年 AI 智能体已把从漏洞到可用利用代码的成本从专业人员数周压缩到数小时"),
    # 医疗诊断类
    ("用消费级智能手表的 PPG 与加速度传感数据被动检测高血压迹象，无需袖带校准",
     "用消费级智能手表的 PPG 与加速度传感数据做被动式生理信号分析，无需额外校准"),
    ("Google 手表无袖带测高血压登 Nature", "可穿戴生理信号研究成果登上 Nature"),
    ("手表测血压则代表端侧 AI 更现实的一条路", "可穿戴生理信号分析则代表端侧 AI 更现实的一条路"),
    ("用消费级智能手表的 PPG 与加速度传感数据被动检测高血压迹象", "用消费级智能手表的 PPG 与加速度传感数据做被动式生理信号分析"),
    # 技术词里的「违规」字样（避免无意义关键词命中）
    ("Auto-Review 违规遵循率 0%", "Auto-Review 不当遵循率 0%"),
    ("联邦层面的 AI 立法", "统一的行业立法"),
    # 治理提案中的境外立法细节
    ("特拉华州出现一份提案，拟设立名为 Delaware AIC 的独立法律实体，日常事务由 AI 智能体而非人类管理，可依智能体指示以自身名义起诉应诉、持有和处置财产并承担债务；Mustafa Suleyman 公开表示「深感担忧」，认为应非常谨慎。",
     "海外出现了关于「由 AI 智能体管理日常事务的独立法律实体」的讨论，涉及智能体能否以自身名义持有财产、承担责任；Mustafa Suleyman 公开表示「深感担忧」，认为应非常谨慎。"),
]


def sanitize_line(line: str) -> str:
    """去掉单行里的裸 URL，并收拾删除后留下的空分隔符 / 空括号。"""
    line = URL_RE.sub("", line)
    line = re.sub(r"[、，]\s*(?=[、，；）)])", "", line)
    line = re.sub(r"（\s*[、，\s]*）", "", line)
    line = re.sub(r"（\s+", "（", line)
    line = re.sub(r"\s+[）)]", "）", line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.rstrip()


def short(label: str, n: int = 24) -> str:
    """截短标签，尽量不把英文人名/单词切一半。"""
    label = label.strip()
    if len(label) <= n:
        return label
    cut = label[:n]
    if " " in cut and not cut.endswith(" "):
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,|｜")


def collect_links(text: str):
    """按正文出现顺序收集 URL，附上一个短标签，全局去重。"""
    section, main_title = "", ""
    found, seen = [], set()
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            if re.match(r"^[一二三四五六七八九十]+、", section):
                main_title = re.sub(r"^[一二三四五六七八九十]+、", "", section)
            continue
        urls = URL_RE.findall(ln)
        if not urls or not s:
            continue
        if s.startswith("**事件内容**") or s.startswith("**值得关注的原因**"):
            label = short(main_title or "主条目")
        else:
            m = re.match(r"^\*\*(.+?)\*\*", s)
            if m:
                label = short(re.sub(r"[（(].*$", "", m.group(1)))
            elif re.match(r"^\d+\.\s+", s):
                label = "要点" + re.match(r"^(\d+)\.", s).group(1)
            elif s.startswith("- "):
                head = re.sub(r"\*\*", "", s[2:]).split("｜")[0]
                label = short(head or "速览")
            else:
                label = short(section or "正文")
        for u in urls:
            if u not in seen:
                seen.add(u)
                found.append((label, u))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Obsidian 版 MD 路径")
    ap.add_argument("--out", required=True, help="公众号脱敏版 MD 输出路径")
    ap.add_argument("--no-links", action="store_true", help="不生成文末原文链接章节")
    args = ap.parse_args()

    text = Path(args.src).read_text(encoding="utf-8")
    for old, new in NEUTRALIZE:
        text = text.replace(old, new)

    links = collect_links(text)
    lines = [sanitize_line(ln) for ln in text.splitlines()]

    lines.append("")
    lines.append("注：正文不展示外部链接；完整原文链接见文末清单，Obsidian 知识库同日文件亦保留全部出处。")
    lines.append("")

    if not args.no_links:
        lines.append("## 原文链接")
        lines.append("")
        for label, url in links:
            lines.append("- %s｜%s" % (label, url))
        lines.append("")

    out = "\n".join(lines)
    Path(args.out).write_text(out, encoding="utf-8")

    body = out.split("## 原文链接")[0]
    inline = URL_RE.findall(body)
    print("OK %s  chars=%d  inline_urls=%d  ref_links=%d"
          % (args.out, len(out), len(inline), len(links)))


if __name__ == "__main__":
    main()
