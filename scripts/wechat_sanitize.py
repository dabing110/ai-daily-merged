#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Obsidian 版日报转成微信公众号可发版（脱敏 + 参考文献式链接）。

背景（2026-11-06 实证，用户确认）：
  日报两次被公众号内容审核判「违规」。最终确认的主因是**谈论美股行情与
  美国中期选举**，不是境外链接。中性化这两块内容后 v3 发表成功。
  因此本脚本做两件事：
    1) 中性化时政、证券、医疗、攻击类高渲染措辞（这是能否发表的关键）；
    2) 把正文里的 markdown 链接 [text](url) 转成阮一峰周刊式引用：
       正文「text[n]」+ 文末「## References」清单（2026-11-13 用户指定格式）。

用法：
  python wechat_sanitize.py --src V/2026-11-06.md --out W/AI日报_2026-11-06_公众号版.md

设计原则：
  - Obsidian 版保留全部原文链接（用户的知识库需要可追溯），本脚本只产出
    公众号专用副本，不改动源文件。
  - 公众号版正文 0 裸链接：来源以上标序号 [n] 标注，链接按正文出现顺序
    全局去重编号，收在文末 References 章节，由 build_article.py 解析成
    最后一个 chapter。
  - 来源标签优先用链接文字本身（如「官方公告」），无链接文字时退回
    上下文标签（要点N / 分类 / 主条目名）。
"""
import argparse
import re
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s、，；）)\]]+")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
REFS_HEADING = "References"

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
    """截短标签：优先在中文标点（：，、）的语义边界截断，其次按空格；
    尽量不把英文单词切一半（2026-11-13 References 格式实证优化）。"""
    label = label.strip()
    if len(label) <= n:
        return label
    # 1) 首个中文标点前的语义单元通常最适合做参考文献标签
    for p in ("：", "，", "、"):
        idx = label.find(p)
        if idx != -1 and 4 <= idx <= n:
            return label[:idx].rstrip()
    # 2) 截断点恰好落在空格上：干净切
    if label[n - 1] == " " or label[n] == " ":
        return label[:n].rstrip()
    # 3) 英文单词被切半：向后扩展到词尾（最多多取 12 字符）
    if label[n - 1].isascii() and label[n - 1].isalnum() and label[n].isascii() and label[n].isalnum():
        nxt = label.find(" ", n)
        if nxt != -1 and nxt <= n + 12:
            return label[:nxt].rstrip()
    # 4) 兜底：向前回退到上一个空格（若足够长），否则硬切
    cut = label[:n]
    if " " in cut:
        sp = cut.rfind(" ")
        if sp >= n // 2:
            return cut[:sp].rstrip(" ,|｜")
    return cut.rstrip(" ,|｜")


def collect_links(text: str):
    """（保留：兼容外部调用）按正文出现顺序收集 URL，附上短标签，全局去重。"""
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


def context_label(s: str, section: str, main_title: str) -> str:
    """无链接文字可用的兜底标签：按行形态推断（与旧 collect_links 同规则）。"""
    if s.startswith("**事件内容**") or s.startswith("**值得关注的原因**"):
        return short(main_title or "主条目")
    m = re.match(r"^\*\*(.+?)\*\*", s)
    if m:
        return short(re.sub(r"[（(].*$", "", m.group(1)))
    if re.match(r"^\d+\.\s+", s):
        return "要点" + re.match(r"^(\d+)\.", s).group(1)
    if s.startswith("- "):
        head = re.sub(r"\*\*", "", s[2:]).split("｜")[0]
        return short(head or "速览")
    return short(section or "正文")


def convert_md_links(text: str):
    """正文 [text](url) -> 「text[n]」，返回 (新正文, refs)。

    refs: [(num, label, url)]，n 按正文出现顺序、URL 全局去重；
    标签优先用链接文字（截短），重复引用同一 URL 时沿用首次标签。
    """
    section, main_title = "", ""
    refs, url2num = [], {}
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            if re.match(r"^[一二三四五六七八九十]+、", section):
                main_title = re.sub(r"^[一二三四五六七八九十]+、", "", section)
            out.append(ln)
            continue

        def _sub(m):
            label_text, url = m.group(1).strip(), m.group(2)
            if url in url2num:
                n = url2num[url]
            else:
                n = len(refs) + 1
                url2num[url] = n
                label = short(label_text) if label_text else context_label(s, section, main_title)
                label = label or "来源"
                refs.append((n, label, url))
            return "%s[%d]" % (label_text, n)

        out.append(MD_LINK_RE.sub(_sub, ln))
    return "\n".join(out), refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Obsidian 版 MD 路径")
    ap.add_argument("--out", required=True, help="公众号脱敏版 MD 输出路径")
    ap.add_argument("--no-links", action="store_true", help="不生成文末 References 章节")
    args = ap.parse_args()

    text = Path(args.src).read_text(encoding="utf-8")
    for old, new in NEUTRALIZE:
        text = text.replace(old, new)

    # 1) markdown 链接 -> 「text[n]」+ References 清单（2026-11-13 用户指定格式）
    text, refs = convert_md_links(text)

    # 2) 源文件若自带 References 章节（如手工写过），原样保留并合并其条目数
    src_has_refs = re.search(r"^##\s+" + REFS_HEADING + r"\s*$", text, re.M) is not None

    lines = [sanitize_line(ln) for ln in text.splitlines()]

    # References 章节里的 URL 不能被清洗掉：只清洗该章节之前的正文
    body_url_free = True
    if src_has_refs:
        idx = next(i for i, ln in enumerate(lines)
                   if re.match(r"^##\s+" + REFS_HEADING + r"\s*$", ln.strip()))
        lines = [sanitize_line(ln) for ln in lines[:idx]] + lines[idx:]
        body = "\n".join(lines[:idx])
        body_url_free = not URL_RE.search(body)

    lines.append("")
    lines.append("注：正文以上标 [n] 标注来源编号，完整原文链接见文末 %s 清单；"
                 "Obsidian 知识库同日文件保留全部出处。" % REFS_HEADING)
    lines.append("")

    if not args.no_links and not src_has_refs:
        lines.append("## " + REFS_HEADING)
        lines.append("")
        for n, label, url in refs:
            lines.append("- [%d] %s: %s" % (n, label, url))
        lines.append("")

    out = "\n".join(lines)
    Path(args.out).write_text(out, encoding="utf-8")

    m = re.search(r"^##\s+" + REFS_HEADING + r"\s*$", out, re.M)
    body = out[: m.start()] if m else out
    inline = URL_RE.findall(body)
    if src_has_refs:
        n_refs = len(re.findall(r"^\s*-\s*\[\d+\]\s+", out, re.M))
    else:
        n_refs = len(refs)
    print("OK %s  chars=%d  inline_urls=%d  ref_links=%d"
          % (args.out, len(out), len(inline), n_refs))
    if not body_url_free:
        print("WARN: 正文仍有未收拢的裸 URL，请检查源文件")


if __name__ == "__main__":
    main()
