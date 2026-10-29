"""
主编排器：串联所有模块，一键完成尽调
"""
from pathlib import Path
from typing import Optional
import os
import shutil
import pandas as pd

from .inventory.scanner import FileScanner
from .industry import get_industry
from .verdict import build_verdict
from .trace import build_calc_trace
from .analyzer.analyzer import FinancialAnalyzer
from .redflag.engine import RedFlagEngine
from .crosscheck.validator import CrossChecker
from .reporter.generator import ReportGenerator
from .legal.analyzer import LegalAnalyzer
from .qualification.checker import QualificationChecker
from .management.analyzer import ManagementAnalyzer
from .operations.analyzer import OperationalAnalyzer
from .llm.client import LLMClient
from .runtime_config import get_llm_config


class DDAgent:
    """
    尽调Agent主入口
    用法:
        agent = DDAgent(output_dir="./output")
        result = agent.run("C:\\path\\to\\资料文件夹")
    """

    def __init__(self, output_dir: Optional[str] = None, use_llm: Optional[bool] = None,
                 industry: Optional[str] = None):
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.industry = get_industry(industry)

        # LLM 默认开启；设置页关掉或 DD_LLM_ENABLED=0 时走纯规则引擎
        if use_llm is None:
            runtime = get_llm_config() or {}
            if "enabled" in runtime:
                use_llm = bool(runtime["enabled"])
            else:
                flag = os.getenv("DD_LLM_ENABLED", "1").strip().lower()
                use_llm = flag not in ("0", "false", "no", "off")
        # provider / model / base_url / api_key 由 LLMClient 自己读环境变量
        self.llm = LLMClient() if use_llm else None

    def _rule_vars(self, results, scanner=None) -> dict:
        """汇总红旗规则需要的变量：文件统计 + 模块结果 + 行业指标。"""
        variables = {}

        # 文件层面：有没有关键材料，直接决定非财务红旗
        if scanner:
            counts = {}
            for f in scanner.files:
                counts[f.category] = counts.get(f.category, 0) + 1
            variables.update({
                "license_count": counts.get("营业执照", 0),
                "has_bank_flow": 1 if counts.get("银行流水", 0) else 0,
                "has_tax_return": 1 if counts.get("纳税申报", 0) else 0,
                "litigation_count": counts.get("未决诉讼", 0),
                "penalty_count": counts.get("行政处罚", 0),
                "lease_count": counts.get("租赁合同", 0),
                "logistics_docs": counts.get("物流单据", 0),
                "warehouse_docs": counts.get("仓单/监管", 0),
                "invoice_docs": counts.get("发票", 0),
                "purchase_docs": counts.get("采购合同", 0),
                "sales_docs": counts.get("销售合同", 0),
                "litigation_amount": 0,  # 暂未抽取金额，NF007 会提示"未披露"
            })

        # 管理层模块：工商变更数量
        mgmt = results.get("management")
        if mgmt is not None:
            records = getattr(mgmt, "change_records", None)
            if records is not None:
                variables["change_records_count"] = len(records)

        # 运营模块：行业指标
        ops = results.get("operations")
        monthly = getattr(ops, "monthly_data", None) if ops else None
        if monthly:
            occ = [d.occupancy_rate for d in monthly if d.occupancy_rate]
            if occ:
                value = sum(occ) / len(occ)
                variables["occupancy_rate"] = value / 100 if value > 1 else value
            adr = [d.avg_daily_rate for d in monthly if d.avg_daily_rate]
            if adr:
                variables["adr"] = sum(adr) / len(adr)
            revpar = [d.revpar for d in monthly if d.revpar]
            if revpar:
                variables["revpar"] = sum(revpar) / len(revpar)

        return variables

    def _detect_company_name(self, scanner) -> str:
        """从文件名自动识别公司名称"""
        import re
        for f in scanner.files:
            name = f.path.name
            # 匹配"XX有限公司"模式
            m = re.search(r"([\u4e00-\u9fa5]{2,20}(?:有限公司|股份公司|集团))", name)
            if m:
                return m.group(1)
        return "目标公司"

    def _run_impl(self, data_dir: str | Path) -> dict:
        """
        运行完整尽调流程
        返回: 各模块结果字典
        """
        data_path = Path(data_dir)
        results = {
            "data_dir": str(data_path),
            "status": "running",
        }
        # === 步骤状态追踪（错误溯源用）===
        steps_status = []
        def add_step(name, status, detail="", level=None):
            item = {"name": name, "status": status, "detail": detail}
            if level:
                item["level"] = level
            steps_status.append(item)
            results["steps_status"] = steps_status
            self._steps_status = steps_status

        import time
        self._start_time = time.time()

        # Step 1: 目录盘点
        print("=" * 60)
        print("Step 1: 目录盘点")
        print("=" * 60)
        try:
            scanner = FileScanner(data_path, industry=self.industry)
            inventory = scanner.scan()
            results["inventory"] = scanner
            print(f"扫描到 {len(scanner.files)} 个文件")
            add_step("目录盘点", "done", f"识别{len(scanner.files)}个文件")
        except Exception as e:
            print(f"❌ Step 1 失败: {e}")
            add_step("目录盘点", "error", str(e), level="fatal")
            scanner = None

        # Step 1.5: LLM 分类 + 重要性（只对未分类/扫描版，最多 6 个；失败回退关键词）
        if scanner and self.llm and self.llm.available and os.getenv("DD_LLM_IMPORTANCE", "1") != "0":
            try:
                from .importance import classify_and_judge
                candidates = [f for f in scanner.files if f.module == "other" or f.is_scanned]
                candidates.sort(key=lambda x: x.importance, reverse=True)
                reviewed = 0
                for f in candidates[:6]:
                    text = ""
                    if f.path.suffix.lower() == ".pdf":
                        try:
                            import pypdfium2 as pdfium
                            pdf = pdfium.PdfDocument(str(f.path))
                            try:
                                if len(pdf) > 0:
                                    text = pdf[0].get_textpage().get_text_range() or ""
                            finally:
                                pdf.close()
                        except Exception:
                            pass
                    result = classify_and_judge(self.llm, f.path.name, str(f.path.parent), text)
                    if not result:
                        continue
                    if result.get("module") and result["module"] != "other" and f.module == "other":
                        f.module = result["module"]
                        f.category = f"LLM识别-{result['category']}"
                    if result.get("score") is not None:
                        f.importance = result["score"]
                    if result.get("reason"):
                        f.reason = result["reason"]
                    reviewed += 1
                    print(f"  LLM：{f.path.name[:30]} → {result['category']} / {result['score']}（{result['reason']}）")
                if reviewed:
                    print(f"LLM 分类+打分处理了 {reviewed} 个文件")
            except Exception as exc:
                print(f"LLM 分类+打分失败（回退关键词评分）: {exc}")

        # 保存文件清单JSON
        import json
        file_list = []
        for f in scanner.files:
            try:
                rel_path = str(f.path.relative_to(scanner.root))
            except ValueError:
                rel_path = f.path.name
            file_list.append({
                "name": f.path.name,
                "module": f.module,
                "category": f.category,
                "file_type": f.file_type,
                "size_mb": round(f.size_mb, 2),
                "is_scanned": f.is_scanned,
                "importance": f.importance,
                "reason": f.reason,
                "rel_path": rel_path,
            })
        
        # 按模块分组
        module_groups = {}
        for item in file_list:
            mod = item["module"] or "other"
            if mod not in module_groups:
                module_groups[mod] = []
            module_groups[mod].append(item)
        
        inventory_json = {
            "total_files": len(scanner.files),
            "module_groups": module_groups
        }
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.output_dir / "file_inventory.json", "w", encoding="utf-8") as f:
            json.dump(inventory_json, f, ensure_ascii=False, indent=2)
        print(f"文件清单已保存: file_inventory.json")
        print()

        # Step 2: 财务报表分析
        print("=" * 60)
        print("Step 2: 财务报表分析")
        print("=" * 60)
        try:
            excel_files = [str(f.path) for f in scanner.files
                           if f.category in ["利润表", "资产负债表", "科目余额表", "财务报表（两表合一）"]
                           and f.path.suffix in [".xls", ".xlsx"]]

            if excel_files:
                analyzer = FinancialAnalyzer()
                analyzer.load_files(excel_files)
                fin_result = analyzer.analyze_all()
                results["financial"] = fin_result
                print(f"提取利润表 {len(fin_result.monthly_income)} 个月")
                add_step("财务报表分析", "done", f"识别{len(fin_result.monthly_income)}个月数据")
            else:
                results["financial"] = None
                print("未找到财务报表文件")
                add_step("财务报表分析", "skipped", "无标准财务报表Excel")
        except Exception as e:
            print(f"❌ Step 2 失败: {e}")
            results["financial"] = None
            add_step("财务报表分析", "error", str(e), level="degraded")
        print()

        # Step 3: 红旗扫描
        print("=" * 60)
        print("Step 3: 红旗信号扫描")
        print("=" * 60)
        try:
            if results.get("financial"):
                engine = RedFlagEngine(
                    results["financial"],
                    extra_rules=self.industry.redflags,
                    extra_vars={},
                )
                hits = engine.scan()
                results["redflags"] = hits
                high = sum(1 for h in hits if h.severity == "high")
                mid = sum(1 for h in hits if h.severity == "medium")
                print(f"命中 {len(hits)} 条红旗（高风险 {high} 条，中风险 {mid} 条）")
                add_step("红旗规则扫描", "done", f"命中{len(hits)}条风险信号")
            else:
                results["redflags"] = []
                print("跳过（无财务数据）")
                add_step("红旗规则扫描", "skipped", "无财务数据")
        except Exception as e:
            print(f"❌ Step 3 失败: {e}")
            results["redflags"] = []
            add_step("红旗规则扫描", "error", str(e), level="degraded")
        print()

        # Step 4: 交叉验证
        print("=" * 60)
        print("Step 4: 交叉验证")
        print("=" * 60)
        try:
            checker = CrossChecker()

            # 纳税申报PDF
            tax_pdfs = [str(f.path) for f in scanner.files
                        if "纳税申报" in f.category or "增值税" in f.path.name]
            if tax_pdfs:
                checker.load_tax_returns(tax_pdfs)
                print(f"加载 {len(checker.tax_records)} 条纳税申报记录")

            # 银行流水
            bank_files = [str(f.path) for f in scanner.files
                          if "银行流水" in f.category or "收入流水" in f.category]
            if bank_files:
                checker.load_bank_flow(bank_files[0])
                print(f"银行流水覆盖 {len(checker.bank_monthly)} 个月")

            if results.get("financial") and (checker.tax_records or checker.bank_monthly):
                compare = checker.compare_revenue_3way(results["financial"].monthly_income)
                results["crosscheck"] = compare
                print(f"完成收入三口径对比")

                # 成本真实性验证
                cost_files = [f.path for f in scanner.files if "成本" in f.path.name and f.path.suffix in [".xls", ".xlsx"]]
                if cost_files:
                    cost_df = checker.compare_cost_structure(cost_files[0])
                    results["cost_verify"] = cost_df
                    print(f"完成成本费用结构验证（{len(cost_df)}条记录）")
            else:
                results["crosscheck"] = None
                print("跳过（缺少对比数据）")
            add_step("跨模块交叉验证", "done", "完成三口径收入对比")
        except Exception as e:
            print(f"❌ Step 4 失败: {e}")
            results["crosscheck"] = None
            add_step("跨模块交叉验证", "error", str(e), level="degraded")
        print()

        # Step 5: 法律合规分析
        print("=" * 60)
        print("Step 5: 法律合规分析")
        print("=" * 60)
        try:
            legal_files = [f.path for f in scanner.files if f.module == "legal"]
            if legal_files:
                legal_analyzer = LegalAnalyzer()
                legal_analyzer.load_files(legal_files)
                results["legal"] = legal_analyzer
                print(f"处理 {len(legal_files)} 个法律文件")

                # 法律红旗检查：押金占租金比例过高
                from .redflag.engine import RedFlagHit
                for lease in legal_analyzer.leases:
                    if lease.total_rent > 0 and lease.deposit > 0:
                        deposit_ratio = lease.deposit / lease.total_rent
                        # 正常押金是2-3个月租金（约17-25%年租金），超过6个月（50%）算高
                        if deposit_ratio > 0.5:
                            hit = RedFlagHit(
                                rule_id="RF-LEGAL-001",
                                name="租赁合同押金占比过高",
                                severity="medium",
                                category="法律合规",
                                explanation=f"押金{lease.deposit/10000:.0f}万，年租金{lease.total_rent/10000:.0f}万，押金占年租金{deposit_ratio*100:.0f}%，通常2-3个月租金（约20%），过高意味着资金占用大",
                                actual_value=f"{deposit_ratio*100:.0f}%",
                                threshold="<50%（6个月租金）"
                            )
                            results["redflags"].append(hit)
                            print(f"  ⚠️ 法律红旗：{lease.file_name} 押金占比{deposit_ratio*100:.0f}%")
                add_step("法律合规分析", "done", f"处理{len(legal_files)}个法律文件")
            else:
                results["legal"] = None
                print("未找到法律合规文件")
                add_step("法律合规分析", "skipped", "无法律合规文件")
        except Exception as e:
            print(f"❌ Step 5 失败: {e}")
            results["legal"] = None
            add_step("法律合规分析", "error", str(e), level="degraded")
        print()

        # Step 5.5: 资质核验 + 管理层股东
        print("=" * 60)
        print("Step 5.5: 资质核验与管理层背景")
        print("=" * 60)
        qual_files = [f.path for f in scanner.files if f.module == "qualification"]
        if qual_files:
            qual_checker = QualificationChecker()
            qual_checker.load_files(qual_files)
            results["qualification"] = qual_checker
            print(f"核验 {len(qual_files)} 个资质文件")
        else:
            results["qualification"] = None
            print("未找到资质文件")

        # 加载management模块的文件
        mgmt_files = [Path(f.path) for f in scanner.files if f.module == "management"]
        # 同时加载legal模块里的公司章程
        charter_files = [Path(f.path) for f in scanner.files if f.category == "公司章程"]
        all_mgmt_files = mgmt_files + charter_files

        if all_mgmt_files:
            mgmt_analyzer = ManagementAnalyzer(llm=self.llm)
            mgmt_analyzer.load_files(all_mgmt_files)
            results["management"] = mgmt_analyzer
            print(f"处理 {len(all_mgmt_files)} 个管理层相关文件（含{len(charter_files)}个公司章程）")
            print(f"  - 征信报告：{len(mgmt_analyzer.credit_reports)}个")
            print(f"  - 工商变更记录：{len(mgmt_analyzer.change_records)}条")
            if mgmt_analyzer.charter_summary:
                print(f"  - 公司章程：已提取关键条款")
        else:
            results["management"] = None
            print("未找到管理层相关文件")
        print()

        # Step 5.6: 运营数据分析（酒店行业）
        print("=" * 60)
        print("Step 5.6: 运营数据分析（酒店行业）")
        print("=" * 60)
        try:
            pms_files = [str(f.path) for f in scanner.files
                         if "PMS" in f.category or "后台系统" in f.path.name or "入住率" in f.path.name]
            if pms_files:
                ops_analyzer = OperationalAnalyzer()
                for pms_file in pms_files:
                    try:
                        ops_analyzer.load_file(pms_file)
                    except Exception as exc:
                        # 单个坏文件不能拖垮整条流程
                        print(f"⚠️ 跳过无法解析的运营数据 {Path(pms_file).name}: {exc}")
                results["operations"] = ops_analyzer
                print(f"分析 {len(pms_files)} 个运营数据文件")
                print(ops_analyzer.get_summary())
            else:
                results["operations"] = None
                print("未找到PMS运营数据文件")
        except Exception as exc:
            print(f"❌ Step 5.6 失败: {exc}")
            results["operations"] = None
            add_step("运营数据分析", "error", str(exc), level="warning")
        print()

        # Step 5.7: OTA渠道协议分析
        print("=" * 60)
        print("Step 5.7: OTA渠道协议分析")
        print("=" * 60)
        from .operations.ota_analyzer import OTAAnalyzer
        ota_files = [str(f.path) for f in scanner.files if "OTA" in f.category]
        if ota_files:
            ota_analyzer = OTAAnalyzer(llm=self.llm)
            ota_analyzer.load_files(ota_files)
            results["ota"] = ota_analyzer
            print(f"分析 {len(ota_files)} 个OTA渠道协议")
        else:
            results["ota"] = None
            print("未找到OTA渠道协议文件")
        print()

        # Step 5.8: 行业/文件/主体规则补扫（依赖前面各模块的结果）
        try:
            if results.get("financial") is not None or scanner:
                rule_vars = self._rule_vars(results, scanner)
                extra_rules = list(self.industry.redflags)
                engine = RedFlagEngine(
                    results.get("financial"),
                    extra_rules=extra_rules,
                    extra_vars=rule_vars,
                )
                existing = {h.rule_id for h in results.get("redflags", [])}
                extra_hits = [h for h in engine.scan() if h.rule_id not in existing]
                if extra_hits:
                    results.setdefault("redflags", []).extend(extra_hits)
                    print(f"补充规则命中 {len(extra_hits)} 条")
        except Exception as e:
            print(f"补充规则扫描失败: {e}")
        print()

        # Step 6: 生成报告
        print("=" * 60)
        print("Step 6: 生成报告")
        print("=" * 60)
        try:
            company_name = self._detect_company_name(scanner)
            self._company_name = company_name
            print(f"识别公司名: {company_name}")
            
            # 事实层：把关键数字抽成带来源的事实，供结论和报告共用
            from .facts import extract_facts, summarize as summarize_facts
            factset = extract_facts(results.get("financial"), results.get("legal"), scanner)
            fact_summary = summarize_facts(factset, self.industry.facts)
            results["facts"] = factset
            results["fact_summary"] = fact_summary
            with open(self.output_dir / "facts.json", "w", encoding="utf-8") as f:
                json.dump(
                    {"facts": factset.to_dict()["facts"], "summary": fact_summary},
                    f, ensure_ascii=False, indent=2,
                )
            print(f"事实抽取：{fact_summary['fact_count']} 条，置信度 {fact_summary['confidence']:.0%}")

            # 先出风控结论，再生成报告
            verdict = build_verdict(
                scanner, results.get("redflags", []), self.industry, results,
                fact_summary=fact_summary,
            )

            # 把证据文件复制到报告目录：全部风险都复制，不只 Top 5
            evidence_dir = self.output_dir / "evidence"
            for risk in verdict.get("all_risks") or verdict.get("top_risks", []):
                copied = []
                for item in risk.get("evidence", []):
                    src = Path(item.get("source", ""))
                    if not src.exists():
                        copied.append({"name": item.get("name"), "path": ""})
                        continue
                    evidence_dir.mkdir(exist_ok=True)
                    target = evidence_dir / src.name
                    suffix = 0
                    while target.exists() and target.stat().st_size != src.stat().st_size:
                        suffix += 1
                        target = evidence_dir / f"{src.stem}_{suffix}{src.suffix}"
                    if not target.exists():
                        shutil.copy2(src, target)
                    copied.append({"name": src.name, "path": f"evidence/{target.name}"})
                risk["evidence"] = copied

            # 计算底稿：每个数字的公式、输入和来源
            calc_trace = build_calc_trace(results, scanner)
            with open(self.output_dir / "calc_trace.json", "w", encoding="utf-8") as f:
                json.dump(calc_trace, f, ensure_ascii=False, indent=2)
            results["calc_trace"] = calc_trace

            results["verdict"] = verdict
            with open(self.output_dir / "verdict.json", "w", encoding="utf-8") as f:
                json.dump(verdict, f, ensure_ascii=False, indent=2)
            print(f"风控结论: {verdict['grade']}（{verdict['score']} 分）")

            # 不管有没有财务数据，都生成报告
            reporter = ReportGenerator(
                analysis_result=results.get("financial"),  # 可能是None
                redflag_hits=results.get("redflags", []),
                crosscheck_df=results.get("crosscheck"),
                legal_analyzer=results.get("legal"),
                cost_verify_df=results.get("cost_verify"),
                scanner=scanner,
                ops_analyzer=results.get("operations"),
                llm=self.llm,
                ota_analyzer=results.get("ota"),
                mgmt_analyzer=results.get("management"),
                industry=self.industry,
                verdict=verdict,
                calc_trace=calc_trace,
                factset=factset,
                fact_summary=fact_summary,
            )
            report_path = reporter.generate(output_dir=str(self.output_dir), company_name=company_name)
            results["report_path"] = str(report_path)
            print(f"报告已生成: {report_path}")
            add_step("报告生成", "done", f"生成{company_name}尽调报告")

            # LLM润色（可选）
            if self.llm and self.llm.available:
                print("LLM润色报告中...")
                md_content = Path(report_path).read_text(encoding="utf-8")
                polished = self.llm.polish_report(md_content)
                polished_path = Path(report_path).with_suffix(".polished.md")
                polished_path.write_text(polished, encoding="utf-8")
                print(f"润色版报告: {polished_path}")
        except Exception as e:
            print(f"❌ Step 6 失败: {e}")
            add_step("报告生成", "error", str(e), level="fatal")
        
        print()
        results["status"] = "completed"

        return results

    def run(self, data_dir: str | Path) -> dict:
        """外层包一层：不管成功失败都写 meta.json，前端永远能读到状态和耗时。"""
        self._steps_status = []
        self._company_name = "未知项目"
        self._error = ""
        self._error_step = ""
        try:
            return self._run_impl(data_dir)
        except Exception as exc:
            import traceback
            self._error = f"{type(exc).__name__}: {exc}"
            self._traceback = traceback.format_exc()[-3000:]
            raise
        finally:
            self._write_meta()

    def _write_meta(self):
        import json
        import time
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            meta_path = self.output_dir / "meta.json"
            total_seconds = int(time.time() - self._start_time) if hasattr(self, "_start_time") else 0
            error = getattr(self, "_error", "")
            steps = getattr(self, "_steps_status", [])
            levels = [step.get("level") for step in steps if step.get("status") == "error"]

            failed_step = ""
            degraded = []
            for step in steps:
                if step.get("status") != "error":
                    continue
                if step.get("level") == "fatal":
                    failed_step = step.get("name", "")
                elif step.get("level") == "degraded":
                    degraded.append(step.get("name", ""))

            if error:
                status = "failed"
                error_level = "fatal"
            elif "degraded" in levels:
                status = "completed"
                error_level = "degraded"
            elif "warning" in levels:
                status = "completed"
                error_level = "warning"
            else:
                status = "completed"
                error_level = ""

            meta = {
                "company_name": getattr(self, "_company_name", "未知项目"),
                "status": status,
                "error": error,
                "error_level": error_level,
                "failed_step": failed_step,
                "degraded_modules": degraded,
                "total_seconds": total_seconds,
                "steps_status": steps,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"写meta失败: {e}")
