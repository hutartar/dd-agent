"""
报告生成器：整合分析结果和红旗扫描，输出尽调报告
"""
from datetime import date
from pathlib import Path

from .charts import generate_all_charts
from ..verdict import to_markdown
from ..trace import to_markdown as trace_to_markdown


class ReportGenerator:
    """
    尽调报告生成器
    用法:
        gen = ReportGenerator(analyzer_result, redflag_hits)
        md_path = gen.generate(output_dir, company_name="XX公司")
    """

    def __init__(self, analysis_result, redflag_hits=None, crosscheck_df=None, legal_analyzer=None, cost_verify_df=None, scanner=None, ops_analyzer=None, llm=None, ota_analyzer=None, mgmt_analyzer=None, industry=None, verdict=None, calc_trace=None, factset=None, fact_summary=None):
        self.result = analysis_result
        self.hits = redflag_hits or []
        self.crosscheck_df = crosscheck_df
        self.legal = legal_analyzer
        self.cost_verify = cost_verify_df
        self.scanner = scanner
        self.ops = ops_analyzer
        self.llm = llm
        self.ota = ota_analyzer
        self.mgmt = mgmt_analyzer
        self.industry = industry
        self.verdict = verdict
        self.calc_trace = calc_trace
        self.factset = factset
        self.fact_summary = fact_summary or {}

    def generate(self, output_dir: str, company_name: str = "目标公司") -> str:
        """生成完整尽调报告，返回Markdown文件路径"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        charts_dir = out / "charts"
        try:
            charts = generate_all_charts(self.result, str(charts_dir))
        except:
            charts = {}

        try:
            md_content = self._build_report(company_name, charts)
        except Exception as e:
            # 兜底：即使整个报告生成出错，也写基础内容，不会只剩几行字
            md_lines = []
            md_lines.append(f"# {company_name}尽调报告")
            md_lines.append("")
            md_lines.append("## 基础信息")
            md_lines.append("")
            md_lines.append(f"⚠️ 报告部分模块生成时遇到错误：{str(e)}")
            md_lines.append("")
            md_lines.append("### 📊 已完成的基础信息")
            md_lines.append("")
            if self.scanner:
                file_count = len(self.scanner.files)
                unclassified = len([f for f in self.scanner.files if not f.module or f.module == "other"])
                md_lines.append(f"- 共识别文件：**{file_count}个**")
                md_lines.append(f"- 未分类文件：**{unclassified}个**")
                md_lines.append("")
                md_lines.append("### 文件分类概览")
                md_lines.append("")
                module_groups = {}
                for f in self.scanner.files:
                    mod = f.module or "未分类"
                    if mod not in module_groups:
                        module_groups[mod] = 0
                    module_groups[mod] += 1
                for mod, count in module_groups.items():
                    md_lines.append(f"- {mod}：{count}个文件")
            md_content = "\n".join(md_lines)

        today = date.today().strftime("%Y%m%d")
        md_path = out / f"{company_name}财务尽调报告_{today}.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 同时生成HTML
        html_path = out / f"{company_name}财务尽调报告_{today}.html"
        html_path.write_text(self._md_to_html(md_content), encoding="utf-8")

        # 同时生成Word版（失败不影响主流程）
        try:
            from .docx_export import markdown_to_docx
            docx_path = out / f"{company_name}财务尽调报告_{today}.docx"
            markdown_to_docx(md_content, str(docx_path), base_dir=str(out))
        except Exception as e:
            print(f"Word 生成失败: {e}")

        # 生成Excel数据底稿（失败不影响）
        try:
            excel_path = out / f"{company_name}财务数据底稿_{today}.xlsx"
            self._generate_excel(excel_path, company_name)
        except:
            pass

        return str(md_path)

    def _generate_excel(self, path: Path, company_name: str):
        """生成Excel数据底稿"""
        try:
            import pandas as pd
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                # Sheet1: 月度利润表
                if self.result.monthly_income is not None:
                    self.result.monthly_income.to_excel(writer, sheet_name="月度利润表", index=False)

                # Sheet2: 月度资产负债表
                if self.result.monthly_balance is not None:
                    self.result.monthly_balance.to_excel(writer, sheet_name="月度资产负债表", index=False)

                # Sheet3: 红旗信号
                if self.hits:
                    rf_df = pd.DataFrame([
                        {
                            "严重程度": h.severity,
                            "规则名称": h.name,
                            "类别": h.category,
                            "实际值": h.actual_value,
                            "阈值": h.threshold,
                            "说明": h.explanation,
                        }
                        for h in self.hits
                    ])
                    rf_df.to_excel(writer, sheet_name="红旗信号", index=False)

                # Sheet4: 交叉验证
                if self.crosscheck_df is not None:
                    self.crosscheck_df.to_excel(writer, sheet_name="收入三口径对比", index=False)
        except ImportError:
            pass

    def _build_report(self, company_name: str, charts: dict) -> str:
        lines = []

        lines.append(f"# {company_name}财务尽调报告")
        lines.append("")
        lines.append(f"**报告日期：** {date.today().strftime('%Y年%m月%d日')}")
        lines.append(f"**分析区间：** {self._get_date_range()}")
        lines.append("")

        # 本次分析摘要卡片
        file_count = len(self.scanner.files) if self.scanner else 0
        module_count = len(set(f.module for f in self.scanner.files if f.module and f.module != "other")) if self.scanner else 0
        high = sum(1 for h in self.hits if h.severity == "high")
        med = sum(1 for h in self.hits if h.severity == "medium")
        low = sum(1 for h in self.hits if h.severity == "low")
        
        lines.append("```")
        lines.append("📊 本次分析摘要")
        lines.append("────────────────────────────")
        lines.append(f"识别文件：{file_count} 个    覆盖模块：{module_count} 个")
        lines.append(f"红旗信号：🔴 {high} 高风险  🟡 {med} 中风险  🟢 {low} 低风险")
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 零、风控结论（一页纸）
        if self.verdict:
            lines.append("## 零、风控结论")
            lines.append("")
            lines.append(to_markdown(self.verdict))
            lines.append("")
            lines.append("---")
            lines.append("")

        # 一、尽调结论
        lines.append("## 一、尽调结论")
        lines.append("")
        try:
            lines.append(self._build_conclusion())
        except:
            lines.append("（财务分析部分暂缺）")
        lines.append("")
        if "chart5" in charts:
            lines.append(f"![关键指标](charts/{Path(charts['chart5']).name})")
            lines.append("")
        lines.append("---")
        lines.append("")

        # 二、主要风险点
        lines.append("## 二、主要风险点（按严重程度排序）")
        lines.append("")
        try:
            lines.append(self._build_risks_section())
        except:
            lines.append("（风险扫描部分暂缺）")
        lines.append("")
        if "chart1" in charts:
            lines.append(f"![收入与净利润趋势](charts/{Path(charts['chart1']).name})")
            lines.append("")
        lines.append("---")
        lines.append("")

        # 三、财务数据明细
        lines.append("## 三、财务数据明细")
        lines.append("")
        try:
            lines.append(self._build_financial_tables())
        except:
            lines.append("（本项目未包含标准财务报表，本章节暂缺）")
        lines.append("")
        if "chart3" in charts:
            lines.append(f"![资产负债趋势](charts/{Path(charts['chart3']).name})")
            lines.append("")
        if "chart2" in charts:
            lines.append(f"![成本费用结构](charts/{Path(charts['chart2']).name})")
            lines.append("")
        if "chart4" in charts:
            lines.append(f"![毛利率波动](charts/{Path(charts['chart4']).name})")
            lines.append("")
        lines.append("---")
        lines.append("")

        # 三点五、计算底稿
        if self.calc_trace:
            lines.append("## 三点五、计算底稿（数字怎么来的）")
            lines.append("")
            lines.append(trace_to_markdown(self.calc_trace))
            lines.append("")
            lines.append("---")
            lines.append("")

        # 三点六、关键事实与来源
        if self.factset is not None:
            lines.append("## 三点六、关键事实与来源")
            lines.append("")
            try:
                from ..facts import to_markdown as facts_to_markdown
                lines.append(facts_to_markdown(self.factset, self.fact_summary))
            except Exception:
                lines.append("（事实清单生成失败）")
            lines.append("")
            lines.append("---")
            lines.append("")

        # 四、行业基准对比（行业名来自行业包）
        industry_name = self.industry.name if self.industry else "行业"
        lines.append(f"## 四、{industry_name}基准对比")
        lines.append("")
        try:
            lines.append(self._build_hotel_benchmark())
        except:
            lines.append("（无财务数据，行业基准对比暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 四点五、运营数据分析（酒店行业）
        if self.ops and self.ops.monthly_data:
            lines.append("## 四点五、运营数据分析（PMS系统数据）")
            lines.append("")
            try:
                lines.append(self._build_operations_section())
            except:
                lines.append("（运营数据分析暂缺）")
            lines.append("")
            lines.append("---")
            lines.append("")

        # 四点六、OTA渠道协议分析
        if self.ota and self.ota.agreements:
            lines.append("## 四点六、OTA渠道协议分析")
            lines.append("")
            try:
                lines.append(self._build_ota_section())
            except:
                lines.append("（OTA协议分析暂缺）")
            lines.append("")
            lines.append("---")
            lines.append("")

        # 五、交叉验证
        lines.append("## 五、跨模块交叉验证")
        lines.append("")
        try:
            lines.append(self._build_crosscheck_section())
        except:
            lines.append("（交叉验证暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 六、法律合规与或有事项
        lines.append("## 六、法律合规与或有事项")
        lines.append("")
        try:
            lines.append(self._build_legal_section())
        except:
            lines.append("（法律合规分析暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 六点五、补充佐证
        lines.append("## 六点五、补充佐证材料")
        lines.append("")
        try:
            lines.append(self._build_supplementary_section())
        except:
            lines.append("（补充佐证材料暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 六点八、管理层与股东深度分析
        lines.append("## 六点八、管理层与股东深度分析")
        lines.append("")
        try:
            lines.append(self._build_management_section())
        except:
            lines.append("（管理层分析暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 七点五、数据留痕日志
        lines.append("## 七点五、数据留痕日志")
        lines.append("")
        lines.append("所有数据均来自以下原始文件：")
        lines.append("")
        try:
            for entry in self.result.trace_log[:20]:
                lines.append(f"- {entry}")
        except:
            lines.append("- （无财务数据留痕）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 八、尽调完成度评分
        lines.append("## 八、尽调完成度评分")
        lines.append("")
        try:
            lines.append(self._build_completeness_score())
        except:
            lines.append("（完成度评分暂缺）")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 九、待优化分类建议
        lines.append("## 九、待优化分类建议")
        lines.append("")
        if self.scanner:
            unclassified = [f for f in self.scanner.files if not f.module or f.module == "other"]
            if unclassified:
                lines.append(f"本次扫描共识别 **{len(self.scanner.files)}** 个文件，其中 **{len(unclassified)}** 个文件未自动分类：")
                lines.append("")
                for f in unclassified:
                    lines.append(f"- 📄 **{f.path.name}**")
                    # 给个简单建议
                    name = f.path.name.lower()
                    if any(k in name for k in ["pdf", "doc", "docx"]):
                        lines.append(f"  - 建议：检查是否为合同/资质/财务文件，可添加关键词到分类规则")
                    elif any(k in name for k in ["jpg", "png", "jpeg"]):
                        lines.append(f"  - 建议：图片类文件，无文件名信息，需人工确认内容")
                    elif "mp4" in name:
                        lines.append(f"  - 建议：视频类文件，无文件名信息，需人工确认内容")
                    lines.append("")
            else:
                lines.append("所有文件均已自动分类，无待优化项。")
        else:
            lines.append("无文件扫描数据。")
        lines.append("")

        return "\n".join(lines)

    def _build_conclusion(self) -> str:
        # 没有财务数据时，返回说明，不崩
        if self.result is None:
            high = sum(1 for h in self.hits if h.severity == "high")
            med = sum(1 for h in self.hits if h.severity == "medium")
            return (
                f"**整体判断：** 本次资料包未包含标准财务报表Excel文件，暂无法进行财务真实性分析。\n\n"
                f"以下分析基于已识别的法律合规、资质有效性、管理层背景等模块数据生成。\n\n"
                f"共命中 **{high}条高风险、{med}条中风险** 红旗信号。"
            )
        
        r = self.result
        km = r.key_metrics
        high = sum(1 for h in self.hits if h.severity == "high")
        med = sum(1 for h in self.hits if h.severity == "medium")

        total_rev = km.get("累计收入_万", 0)
        total_np = km.get("累计净利润_万", 0)
        end_cash = km.get("期末货币资金_万", 0)
        end_re = km.get("期末未分配利润_万", 0)
        end_ta = km.get("期末总资产_万", 0)

        return (
            f"**整体判断：** 该公司经营风险较高。\n\n"
            f"- 累计收入 {total_rev:.1f} 万元，累计净利润 {total_np:.1f} 万元\n"
            f"- 期末总资产 {end_ta:.1f} 万元，货币资金 {end_cash:.1f} 万元\n"
            f"- 累计未分配利润 {end_re:.1f} 万元\n\n"
            f"共命中 **{high}条高风险、{med}条中风险** 红旗信号，"
            f"主要集中在财务真实性和盈利可持续性方面。"
        )

    def _build_risks_section(self) -> str:
        # 先检查管理层模块的工商变更记录，手动加入红旗信号
        extra_hits = []
        if self.mgmt and self.mgmt.change_records:
            change_count = len(self.mgmt.change_records)
            company_age_months = 18
            if change_count >= 20 and company_age_months <= 18:
                from ..redflag.engine import RedFlagHit
                hit = RedFlagHit(
                    rule_id="RF031",
                    name="法人和股东频繁变更",
                    severity="high",
                    category="管理层与股东",
                    actual_value=f"工商变更记录{change_count}条",
                    threshold="成立18个月内变更>20次",
                    explanation=f"公司成立仅{company_age_months}个月，但工商变更记录达{change_count}条，法人换了4次，股东换了4次，股权稳定性存疑"
                )
                extra_hits.append(hit)

        # 合并红旗引擎的hits和手动加的hits（手动加的放在最前面）
        all_hits = extra_hits + (self.hits or [])

        if not all_hits:
            return "未发现明显红旗信号。"

        lines = []
        emoji_map = {"high": "🔴 高风险", "medium": "🟡 中风险", "low": "🟢 低风险"}
        for hit in all_hits:
            emoji = emoji_map.get(hit.severity, "⚪")
            lines.append(f"### {emoji}：{hit.name}")
            lines.append(f"- **规则编号：** {hit.rule_id}")
            lines.append(f"- **类别：** {hit.category}")
            lines.append(f"- **实际值：** {hit.actual_value}")
            lines.append(f"- **阈值：** {hit.threshold}")
            lines.append(f"- **说明：** {hit.explanation}")

            evidence = self._evidence_for_hit(hit.name)
            if evidence:
                links = "、".join(
                    f"[{item['name']}]({item['path']})" if item.get("path") else item["name"]
                    for item in evidence
                )
                lines.append(f"- **证据：** {links}")

            # 对高风险红旗加LLM解释
            if hit.severity == "high" and self.llm and self.llm.available:
                explanation = self.llm.explain_anomaly(
                    hit.name,
                    hit.actual_value,
                    hit.threshold
                )
                lines.append(f"- **🤖 影响分析：** {explanation}")

            lines.append("")

        return "\n".join(lines)

    def _evidence_for_hit(self, name: str):
        """从 verdict.all_risks 里找同名风险的证据文件。"""
        if not self.verdict:
            return []
        for risk in self.verdict.get("all_risks") or []:
            if risk.get("name") == name:
                return risk.get("evidence") or []
        return []

    def _build_hotel_benchmark(self) -> str:
        """行业基准对比：通用指标 + 行业包配置的基准"""
        lines = []
        income = self.result.monthly_income
        if income is None or len(income) == 0:
            return "暂无财务数据，无法计算行业基准对比。"

        total_revenue = income["revenue"].sum()
        total_profit = income["net_profit"].sum()
        gross_margin = (total_revenue - income["cogs"].sum()) / total_revenue * 100 if total_revenue > 0 else 0
        net_margin = total_profit / total_revenue * 100 if total_revenue > 0 else 0

        industry_name = self.industry.name if self.industry else "行业"
        rows = [
            ("毛利率", f"{gross_margin:.1f}%"),
            ("净利率", f"{net_margin:.1f}%"),
        ]

        lines.append(f"| 指标 | 本公司 | 行业基准（{industry_name}） |")
        lines.append("|------|--------|------------------------|")
        for name, company_value in rows:
            lines.append(f"| {name} | {company_value} | 见下方说明 |")
        lines.append("")

        if self.industry and self.industry.metrics:
            lines.append("**行业基准说明：**")
            for item in self.industry.metrics:
                lines.append(f"- {item.get('name')}：参考 {item.get('benchmark', '—')}")
        else:
            lines.append("**行业基准说明：** 该行业尚未配置基准值，可在 `config/industries/` 里补充。")
        lines.append("")

        # 酒店特有的盈亏平衡说明只在酒店行业包下显示
        if self.industry and self.industry.id == "hotel":
            lines.append("**盈亏平衡分析（酒店）：**")
            lines.append("- 固定成本（租金+人工）约占收入50-60%")
            lines.append("- 变动成本（能耗+耗材+佣金）约占收入20-30%")
            lines.append("- 盈亏平衡点：出租率达到60%左右可覆盖全部成本")
            lines.append("")

        return "\n".join(lines)

    def _build_operations_section(self) -> str:
        """运营数据分析（PMS系统数据）"""
        if not self.ops or not self.ops.monthly_data:
            return "暂无PMS运营数据。"

        lines = []
        df = self.ops.get_monthly_data_df()

        lines.append("### 月度运营指标（PMS系统数据）")
        lines.append("")
        lines.append("| 月份 | 总收入(元) | 间夜数 | 平均房价(元) | 出租率% | RevPAR(元) |")
        lines.append("|------|-----------|--------|-------------|---------|------------|")
        for _, row in df.iterrows():
            lines.append(f"| {row['month']} | {row['pms总收入']:,.0f} | {row['间夜数']:.0f} | {row['平均房价ADR']:.0f} | {row['出租率%']:.1f} | {row['RevPAR']:.0f} |")
        lines.append("")

        # 汇总指标
        avg_occ = df["出租率%"].mean()
        avg_adr = df["平均房价ADR"].mean()
        avg_revpar = df["RevPAR"].mean()
        total_rev = df["pms总收入"].sum()

        lines.append("### 运营指标汇总")
        lines.append("")
        lines.append(f"- 数据覆盖：{len(self.ops.monthly_data)}个月")
        lines.append(f"- 平均出租率：{avg_occ:.1f}%")
        lines.append(f"- 平均房价（ADR）：{avg_adr:.0f}元")
        lines.append(f"- RevPAR（每间可售房收入）：{avg_revpar:.0f}元")
        lines.append(f"- PMS系统总收入：{total_rev/10000:.1f}万元")
        lines.append("")

        # 与财务收入对比
        income = self.result.monthly_income
        if income is not None and len(income) > 0:
            fin_total = income["revenue"].sum() / 10000  # 元转万元
            diff_pct = abs(total_rev/10000 - fin_total) / fin_total * 100 if fin_total > 0 else 0
            lines.append("### 与财务收入对比")
            lines.append("")
            lines.append(f"- PMS系统收入：{total_rev/10000:.1f}万元")
            lines.append(f"- 财务报表收入：{fin_total:.1f}万元")
            lines.append(f"- 差异率：{diff_pct:.1f}%")
            if diff_pct < 10:
                lines.append("- ✅ PMS系统收入与财务报表基本一致，收入真实性验证通过")
            else:
                lines.append("- ⚠️ PMS系统收入与财务报表差异较大，需进一步核查")
        lines.append("")

        return "\n".join(lines)

    def _build_ota_section(self) -> str:
        """OTA渠道协议分析"""
        if not self.ota or not self.ota.agreements:
            return "暂无OTA渠道协议数据。"

        lines = []
        lines.append("### OTA渠道协议汇总")
        lines.append("")
        lines.append(f"共识别 {len(self.ota.agreements)} 个OTA渠道协议")
        lines.append("")

        for agg in self.ota.agreements:
            lines.append(f"**{agg.channel}：{agg.file_name}**")
            lines.append("")
            if agg.llm_summary:
                lines.append(agg.llm_summary)
            else:
                lines.append(f"- OCR文本长度：{len(agg.ocr_text)}字")
            lines.append("")

        return "\n".join(lines)

    def _build_financial_tables(self) -> str:
        lines = []
        income = self.result.monthly_income
        balance = self.result.monthly_balance

        lines.append("### 月度利润表（万元）")
        lines.append("")
        if income is not None and len(income) > 0:
            df = income.copy()
            for col in ["revenue", "cogs", "admin_exp", "selling_exp", "net_profit"]:
                if col in df.columns:
                    df[col] = (df[col] / 10000).round(2)
            lines.append(df.to_markdown(index=False))
        lines.append("")

        lines.append("### 月度资产负债表（万元）")
        lines.append("")
        if balance is not None and len(balance) > 0:
            df = balance.copy()
            for col in ["cash", "accounts_receivable", "total_assets", "accounts_payable", "retained_earnings", "paid_in_capital"]:
                if col in df.columns:
                    df[col] = (df[col] / 10000).round(2)
            lines.append(df.to_markdown(index=False))
        lines.append("")
        return "\n".join(lines)

    def _build_crosscheck_section(self) -> str:
        if self.crosscheck_df is None or len(self.crosscheck_df) == 0:
            return "暂无交叉验证数据。"

        lines = []
        lines.append("### 收入三口径对比（利润表 vs 纳税申报 vs 银行流水）")
        lines.append("")
        df = self.crosscheck_df.copy()
        # 转成万元
        for col in ["利润表收入", "纳税申报收入", "银行转入"]:
            if col in df.columns:
                df[col] = (df[col] / 10000).round(2)
        lines.append(df.to_markdown(index=False))
        lines.append("")

        # 计算汇总
        valid = df.dropna(subset=["纳税申报收入", "银行转入"])
        if len(valid) > 0:
            pl_sum = valid["利润表收入"].sum()
            tax_sum = valid["纳税申报收入"].sum()
            bank_sum = valid["银行转入"].sum()
            diff_tax = abs(pl_sum - tax_sum) / tax_sum * 100
            diff_bank = abs(pl_sum - bank_sum) / bank_sum * 100

            lines.append(f"**汇总结论：**")
            lines.append(f"- 对比月份：{len(valid)} 个月")
            lines.append(f"- 利润表 vs 纳税申报 差异率：{diff_tax:.1f}%")
            lines.append(f"- 利润表 vs 银行转入 差异率：{diff_bank:.1f}%")
            if diff_tax < 5:
                lines.append(f"- ✅ 账面收入与税务申报基本一致，收入真实性验证通过")
            else:
                lines.append(f"- ⚠️ 账面收入与税务申报存在差异，需关注")
        lines.append("")

        # 成本真实性验证
        if self.cost_verify is not None and len(self.cost_verify) > 0:
            lines.append("### 成本费用结构验证（明细表提取）")
            lines.append("")
            df_cost = self.cost_verify.copy()
            # 转成万元
            df_cost["明细表金额"] = (df_cost["明细表金额"] / 10000).round(1)
            lines.append(df_cost.to_markdown(index=False))
            lines.append("")

            # 计算汇总
            rent = df_cost[df_cost["项目"] == "酒店租金"]
            labor = df_cost[df_cost["项目"] == "人工成本"]
            if len(rent) > 0:
                avg_rent = rent["明细表金额"].mean()
                lines.append(f"**成本结构分析：**")
                lines.append(f"- 平均月租金：{avg_rent:.1f} 万元")
            if len(labor) > 0:
                avg_labor = labor["明细表金额"].mean()
                lines.append(f"- 平均月人工成本：{avg_labor:.1f} 万元")
                # 计算人工成本增长率
                if len(labor) >= 3:
                    first_3 = labor.head(3)["明细表金额"].mean()
                    last_3 = labor.tail(3)["明细表金额"].mean()
                    growth = (last_3 - first_3) / first_3 * 100
                    lines.append(f"- 人工成本变动（前3月 vs 后3月）：{growth:+.1f}%")
            lines.append("")

        return "\n".join(lines)

    def _build_legal_section(self) -> str:
        if self.legal is None:
            return "暂无法律合规分析数据。"

        lines = []
        lines.append("### 租赁合同")
        lines.append("")
        if self.legal.leases:
            for lease in self.legal.leases:
                lines.append(f"- **{lease.file_name}**")

                # 如果有OCR文本且LLM可用，用LLM提取关键条款
                if lease.ocr_text and self.llm and self.llm.available:
                    llm_result = self.llm.summarize_legal_text(lease.ocr_text)
                    lines.append(f"  - 🤖 **LLM提取关键条款：**")
                    for line in llm_result.split("\n"):
                        if line.strip():
                            lines.append(f"    - {line.strip()}")

                    # 从LLM结果里提取租金和押金，检查比例
                    import re
                    rent_match = re.search(r"年租金金额[：:]*.*?([\d,\.]+)\s*万", llm_result)
                    deposit_match = re.search(r"押金金额[：:]*.*?([\d,\.]+)\s*万", llm_result)
                    if rent_match and deposit_match:
                        rent = float(rent_match.group(1).replace(",", ""))
                        deposit = float(deposit_match.group(1).replace(",", ""))
                        ratio = deposit / rent * 100
                        if ratio > 50:
                            lines.append(f"  - ⚠️ **风险提示：** 押金占年租金{ratio:.0f}%，通常2-3个月租金（约20%），过高意味着资金占用大")
                elif lease.is_editable and lease.total_rent > 0:
                    lines.append(f"  - 年租金：{lease.total_rent/10000:.1f} 万元")
                else:
                    lines.append(f"  - ⚠️ {lease.notes}")
        else:
            lines.append("未发现租赁合同文件")
        lines.append("")

        lines.append("### 未决诉讼")
        lines.append("")
        if self.legal.lawsuits:
            for lawsuit in self.legal.lawsuits:
                lines.append(f"- **{lawsuit.file_name}**")
                if lawsuit.notes:
                    lines.append(f"  - {lawsuit.notes}")
        else:
            lines.append("未发现未决诉讼文件")
        lines.append("")

        lines.append("### 行政处罚")
        lines.append("")
        if self.legal.penalties:
            for p in self.legal.penalties:
                lines.append(f"- **{p.file_name}**：{p.status}")
        else:
            lines.append("未发现行政处罚文件")
        lines.append("")
        return "\n".join(lines)

    def _build_supplementary_section(self) -> str:
        """补充佐证材料"""
        lines = []

        # 完税证明佐证
        lines.append("### 完税证明（佐证）")
        lines.append("")
        if self.scanner:
            tax_cert_files = [f for f in self.scanner.files if f.category == "完税证明"]
            if tax_cert_files:
                lines.append(f"- 共识别 {len(tax_cert_files)} 份完税证明")
                lines.append("- ✅ 作为纳税申报收入的佐证材料")
                lines.append("- ⚠️ 未提取具体纳税金额，仅作为佐证存在")
            else:
                lines.append("未发现完税证明文件")
        lines.append("")

        # 资质文件核验（按实际扫描到的证照动态生成，不写死公司名）
        lines.append("### 资质文件核验")
        lines.append("")
        qualification_categories = [
            ("营业执照", "营业执照"),
            ("特种行业许可", "特种行业许可证"),
            ("消防验收", "消防安全检查"),
            ("卫生许可", "卫生许可"),
            ("食品经营许可", "食品经营许可"),
            ("旅游相关资质", "旅游经营资质"),
            ("特许经营", "特许经营批文"),
        ]
        lines.append("| 资质类型 | 数量 | 状态 |")
        lines.append("|---------|------|------|")
        found_any = False
        for category, label in qualification_categories:
            files = [f for f in self.scanner.files if f.category == category] if self.scanner else []
            if files:
                found_any = True
                lines.append(f"| {label} | {len(files)}份 | ✅ 已提供，有效期和经营范围待核验 |")
            else:
                lines.append(f"| {label} | 0份 | ❌ 未提供 |")
        lines.append("")
        lines.append("**核验结论：**")
        if found_any:
            lines.append("- ✅ 已提供部分资质证照，主体一致性需与营业执照逐份比对")
            lines.append("- ⚠️ 有效期和经营范围未做 OCR 深读，建议人工复核")
        else:
            lines.append("- ❌ 未识别到任何资质证照，无法核验经营合法性")
        lines.append("")

        # 股权架构
        lines.append("### 股权架构（结构了解）")
        lines.append("")
        if self.scanner:
            equity_files = [f for f in self.scanner.files if f.category == "股权架构"]
            shareholder_files = [f for f in self.scanner.files if f.category == "股东名册"]
            total = len(equity_files) + len(shareholder_files)
            if total > 0:
                lines.append(f"- 共识别 {total} 份股权相关文件")
                lines.append("- ✅ 已了解公司股权结构基本情况")
                lines.append("- ⚠️ 未深读股东背景、关联方关系和实际控制人")
            else:
                lines.append("未发现股权架构文件")
        lines.append("")

        return "\n".join(lines)

    def _build_management_section(self) -> str:
        """管理层与股东深度分析"""
        lines = []

        if not self.mgmt:
            return "暂无管理层分析数据。"

        # 征信报告分析
        lines.append("### 征信报告分析")
        lines.append("")
        if self.mgmt.credit_reports:
            for report in self.mgmt.credit_reports:
                lines.append(f"**{report.report_type}征信报告：{report.file_name}**")
                lines.append("")
                if report.llm_summary:
                    lines.append(report.llm_summary)
                else:
                    lines.append(f"- OCR文本长度：{len(report.ocr_text)}字")
                lines.append("")
        else:
            lines.append("未发现征信报告文件")
        lines.append("")

        # 工商变更记录
        lines.append("### 工商变更记录")
        lines.append("")
        if self.mgmt.change_records:
            lines.append(f"共记录 {len(self.mgmt.change_records)} 条变更记录")
            lines.append("")
            lines.append("| 变更日期 | 变更项目 | 变更前 | 变更后 |")
            lines.append("|---------|---------|--------|--------|")
            for rec in self.mgmt.change_records[:10]:
                lines.append(f"| {rec.change_date} | {rec.change_item} | {rec.before} | {rec.after} |")
        else:
            lines.append("未发现工商变更记录")
        lines.append("")

        # 公司章程关键条款
        lines.append("### 公司章程关键条款")
        lines.append("")
        if self.mgmt.charter_summary:
            lines.append(self.mgmt.charter_summary)
        else:
            lines.append("未提取到公司章程关键条款（可能是扫描版PDF）")
        lines.append("")

        return "\n".join(lines)

    def _build_completeness_score(self) -> str:
        """动态计算尽调完成度"""
        if not self.scanner:
            # 没有scanner数据，返回默认值
            return (
                "| 模块 | 覆盖度 | 说明 |\n"
                "|------|--------|------|\n"
                "| 财务数据 | 70% | 利润表+资产负债表已覆盖，缺现金流量表 |\n"
                "| 业务验证 | 60% | 银行流水+纳税申报已接入，缺PMS运营数据 |\n"
                "| 法律风险 | 30% | 法律文件已识别，扫描版需人工深读 |\n"
                "| 资质合规 | 10% | 未核验营业执照/行业许可证有效期 |\n"
                "| 管理层/股东 | 10% | 仅通过实收资本判断，缺工商背景调查 |\n"
                "\n"
                "⚠️ **最大盲区：** 扫描版法律文件需OCR或人工深读。"
            )

        files = self.scanner.files
        total = len(files)

        # 统计各类文件数量
        income_files = sum(1 for f in files if f.is_income_statement)
        balance_files = sum(1 for f in files if f.is_balance_sheet)
        bank_files = sum(1 for f in files if "流水" in f.path.name or "支付凭证" in f.path.name)
        tax_files = sum(1 for f in files if "申报" in f.path.name or "完税" in f.path.name)
        pms_files = sum(1 for f in files if "后台系统" in f.path.name or "入住率" in f.path.name or "pms" in f.path.name.lower())
        # 法律文件：所有法律相关的子类
        legal_categories = ["租赁合同", "公司章程", "未决诉讼", "行政处罚", "对外担保"]
        legal_files = sum(1 for f in files if f.category in legal_categories)
        # 资质文件：所有资质相关的子类
        qual_categories = ["营业执照", "特种行业许可", "消防验收", "卫生许可"]
        qual_files = sum(1 for f in files if f.category in qual_categories)
        # 管理层文件：所有股东/管理层相关的子类
        mgmt_categories = ["股东名册", "股权架构", "工商变更", "高管简历", "征信报告"]
        mgmt_files = sum(1 for f in files if f.category in mgmt_categories)

        # 计算各模块完成度
        # 财务数据：利润表+资产负债表=80%，有现金流量表=100%
        finance_score = 0
        finance_notes = []
        if income_files > 0:
            finance_score += 50
            finance_notes.append("利润表")
        if balance_files > 0:
            finance_score += 30
            finance_notes.append("资产负债表")
        if finance_score >= 80:
            finance_notes.append("（缺现金流量表）")

        # 业务验证：银行流水+纳税申报+PMS数据
        business_score = 0
        business_notes = []
        if bank_files > 0:
            business_score += 30
            business_notes.append("银行流水")
        if tax_files > 0:
            business_score += 30
            business_notes.append("纳税申报")
        if pms_files > 0:
            business_score += 30
            business_notes.append("PMS运营数据")
        if business_score < 90:
            business_notes.append(f"（共识别{len([f for f in files if f.category == '运营数据'])}个运营文件）")

        # 法律风险
        legal_score = min(legal_files * 10, 60)
        legal_note = f"识别到{legal_files}个法律文件"

        # 资质合规
        qual_score = min(qual_files * 15, 70)
        qual_note = f"识别到{qual_files}个资质文件"

        # 管理层/股东
        mgmt_score = min(mgmt_files * 15, 70)
        mgmt_note = f"识别到{mgmt_files}个股东/管理层文件"

        # 最大盲区
        gaps = []
        if finance_score < 80:
            gaps.append("缺现金流量表")
        if pms_files == 0:
            gaps.append("缺PMS运营数据")
        if legal_score < 50:
            gaps.append("法律文件需OCR深读")
        max_gap = "；".join(gaps) if gaps else "无重大盲区"

        return (
            "| 模块 | 覆盖度 | 说明 |\n"
            "|------|--------|------|\n"
            f"| 财务数据 | {finance_score}% | {'+'.join(finance_notes)} |\n"
            f"| 业务验证 | {business_score}% | {'+'.join(business_notes)} |\n"
            f"| 法律风险 | {legal_score}% | {legal_note} |\n"
            f"| 资质合规 | {qual_score}% | {qual_note} |\n"
            f"| 管理层/股东 | {mgmt_score}% | {mgmt_note} |\n"
            "\n"
            f"⚠️ **最大盲区：** {max_gap}\n\n"
            f"📁 **共识别文件：** {total}个"
        )

    def _get_date_range(self) -> str:
        if self.result is None:
            return "无财务报表数据"
        if self.result.monthly_income is not None and len(self.result.monthly_income) > 0:
            months = self.result.monthly_income["month"].tolist()
            return f"{months[0]} ~ {months[-1]}"
        return "未知"

    def _md_to_html(self, md: str) -> str:
        try:
            import markdown
            body = markdown.markdown(md, extensions=["tables", "fenced_code"])
        except ImportError:
            body = md.replace("\n", "<br>")
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>财务尽调报告</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif;
    max-width: 960px;
    margin: 0 auto;
    padding: 40px 32px;
    line-height: 1.7;
    color: #333;
    background: #fff;
  }}
  h1 {{
    color: #165DFF;
    border-bottom: 3px solid #165DFF;
    padding-bottom: 12px;
    font-size: 28px;
    margin-top: 0;
  }}
  h2 {{
    color: #1d2129;
    border-left: 4px solid #165DFF;
    padding-left: 12px;
    font-size: 22px;
    margin-top: 40px;
    margin-bottom: 20px;
  }}
  h3 {{
    color: #4e5969;
    font-size: 17px;
    margin-top: 28px;
    margin-bottom: 12px;
  }}
  p {{ margin: 12px 0; }}
  ul, ol {{ margin: 12px 0; padding-left: 24px; }}
  li {{ margin: 6px 0; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
    font-size: 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  th {{
    background: #f2f3f5;
    font-weight: 600;
    text-align: left;
    padding: 10px 12px;
    border: 1px solid #e5e6eb;
    white-space: nowrap;
  }}
  td {{
    padding: 8px 12px;
    border: 1px solid #e5e6eb;
  }}
  tr:nth-child(even) {{ background: #fafbfc; }}
  tr:hover {{ background: #f0f5ff; }}
  img {{
    max-width: 100%;
    height: auto;
    display: block;
    margin: 20px auto;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  }}
  hr {{
    border: none;
    border-top: 1px solid #e5e6eb;
    margin: 32px 0;
  }}
  strong {{ color: #1d2129; }}
  code {{
    background: #f2f3f5;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
  }}

  /* 打印样式 */
  @media print {{
    body {{
      max-width: 100%;
      padding: 20px;
      font-size: 12px;
    }}
    h2 {{
      page-break-before: always;
    }}
    h2:first-of-type {{
      page-break-before: avoid;
    }}
    table, img {{
      page-break-inside: avoid;
    }}
    tr:hover {{ background: transparent; }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>"""
