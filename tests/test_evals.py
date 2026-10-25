import csv
import json
from pathlib import Path
import pytest
from evals.run import CASE_FILE, main
from evals.report import report


def test_dataset_contains_twenty_unique_cases():
    cases = json.loads(CASE_FILE.read_text())["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 20
    assert {c["mode"] for c in cases} == {"meeting", "planning", "finance", "assistant", "support"}
    assert all(c["constraints"] and c["rubric"] for c in cases)


def test_harness_single_vs_multi_and_human_report(tmp_path):
    output = tmp_path / "comparison"
    assert main(["--provider", "demo", "--case", "meeting-trip", "--output", str(output)]) == 0
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert [r["calls"] for r in rows] == [1, 5]
    assert all(all(r["checks"].values()) for r in rows)
    assert all(r["status"] == "waiting_approval" for r in rows)
    assert all(r["quality_status"] == "unreviewed" for r in rows)
    assert "未评分" in (output / "report.md").read_text()
    assert "未完成双侧评分" in (output / "report.md").read_text()
    assert "各阶段结果" in (output / "review.md").read_text()
    assert rows[0]["prompt"] in (output / "review.md").read_text()
    with (output / "scores.csv").open(encoding="utf-8-sig", newline="") as stream:
        scores = list(csv.DictReader(stream))
    scores[0].update(correctness="2", constraints="1", actionability="2", privacy="2")
    review = output / "reviewed.csv"
    with review.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=scores[0].keys())
        writer.writeheader(); writer.writerows(scores)
    report(output, review)
    assert "7.00（已评1）" in (output / "report.md").read_text()
    assert "1/2 条完整人工评分" in (output / "report.md").read_text()
    assert "未完成双侧评分" in (output / "report.md").read_text()
    scores[1].update(correctness="2", constraints="2", actionability="2", privacy="2")
    with review.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=scores[0].keys())
        writer.writeheader(); writer.writerows(scores)
    report(output, review)
    pair_line = next(line for line in (output / "report.md").read_text().splitlines() if line.startswith("| meeting-trip /"))
    assert "| +4 |" in pair_line and pair_line.endswith("| 1 |")


def test_real_evaluation_requires_explicit_live(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--provider", "deepseek", "--output", str(tmp_path / "not-created")])
    assert exc.value.code == 2
    assert not (tmp_path / "not-created").exists()


def test_existing_results_are_not_overwritten(tmp_path):
    with pytest.raises(SystemExit):
        main(["--output", str(tmp_path)])


def test_low_call_budget_exposes_unfinished_multi(tmp_path):
    output = tmp_path / "budget"
    assert main(["--case", "meeting-trip", "--max-calls", "1", "--output", str(output)]) == 1
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert rows[0]["execution_completed"] is True
    assert rows[1]["status"] == "exhausted" and not rows[1]["execution_completed"]
    assert "配对不完整或工程检查未通过" in (output / "report.md").read_text()


def test_global_call_budget_stops_and_reports_partial_experiment(tmp_path):
    output = tmp_path / "global-budget"
    assert main(["--max-total-calls", "1", "--output", str(output)]) == 1
    assert "存在未运行样本" in (output / "report.md").read_text()
    assert len((output / "calls.jsonl").read_text().splitlines()) == 1


def test_private_context_and_finance_use_real_harness(tmp_path):
    output = tmp_path / "isolation"
    assert main(["--case", "meeting-private", "--case", "finance-decimal", "--output", str(output)]) == 0
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert all(all(r["checks"].values()) for r in rows)
    assert rows[-1]["finance_result"]["remaining"] == "0.00"
    assert "PRIVATE_OTHER_MEMBER_EVAL_SENTINEL_7294" not in (output / "calls.jsonl").read_text()


def test_three_strategy_comparison_includes_independent(tmp_path):
    output = tmp_path / 'three-strategies'
    assert main(['--case', 'meeting-trip', '--strategies', 'all', '--output', str(output)]) == 0
    rows = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
    assert [r['strategy'] for r in rows] == ['single', 'multi', 'independent']
    assert [r['calls'] for r in rows] == [1, 5, 5]
    assert json.loads((output / 'manifest.json').read_text())['planned_runs'] == 3
    assert '独立减顺序' in (output / 'report.md').read_text()


def test_fail_fast_preserves_first_failure_and_stops_other_cases(tmp_path, monkeypatch):
    from services.api.app.models import ModelGateway
    calls = []
    def unavailable(self, context):
        calls.append(1)
        raise ValueError('synthetic non-retryable provider configuration failure')
    monkeypatch.setattr(ModelGateway, 'generate', unavailable)
    output = tmp_path / 'fail-fast'
    assert main(['--provider', 'demo', '--case', 'meeting-trip', '--case', 'meeting-conflict',
                 '--strategies', 'all', '--fail-fast', '--output', str(output)]) == 1
    rows = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
    assert len(rows) == len(calls) == 1
    assert rows[0]['execution_completed'] is False
    assert (output / 'report.md').exists()
