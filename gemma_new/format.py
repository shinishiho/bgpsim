"""Turn FunctionGemma chat records into SFT prompt/completion pairs.

This is the Google notebook's ``apply_format`` step, lifted out verbatim in
spirit: render each record with the model's tool-aware chat template once with
the assistant turn and once without, and take the completion as the tail the
assistant turn added. Training then runs on the ``prompt``/``completion`` columns
with ``completion_only_loss=True`` -- no hand-patched ``{% generation %}`` markers,
no custom collator.

The shared ``tools`` block is passed in (built once from the tool surface) rather
than stored per record; the chat template folds it into both renders identically,
so the completion is unaffected and the prompt carries the same declarations the
runner shows at inference.
"""

from __future__ import annotations


def apply_format(record: dict, tokenizer, tools: list[dict]) -> dict:
    """One record -> ``{"prompt", "completion", "split"}``.

    `prompt` is everything up to and including the ``<start_of_turn>model``
    generation prompt; `completion` is the assistant tool call the model must
    learn to produce. `split` echoes the record's train/eval metadata so the
    caller can filter, exactly as the notebook does.
    """
    messages = record["messages"]

    prompt_and_completion = tokenizer.apply_chat_template(
        messages,
        tools=tools,
        tokenize=False,
        # The full conversation, assistant turn included; no trailing generation
        # prompt is added (we already have the assistant content).
        add_generation_prompt=False,
    )
    prompt = tokenizer.apply_chat_template(
        messages[:-1],
        tools=tools,
        tokenize=False,
        # Stop just before the assistant turn, but include the
        # "<start_of_turn>model" generation prompt so the boundary is where the
        # model would start generating at inference.
        add_generation_prompt=True,
    )
    completion = prompt_and_completion[len(prompt):]

    return {"prompt": prompt, "completion": completion, "split": record["metadata"]}


def format_dataset(records: list[dict], tokenizer, tools: list[dict]) -> list[dict]:
    """Map `apply_format` over every record (plain Python, like the notebook)."""
    return [apply_format(r, tokenizer, tools) for r in records]


def max_token_count(formatted: list[dict], tokenizer, headroom: int = 100) -> int:
    """Longest ``prompt + completion`` in tokens, plus headroom.

    Mirrors the notebook: pick the longest example, count its tokens, and add a
    margin. `SFTConfig.max_length` is set to this so the assistant call (which
    sits at the very end of the sequence) is never truncated -- truncation there
    would mask every label to -100 and zero the loss.
    """
    longest = max(formatted, key=lambda ex: len(ex["prompt"] + ex["completion"]))
    n = len(tokenizer.tokenize(longest["prompt"] + longest["completion"]))
    return n + headroom
