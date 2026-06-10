# Natural-language command routing (FunctionGemma)

This package fine-tunes [`google/functiongemma-270m-it`](https://huggingface.co/google/functiongemma-270m-it)
— a 270M Gemma 3 specialised for function calling — to turn plain-English
requests into the flat-grammar command lines the simulator already speaks.

> "spin up R7 and peer it with R1" → `router R7 as 1` → `peer R7 R1`

It is **optional**: without the `gemma` extra and a trained checkpoint, the app
behaves exactly as before (an unknown verb is just an error). When both are
present, any line the parser doesn't recognise falls through to the model.

## How it fits together

| File | Role |
| --- | --- |
| `tools.py` | The tool surface — one typed function per command verb. Its signatures + docstrings become the JSON schema the model sees; each body renders the matching `commands.py` line. **Single source of truth.** No torch. |
| `dataset.py` | `build_conversations(size=…)` — synthesises natural-language ↔ tool-call training chats. `size` is `SMALL`/`MEDIUM`/`LARGE`/`MAXIMUM`; each command's share scales with its template-space size. Pure Python; run `python -m gemma.dataset` to eyeball samples. |
| `modal_train.py` | Fine-tunes on an A100 via [Modal](https://modal.com), masking the prompt so loss falls only on the assistant tool call. Saves a checkpoint to the `bgpsim-models` volume. |
| `runner.py` | `GemmaRouter` — loads the checkpoint, shows it the same tools, generates one call, parses FunctionGemma's `<start_function_call>call:NAME{...}` output, maps it back to a command line. Runs on **MLX** on Apple Silicon (~2.5x faster than PyTorch-MPS) and falls back to PyTorch elsewhere; the backend is imported lazily. |
| `eval.py` | Accuracy harness over a held-out (different-seed) set: tool accuracy, exact command accuracy, valid-grammar rate. |

The trainer and the runner build their tool schemas from the *same* `TOOLS`
list, so what the model is trained on and what it's prompted with can't drift.

## Workflow

### 1. Train (on Modal)

Needs a Modal account and a `huggingface-secret` (for the gated Gemma weights):

```bash
uvx modal run gemma/modal_train.py                       # sweep SMALL,MEDIUM,LARGE
uvx modal run gemma/modal_train.py --sizes LARGE --epochs 8   # one size
```

Each size lands in its own size-tagged checkpoint on the `bgpsim-models`
volume — `gemma-bgpsim-ft-<size>-n<examples>` (e.g. `gemma-bgpsim-ft-large-n1138`)
— so sweep runs don't clobber each other. Every checkpoint carries a
`metrics.json` (dataset size, train flags, and held-out accuracy on a *fixed*
eval set), so a later size-vs-accuracy report assembles straight from the
volume. The run prints a comparison table at the end.

### 2. Fetch the checkpoint locally

The UI loads from `gemma-bgpsim-ft/`, so fetch the size you want under that name:

```bash
uvx modal volume get bgpsim-models gemma-bgpsim-ft-large-n1138 ./gemma-bgpsim-ft
```

`gemma-bgpsim-ft/` is git-ignored. Its presence is what flips the UI's
natural-language fallback on. Re-score any saved checkpoint without retraining:

```bash
uvx modal run gemma/modal_train.py::eval_only --ckpt /models/gemma-bgpsim-ft-large-n1138
```

### 3. Evaluate

```bash
uv run --extra gemma python -m gemma.eval --model gemma-bgpsim-ft
# add --verbose to print every miss
```

### 4. Try it / use it in the app

```bash
uv run --extra gemma python -m gemma.runner "cut the cable between R1 and R2"
uv run --extra gemma main.py     # type plain English at the command bar
```

## Notes

- **Format.** Tools are passed via `apply_chat_template(..., tools=...)`; the
  model emits `<start_function_call>call:NAME{key:<escape>value<escape>}<end_function_call>`.
  `runner.parse_tool_call()` handles that (and bare numeric args).
- **Single-step only.** FunctionGemma isn't trained for multi-turn or chained
  calls, so each line maps to exactly one command. Compound requests like the
  "spin up and peer" example above need two lines.
- **Editing the grammar?** Add/rename a verb in `commands.py`, mirror it in
  `tools.py` (+ a generator in `dataset.py`), then retrain.
