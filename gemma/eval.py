"""Evaluate a fine-tuned BGP-sim router.

    uv run --extra gemma python -m gemma.eval --model gemma-bgpsim-ft

Generates a held-out set of natural-language requests (a different seed than
training, so the phrasings are unseen), asks the model to translate each, and
reports three numbers:

  * tool accuracy   -- did it pick the right verb?
  * command accuracy-- did the rendered command line match exactly?
  * valid-grammar   -- does the rendered line parse without an "unknown command"?

The last check round-trips through `commands.apply_command` so a regression in
either the tool surface or the parser shows up here too.
"""

from __future__ import annotations

import argparse

from gemma.dataset import SIZES, build_conversations
from gemma.runner import GemmaRouter
from gemma.tools import TOOLS, to_command_line


def _expected(conv: dict) -> tuple[str, str, str]:
    """(query, expected_tool, expected_command_line) for one conversation."""
    query = conv["messages"][1]["content"]
    call = conv["messages"][2]["tool_calls"][0]["function"]
    return query, call["name"], to_command_line(call["name"], call["arguments"])


def _is_valid_grammar(line: str) -> bool:
    """True when the parser recognises the verb (args may still be rejected)."""
    from models.world import World
    from commands import apply_command

    result = apply_command(World(), line)
    return result.ok or not result.error.startswith("unknown command")


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate the BGP-sim tool router.")
    ap.add_argument(
        "--model",
        default="gemma-bgpsim-ft",
        help="Path to the fine-tuned checkpoint (default: gemma-bgpsim-ft).",
    )
    ap.add_argument(
        "--size", choices=SIZES, default="SMALL", help="Held-out set size (default: SMALL)."
    )
    ap.add_argument("--seed", type=int, default=1234, help="Held-out generation seed.")
    ap.add_argument("--verbose", action="store_true", help="Print every miss.")
    args = ap.parse_args()

    router = GemmaRouter(model_path=args.model)
    print(f"loading {router.model_path} ...")
    router.load()

    examples = build_conversations(size=args.size, seed=args.seed)
    print(f"evaluating {len(examples)} examples ({len(TOOLS)} tools)\n")

    tool_hits = cmd_hits = grammar_hits = 0
    per_tool_total: dict[str, int] = {}
    per_tool_hit: dict[str, int] = {}

    for conv in examples:
        query, want_tool, want_cmd = _expected(conv)
        per_tool_total[want_tool] = per_tool_total.get(want_tool, 0) + 1

        t = router.translate(query)
        tool_ok = t.ok and t.tool == want_tool
        cmd_ok = t.ok and t.command == want_cmd
        grammar_ok = t.ok and _is_valid_grammar(t.command)

        tool_hits += tool_ok
        cmd_hits += cmd_ok
        grammar_hits += grammar_ok
        per_tool_hit[want_tool] = per_tool_hit.get(want_tool, 0) + tool_ok

        if args.verbose and not cmd_ok:
            got = f"{t.tool} -> `{t.command}`" if t.ok else f"(no call) {t.raw!r}"
            print(f"  MISS {query!r}\n    want {want_tool} -> `{want_cmd}`\n    got  {got}")

    n = len(examples)
    print("\n" + "=" * 60)
    print(f"tool accuracy:    {tool_hits}/{n} = {tool_hits / n:.1%}")
    print(f"command accuracy: {cmd_hits}/{n} = {cmd_hits / n:.1%}")
    print(f"valid grammar:    {grammar_hits}/{n} = {grammar_hits / n:.1%}")
    print("=" * 60)
    print("\nper-tool routing accuracy:")
    for tool in sorted(per_tool_total):
        hit, tot = per_tool_hit.get(tool, 0), per_tool_total[tool]
        flag = "" if hit == tot else "  <-- review"
        print(f"  {tool:22s} {hit}/{tot}{flag}")


if __name__ == "__main__":
    main()
