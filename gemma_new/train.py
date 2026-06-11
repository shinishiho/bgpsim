"""Notebook-faithful SFT core for the BGP-sim FunctionGemma router.

This mirrors Google's
``Finetune FunctionGemma 270M for Mobile Actions`` notebook step for step, only
swapping the data source (our template engine, see `dataset.build_dataset`) for
the downloaded google/mobile-actions jsonl:

  1. build chat records and render them to ``prompt`` / ``completion`` pairs
     (`format.apply_format`);
  2. size ``max_length`` from the longest example (`format.max_token_count`);
  3. SFT with ``completion_only_loss=True`` and a cosine schedule -- the prompt
     is masked, loss is on the assistant call only, no hand-patched
     ``{% generation %}`` markers or custom collator;
  4. score the held-out split with the notebook's name+argument metric, plus our
     exact command-line accuracy (`eval.score_dataset`).

`run_training` is a plain function with no Modal dependency, so it runs anywhere
with a GPU (the Modal wrapper in `modal_train.py` just calls it on a B200).
"""

from __future__ import annotations

import argparse
import datetime
import json

from gemma_new.tools import MODEL_ID, TOOLS


def build_tool_schemas() -> list[dict]:
    """The 22-tool surface as JSON schemas, one declaration per tool.

    `get_json_schema` emits each tool's real params only -- the same per-tool
    declarations google/mobile-actions ships (no cross-tool param union). The
    chat template renders this block identically into the train and inference
    prompts.
    """
    from transformers.utils import get_json_schema

    return [get_json_schema(fn) for fn in TOOLS]


def run_training(
    output_dir: str,
    *,
    size: str = "MEDIUM",
    epochs: float = 2.0,
    batch_size: int = 4,
    effective_batch: int = 32,
    lr: float = 1e-5,
    seed: int = 42,
    use_liger: bool = False,
) -> dict:
    """Fine-tune one checkpoint and return a self-describing metrics record.

    `batch_size` is the per-device micro-batch (the memory knob -- it sizes the
    262k-vocab logit tensor); `effective_batch` is the optimizer batch, reached by
    deriving ``gradient_accumulation_steps = effective_batch // batch_size``. So
    `batch_size` can be tuned to the GPU without changing the optimization regime.
    Default `effective_batch=32` matches the notebook (its 4 x 8).

    Saves the model + tokenizer to `output_dir`, scores the held-out (``eval``)
    split in place on the same device, and writes ``metrics.json`` alongside the
    weights so a later report can be assembled straight from the artifacts.
    """
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    from gemma_new.dataset import build_dataset
    from gemma_new.eval import score_dataset
    from gemma_new.format import format_dataset, max_token_count

    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    tools = build_tool_schemas()

    # Build -> format (prompt/completion/split) over plain Python dicts, exactly
    # as the notebook's `dataset.map(apply_format)` does.
    records = build_dataset(size=size, seed=seed)
    formatted = format_dataset(records, tok, tools)
    max_len = max_token_count(formatted, tok)

    train_rows = [f for f in formatted if f["split"] == "train"]
    eval_rows = [f for f in formatted if f["split"] == "eval"]
    # Raw records for the held-out split, kept for the final name/arg/command
    # scoring (it needs the gold tool_calls, not just prompt/completion strings).
    eval_records = [rec for rec, f in zip(records, formatted) if f["split"] == "eval"]

    def _pc(rows: list[dict]) -> Dataset:
        return Dataset.from_list(
            [{"prompt": r["prompt"], "completion": r["completion"]} for r in rows]
        )

    train_ds, eval_ds = _pc(train_rows), _pc(eval_rows)

    # batch_size is the memory knob; accum makes up the rest of the effective batch.
    grad_accum = max(1, effective_batch // batch_size)

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        attn_implementation="eager",  # Gemma trains more stably with eager attn
        device_map="auto",  # land on the GPU at load
    )
    model.config.pad_token_id = tok.pad_token_id
    print(
        f"device={model.device} | {len(train_ds)} train / {len(eval_ds)} eval "
        f"| max_length={max_len} | batch {batch_size} x accum {grad_accum} "
        f"= effective {batch_size * grad_accum}"
    )

    args = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        lr_scheduler_type="cosine",  # cosine is often better for full FT
        max_length=max_len,  # sized from the data so the call is never truncated
        packing=False,
        completion_only_loss=True,  # mask the prompt, train on the call only
        gradient_checkpointing=True,
        optim="adamw_torch_fused",
        bf16=True,
        logging_strategy="steps",
        logging_steps=50,
        eval_strategy="steps" if len(eval_ds) else "no",
        eval_steps=0.25,  # fraction of total steps -> 4 evals/run, size-independent
        save_strategy="no",  # saved explicitly below
        report_to="none",
        seed=seed,
        # Liger's fused linear cross-entropy never materializes the full
        # [seq, 262k] logit tensor -- the difference between OOM and a 2.2 GB
        # step on a 6 GB card. Off by default so the Modal/B200 path stays
        # exactly notebook-faithful; flip on for local low-VRAM training.
        use_liger_kernel=use_liger,
    )

    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds if len(eval_ds) else None,
        processing_class=tok,
    )
    trainer.train()
    trainer.save_model(output_dir)
    tok.save_pretrained(output_dir)
    print(f"saved fine-tuned model to {output_dir}")

    metrics = (
        score_dataset(eval_records, tok, model, tools) if eval_records else {"n": 0}
    )

    record = {
        "output_dir": output_dir,
        "dataset_size": size,
        "n_examples": len(records),
        "n_train": len(train_ds),
        "n_eval": len(eval_ds),
        "epochs": epochs,
        "batch_size": batch_size,
        "effective_batch": batch_size * grad_accum,
        "gradient_accumulation_steps": grad_accum,
        "learning_rate": lr,
        "seed": seed,
        "model_id": MODEL_ID,
        "max_length": max_len,
        "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **metrics,
    }
    with open(f"{output_dir}/metrics.json", "w") as f:
        json.dump(record, f, indent=2)
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description="Fine-tune FunctionGemma locally.")
    ap.add_argument("--output-dir", default="gemma-bgpsim-ft")
    ap.add_argument("--size", default="SMALL")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--batch-size", type=int, default=4, help="per-device micro-batch")
    ap.add_argument("--effective-batch", type=int, default=32, help="optimizer batch")
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--liger",
        action="store_true",
        help="fused linear cross-entropy (fits full FT in ~6 GB VRAM)",
    )
    a = ap.parse_args()
    record = run_training(
        a.output_dir,
        size=a.size,
        epochs=a.epochs,
        batch_size=a.batch_size,
        effective_batch=a.effective_batch,
        lr=a.lr,
        seed=a.seed,
        use_liger=a.liger,
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
