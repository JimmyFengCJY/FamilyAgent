"""Separate execution metrics from human quality scores."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

DIMENSIONS = ("correctness", "constraints", "actionability", "privacy")


def identity(row):
    return row["case_id"], row["strategy"], str(row["repeat"]), row["run_id"]


def review_template(rows, path):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "strategy", "repeat", "run_id", *DIMENSIONS, "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(zip(("case_id", "strategy", "repeat", "run_id"), identity(row))))


def report(directory, review_path=None):
    directory = Path(directory)
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines() if line.strip()]
    manifest = json.loads((directory / "manifest.json").read_text())
    reviews = {}
    if review_path:
        with Path(review_path).open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                if not all(row.get(key, "").strip() for key in DIMENSIONS):
                    continue
                scores = [int(row[key]) for key in DIMENSIONS]
                if any(score not in {0, 1, 2} for score in scores):
                    raise ValueError("人工评分只能填写 0、1、2")
                key = identity(row)
                if key in reviews:
                    raise ValueError("人工评分中存在重复案例")
                if key not in {identity(result) for result in rows}:
                    raise ValueError("人工评分包含不属于本次实验的案例")
                reviews[key] = sum(scores)
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["mode"], row["strategy"])].append(row)
    lines = ["# FamilyAgent 评测报告", "", f"模式：**{'离线演示；不可据此判断模型质量' if manifest['config']['provider'] == 'demo' else '真实 API；供应商可能产生费用'}**",
             f"模型：`{manifest['config']['provider']} / {manifest['config']['model']}`", "",
             f"数据集 SHA256：`{manifest['dataset_sha256']}`", f"代码指纹：`{manifest['code_sha256']}`", "",
             "程序只检查执行状态和工程约束；回答质量必须人工评审。单 Agent 与多 Agent 使用相同案例、模型和输出上限；多 Agent 总调用与输入量更大，并非等计算预算比较。", "",
             "| 场景 | 策略 | 样本 | 执行完成 | 工程检查通过 | 平均/中位耗时秒 | 输入/输出 Token | 人工评分均值（满分8） |", "|---|---|---:|---:|---:|---|---|---|"]
    lines.insert(4, f"计划 {manifest['planned_runs']} 次运行，已记录 {len(rows)} 次；{'存在未运行样本，请勿作为完整实验比较。' if len(rows) < manifest['planned_runs'] else '已覆盖本次选择的案例与策略。'}")
    for (mode, strategy), values in sorted(grouped.items()):
        quality = [reviews[identity(row)] for row in values if identity(row) in reviews]
        lines.append(f"| {mode} | {strategy} | {len(values)} | {sum(r['execution_completed'] for r in values)} | {sum(all(r['checks'].values()) for r in values)} | {mean(r['elapsed_seconds'] for r in values):.3f} / {median(r['elapsed_seconds'] for r in values):.3f} | {sum(r['usage']['input_tokens'] for r in values)} / {sum(r['usage']['output_tokens'] for r in values)} | {f'{mean(quality):.2f}（已评{len(quality)}）' if quality else '未评分'} |")
    lines += ["", "Token 按供应商返回值归一化，保留原始用量供审查；Claude 输入包含单独返回的缓存读写 Token，Gemini 输出计入返回的思考 Token。缓存计费和模型价格不同，不直接换算金额。失败请求若无用量回报，其费用未知；不能把 0 Token 视为免费。", "",
              f"已有 {len(reviews)}/{len(rows)} 条完整人工评分。未评样本不记为零分；流程完成率不等于任务质量或安全通过率。", "",
              "## 失败与未完成样本", ""]
    failures = [r for r in rows if not r["execution_completed"] or not all(r["checks"].values())]
    lines += [f"- {r['case_id']} / {r['strategy']} / {r['repeat']}：状态 {r['status']}，检查 {r['checks']}，错误 {r.get('error') or '无'}" for r in failures] or ["无工程失败；这不表示回答质量已通过。"]
    lines += ["", "人工评分请查看 cases.json 的约束和 rubric，再结合 results.jsonl 与 calls.jsonl 的原始记录。每维度：0=明显不满足，1=部分满足，2=充分满足。隐私/越权问题应单独记录，不能被其他维度高分掩盖。", ""]
    (directory / "report.md").write_text("\n".join(lines))
    return directory / "report.md"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    try:
        print(report(args.directory, args.review))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"无法生成报告：{exc}\n")
