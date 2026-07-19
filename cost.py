import json
from pathlib import Path

MODEL = "claude-haiku-4-5"

# USD per million tokens -- https://platform.claude.com/docs/en/about-claude/pricing
INPUT_PRICE_PER_MTOK = 1.00
OUTPUT_PRICE_PER_MTOK = 5.00


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * INPUT_PRICE_PER_MTOK
            + output_tokens / 1_000_000 * OUTPUT_PRICE_PER_MTOK)


class CostTracker:
    """Accumulates token usage across every Claude call made during one run.

    Only calls that actually happen this run are recorded -- cached/skipped
    work costs nothing, so a fully-cached rerun correctly reports near-$0.
    """

    def __init__(self):
        self._stages: dict[str, dict[str, int]] = {}

    def record(self, stage: str, usage) -> None:
        stats = self._stages.setdefault(stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        stats["calls"] += 1
        stats["input_tokens"] += usage.input_tokens
        stats["output_tokens"] += usage.output_tokens

    def write(self, case: dict, output_path: str) -> str:
        by_stage = {}
        total_calls = total_input = total_output = 0
        for stage, stats in self._stages.items():
            by_stage[stage] = {
                "calls": stats["calls"],
                "input_tokens": stats["input_tokens"],
                "output_tokens": stats["output_tokens"],
                "cost_usd": round(_cost(stats["input_tokens"], stats["output_tokens"]), 6),
            }
            total_calls += stats["calls"]
            total_input += stats["input_tokens"]
            total_output += stats["output_tokens"]

        report = {
            "case": case["name"],
            "model": MODEL,
            "pricing": {
                "input_per_mtok_usd": INPUT_PRICE_PER_MTOK,
                "output_per_mtok_usd": OUTPUT_PRICE_PER_MTOK,
            },
            "by_stage": by_stage,
            "totals": {
                "calls": total_calls,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "cost_usd": round(_cost(total_input, total_output), 6),
            },
        }

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        return output_path
