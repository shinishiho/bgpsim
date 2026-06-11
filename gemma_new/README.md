# gemma_new — notebook-faithful FunctionGemma fine-tuning

A clean rewrite of the training/eval pipeline that follows Google's
[*Finetune FunctionGemma 270M for Mobile Actions*](../gemma/[FunctionGemma]Finetune_FunctionGemma_270M_for_Mobile_Actions_with_Hugging_Face.ipynb)
notebook step for step. The only departure from the notebook is the data source:
instead of downloading the `google/mobile-actions` jsonl, we generate our own
records from a combinatorial template engine over the 22 BGP-sim tools.

## Pipeline

| stage | module | notebook counterpart |
|-------|--------|----------------------|
| tool surface | [tools.py](tools.py) | the `tools` declarations |
| build records | [dataset.py](dataset.py) `build_dataset` | downloaded `dataset.jsonl` |
| format | [format.py](format.py) `apply_format` | `apply_format` / `dataset.map` |
| size `max_length` | [format.py](format.py) `max_token_count` | "longest example" cell |
| train | [train.py](train.py) `run_training` | `SFTConfig` + `SFTTrainer` |
| score | [eval.py](eval.py) `score_dataset` | `extract_function_call` + `get_scored_data_frame` |
| run on GPU | [modal_train.py](modal_train.py) | the Colab runtime |

Each record is `{"messages": [...], "metadata": "train"|"eval"}`, the same shape
`google/mobile-actions` ships. `apply_format` renders it to `prompt` / `completion`
strings; training masks the prompt (`completion_only_loss=True`) and learns the
assistant call only — no hand-patched `{% generation %}` markers, no Liger kernel,
no custom collator.

### No parameter union

Each call lists only the arguments the verb uses, and each declaration only its
own params — exactly like `google/mobile-actions`. The `None`-filled "union" you
may see in the HF dataset viewer (or from `Dataset.from_list`) is an Arrow/Parquet
schema-inference artifact — the viewer says *"Auto-converted to Parquet"* — that
appears when the data is loaded through a *typed* `messages` column. The notebook
sidesteps it by loading the jsonl as a plain `text` column and `json.loads`-ing
each line; `format.py` does the equivalent by rendering to prompt/completion
strings before any `Dataset` is built, so the union never creeps in.

## Usage

```bash
# inspect the generated data (CPU, no torch)
uv run python -m gemma_new.dataset

# full sweep on Modal B200 (one checkpoint per size, with metrics.json each)
uvx modal run gemma_new/modal_train.py --sizes SMALL,MEDIUM,LARGE

# score a saved checkpoint without retraining
uvx modal run gemma_new/modal_train.py::eval_only --ckpt /models/gemma-bgpsim-ft-new-medium

# train locally on a GPU/MPS box (small smoke run)
uv run --extra gemma python -m gemma_new.train --size SMALL --epochs 1
```

Fetch a checkpoint for the app (the runner loads `./gemma-bgpsim-ft`):

```bash
uvx modal volume get bgpsim-models gemma-bgpsim-ft-new-medium ./gemma-bgpsim-ft
```
