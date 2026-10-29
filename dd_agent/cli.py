"""
CLI入口：python -m dd_agent scan 文件夹路径
"""
import click
from pathlib import Path
from rich.console import Console
from rich.table import Table

from .orchestrator import DDAgent

console = Console()


@click.group()
def cli():
    """DD-Agent 智能财务尽调系统"""
    pass


@cli.command()
@click.argument("data_dir", type=click.Path(exists=True))
@click.option("--output", "-o", default="./output", help="报告输出目录")
@click.option("--industry", "-i", default="generic",
              help="行业包：generic / hotel / culture-tourism / investment")
def scan(data_dir, output, industry):
    """扫描文件夹并生成尽调报告"""
    folder = Path(data_dir).resolve()
    console.print(f"[bold blue]DD-Agent 智能财务尽调系统[/bold blue]")
    console.print(f"📂 数据目录: {folder}")
    console.print(f"📁 输出目录: {output}")
    console.print(f"🏷️  行业包: {industry}")
    console.print()

    agent = DDAgent(output_dir=output, industry=industry)
    result = agent.run(data_dir)

    console.print()
    console.print("=" * 60)
    console.print("[bold green]✅ 尽调完成！[/bold green]")
    console.print("=" * 60)
    console.print(f"报告路径: {result.get('report_path', 'N/A')}")

    # 打印红旗摘要
    redflags = result.get("redflags", [])
    if redflags:
        console.print()
        table = Table(title="🚩 红旗信号摘要")
        table.add_column("严重程度", style="bold")
        table.add_column("规则名称")
        table.add_column("说明")
        for rf in redflags:
            color = {"high": "red", "medium": "yellow", "low": "blue"}.get(rf.severity, "white")
            table.add_row(
                f"[{color}]{rf.severity.upper()}[/{color}]",
                rf.name,
                rf.explanation[:60]
            )
        console.print(table)


@cli.command()
@click.argument("data_dir", type=click.Path(exists=True))
@click.option("--industry", "-i", default="generic", help="行业包：generic / hotel / culture-tourism / investment / agency-procurement")
def inventory(data_dir, industry):
    """只做目录盘点，不生成报告"""
    from .inventory.scanner import FileScanner
    scanner = FileScanner(Path(data_dir), industry=industry)
    inv = scanner.scan()
    print(inv.summary())


@cli.command()
@click.argument("results_root", type=click.Path(exists=True))
def portfolio(results_root):
    """多项目组合视图"""
    from .monitor import portfolio as build_portfolio
    rows = build_portfolio(results_root)
    table = Table(title="多项目组合视图")
    table.add_column("任务")
    table.add_column("公司/项目")
    table.add_column("行业")
    table.add_column("等级")
    table.add_column("分数")
    table.add_column("风险数")
    for row in rows:
        table.add_row(
            str(row["id"]), str(row["company"]), str(row["industry"] or "-"),
            str(row["grade"] or "-"), str(row["score"] if row["score"] is not None else "-"),
            str(row["risk_count"]),
        )
    console.print(table)


@cli.command(name="diff")
@click.argument("old_task", type=click.Path(exists=True))
@click.argument("new_task", type=click.Path(exists=True))
def diff_tasks(old_task, new_task):
    """同一项目两次分析对比"""
    from .monitor import compare
    from rich.json import JSON
    result = compare(old_task, new_task)
    console.print(JSON.from_data(result))


if __name__ == "__main__":
    cli()
