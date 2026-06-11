"""Score a FunctionGemma checkpoint, following the Google notebook's method.

The notebook parses the model's ``<start_function_call>call:NAME{...}<end_function_call>``
output back into ``{name, arguments}``, then compares predicted vs. gold on two
axes: the function name and the (order-insensitive) argument dict. We keep that
exact scheme and add one project-specific axis -- exact command-line accuracy via
``tools.to_command_line`` -- since the whole point of the router is to emit a
runnable command line.

One deviation from the notebook's ``extract_function_call``: our tools take ints
(``asn``, ``cost``, ``value``) and FunctionGemma emits those unescaped (``asn:1``,
not ``asn:<escape>1<escape>``). The notebook's regex only captures
``<escape>``-wrapped strings -- fine for mobile-actions' all-string args -- so it
would silently drop every integer. We capture both escaped and bare args, and
drop any ``None`` value defensively (on both predicted and gold) before comparing.
"""

from __future__ import annotations

import re

from gemma_new.tools import to_command_line

# `<start_function_call>call:NAME{ ... }<end_function_call>` is the wire format;
# tolerate a bare `call:NAME{...}` too, in case special tokens were stripped.
_CALL_RE = re.compile(r"call:\s*([A-Za-z_]\w*)\s*\{(.*?)\}", re.DOTALL)
_ESCAPED_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*:\s*<escape>(.*?)<escape>", re.DOTALL)
_BARE_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*:\s*([^,{}<]+)")


def extract_function_call(model_output: str) -> list[dict]:
    """Parse call markers into ``[{"function": {"name", "arguments"}}]``.

    Same shape the notebook returns. Arguments are pulled in two passes -- escaped
    string values first, then any bare numeric/enum values that weren't wrapped --
    and ``None`` union fillers are dropped, leaving only the slots the verb fills.
    """
    results = []
    for name, body in _CALL_RE.findall(model_output):
        arguments: dict[str, str] = {}
        for key, val in _ESCAPED_ARG_RE.findall(body):
            if val.strip() != "None":
                arguments[key] = val.strip()
        stripped = _ESCAPED_ARG_RE.sub("", body)
        for key, val in _BARE_ARG_RE.findall(stripped):
            val = val.strip()
            if val != "None" and key not in arguments:
                arguments[key] = val
        results.append({"function": {"name": name, "arguments": arguments}})
    return results


def extract_text(model_output: str) -> str | None:
    """Plain-text response (no tool call), with ``<end_of_turn>`` stripped.

    Kept for parity with the notebook; our dataset is tool-calls only, so this is
    ``None`` in practice.
    """
    if not model_output or model_output.startswith("<start_function_call>"):
        return None
    return model_output.replace("<end_of_turn>", "").strip()


def _norm_args(arguments: dict) -> dict[str, str]:
    """Drop ``None`` union fillers and stringify the rest for comparison.

    Applied to both predicted and gold so an int ``1`` and the decoded string
    ``"1"`` compare equal and the dropped fillers never cause a spurious miss.
    """
    return {k: str(v) for k, v in arguments.items() if v is not None}


def _gold_call(messages: list[dict]) -> dict:
    """The single gold ``{name, arguments}`` from the assistant turn."""
    return messages[-1]["tool_calls"][0]["function"]


def score_dataset(
    records: list[dict],
    tokenizer,
    model,
    tools: list[dict],
    *,
    batch_size: int = 32,
    max_new_tokens: int = 160,
    show_misses: int = 20,
) -> dict:
    """Greedy-decode every record and score it against its gold call.

    `records` are raw chat records (``messages`` + ``metadata``), so the eval
    prompt is built here from ``messages[:-1]`` -- identical to what the runner
    sends at inference. Reports function-name accuracy and argument accuracy (the
    notebook's two axes) plus exact command-line accuracy.
    """
    import torch

    model.eval()

    # Left-pad so each row's generated tokens share an offset and one slice
    # recovers the whole batch's continuation. The chat template already prepends
    # <bos>, so re-tokenize with add_special_tokens=False to avoid a doubled BOS.
    prev_side = tokenizer.padding_side
    tokenizer.padding_side = "left"
    prompts: list[str] = []
    golds: list[dict] = []
    for r in records:
        prompts.append(
            tokenizer.apply_chat_template(
                r["messages"][:-1],
                tools=tools,
                add_generation_prompt=True,
                tokenize=False,
            )
        )
        golds.append(_gold_call(r["messages"]))

    raws: list[str] = []
    for i in range(0, len(prompts), batch_size):
        inputs = tokenizer(
            prompts[i : i + batch_size],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=4096,
            add_special_tokens=False,
        ).to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        gen = out[:, inputs["input_ids"].shape[1] :]
        raws.extend(tokenizer.decode(g, skip_special_tokens=False) for g in gen)
    tokenizer.padding_side = prev_side

    name_hits = arg_hits = cmd_hits = 0
    per_tool_total: dict[str, int] = {}
    per_tool_cmd_hit: dict[str, int] = {}
    misses: list[tuple[str, str, str]] = []  # (query, gold_cmd, pred_cmd)

    for r, gold, raw in zip(records, golds, raws):
        query = r["messages"][1]["content"]
        want_name = gold["name"]
        want_args = _norm_args(gold["arguments"])
        want_cmd = to_command_line(want_name, gold["arguments"])
        per_tool_total[want_name] = per_tool_total.get(want_name, 0) + 1

        calls = extract_function_call(raw)
        name_ok = arg_ok = cmd_ok = False
        pred_cmd = "<no tool call>"
        if calls:
            pred = calls[0]["function"]
            name_ok = pred["name"] == want_name
            arg_ok = name_ok and _norm_args(pred["arguments"]) == want_args
            try:
                pred_cmd = to_command_line(pred["name"], pred["arguments"])
                cmd_ok = pred_cmd == want_cmd
            except (KeyError, TypeError) as e:
                pred_cmd = f"<{type(e).__name__}: {e}>"

        name_hits += name_ok
        arg_hits += arg_ok
        cmd_hits += cmd_ok
        per_tool_cmd_hit[want_name] = per_tool_cmd_hit.get(want_name, 0) + cmd_ok
        if not cmd_ok:
            misses.append((query, want_cmd, pred_cmd))

    n = len(records)
    print("=" * 72)
    print(
        f"EVAL  name {name_hits}/{n}={name_hits / n:.1%}"
        f"  |  args {arg_hits}/{n}={arg_hits / n:.1%}"
        f"  |  command {cmd_hits}/{n}={cmd_hits / n:.1%}"
    )
    print("-" * 72)
    print(f"  {'tool':22s} {'cmd':>9s}")
    for tool in sorted(per_tool_total):
        tot = per_tool_total[tool]
        chit = per_tool_cmd_hit.get(tool, 0)
        flag = "" if chit == tot else "  <-- review"
        print(f"  {tool:22s} {chit:>4d}/{tot:<4d}{flag}")
    if misses and show_misses:
        print("-" * 72)
        print(f"command mismatches ({len(misses)} total, showing {show_misses}):")
        for query, want_cmd, pred_cmd in misses[:show_misses]:
            print(f"  q={query!r}\n     want={want_cmd!r}  pred={pred_cmd!r}")
    print("=" * 72)

    return {
        "n": n,
        "name_accuracy": name_hits / n,
        "argument_accuracy": arg_hits / n,
        "command_accuracy": cmd_hits / n,
    }
