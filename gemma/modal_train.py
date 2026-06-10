import modal

app = modal.App("bgpsim-functiongemma")

image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install(
        "torch>=2.12",
        "transformers>=4.50",
        "datasets>=2.20",
        "accelerate>=0.30",
        "huggingface_hub",
        "jinja2>=3.1",  # required by transformers to compile the chat template
    )
    .add_local_python_source("gemma")
)

models_vol = modal.Volume.from_name("bgpsim-models", create_if_missing=True)
hf_cache = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
hf_secret = modal.Secret.from_name("huggingface-secret")

# Default checkpoint (back-compat: what the runner expects after a plain fetch).
OUTPUT_DIR = "/models/gemma-bgpsim-ft"

# Held-out eval set, FIXED across every run so accuracies are comparable no
# matter which training size produced the checkpoint. Distinct seed => unseen
# phrasings relative to any training draw.
EVAL_SIZE = "SMALL"
EVAL_SEED = 98765


def checkpoint_dir(size: str, n_examples: int) -> str:
    """Systematic, size-tagged checkpoint path so sweep runs don't clobber and
    the dataset size is legible straight from the volume listing."""
    return f"/models/gemma-bgpsim-ft-{size.lower()}-n{n_examples}"


def _evaluate(
    model, tok, tools, size: str = "SMALL", seed: int = 1234, batch_size: int = 32
) -> dict:
    """Greedy-decode a held-out set and score it (runs on the GPU container).

    The eval examples are generated with a *different* seed than training, so
    the phrasings are unseen. Reports tool-routing accuracy (did it pick the
    right verb?) and exact command accuracy (did the rendered line match?).

    Generation is batched: at batch=1 an 80 GB A100 sits ~idle waiting on the
    decode loop, so we left-pad and run `batch_size` prompts per generate() call.
    """
    import torch

    from gemma.dataset import build_conversations
    from gemma.runner import parse_tool_call
    from gemma.tools import to_command_line

    model.eval()
    examples = build_conversations(size=size, seed=seed)

    # Render every prompt up front, then decode in left-padded batches. Left
    # padding keeps each row's generated tokens at the same offset, so a single
    # slice recovers the continuation for the whole batch. The chat template
    # already prepends <bos>, so re-tokenize with add_special_tokens=False to
    # avoid a doubled BOS.
    prev_side = tok.padding_side
    tok.padding_side = "left"
    golds: list[tuple[str, str, str]] = []  # (query, want_tool, want_cmd)
    prompts: list[str] = []
    for conv in examples:
        query = conv["messages"][1]["content"]
        gold = conv["messages"][2]["tool_calls"][0]["function"]
        want_tool = gold["name"]
        want_cmd = to_command_line(want_tool, gold["arguments"])
        golds.append((query, want_tool, want_cmd))
        prompts.append(
            tok.apply_chat_template(
                conv["messages"][:-1],
                tools=tools,
                add_generation_prompt=True,
                tokenize=False,
            )
        )

    raws: list[str] = []
    for i in range(0, len(prompts), batch_size):
        inputs = tok(
            prompts[i : i + batch_size],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=4096,
            add_special_tokens=False,
        ).to(model.device)
        with torch.no_grad():
            out = model.generate(
                # FunctionGemma emits the full param *union* (unused slots as
                # None) before the real values, so a 3-arg verb like
                # add_static_route needs ~110 tokens just to close its brace.
                # 96 truncated it; 160 leaves headroom.
                **inputs,
                max_new_tokens=160,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        gen = out[:, inputs["input_ids"].shape[1] :]
        raws.extend(tok.decode(g, skip_special_tokens=False) for g in gen)
    tok.padding_side = prev_side

    tool_hits = cmd_hits = 0
    per_tool_total: dict[str, int] = {}
    per_tool_hit: dict[str, int] = {}
    per_tool_cmd_hit: dict[str, int] = {}
    misses: list[tuple[str, str, str]] = []  # (query, gold_cmd, predicted_cmd)

    for (query, want_tool, want_cmd), raw in zip(golds, raws):
        per_tool_total[want_tool] = per_tool_total.get(want_tool, 0) + 1
        parsed = parse_tool_call(raw)
        tool_ok = cmd_ok = False
        pred_cmd = "<no tool call>"
        if parsed:
            name, args = parsed
            tool_ok = name == want_tool
            try:
                pred_cmd = to_command_line(name, args)
                cmd_ok = pred_cmd == want_cmd
            except (KeyError, TypeError) as e:
                pred_cmd = f"<{type(e).__name__}: {e}>"
        tool_hits += tool_ok
        cmd_hits += cmd_ok
        per_tool_hit[want_tool] = per_tool_hit.get(want_tool, 0) + tool_ok
        per_tool_cmd_hit[want_tool] = per_tool_cmd_hit.get(want_tool, 0) + cmd_ok
        if not cmd_ok:
            misses.append((query, want_cmd, pred_cmd))

    n = len(examples)
    print("=" * 72)
    print(
        f"EVAL  tool accuracy {tool_hits}/{n} = {tool_hits / n:.1%}"
        f"  |  command accuracy {cmd_hits}/{n} = {cmd_hits / n:.1%}"
    )
    print("-" * 72)
    print(f"  {'tool':22s} {'tool':>7s} {'cmd':>7s}")
    for tool in sorted(per_tool_total):
        tot = per_tool_total[tool]
        thit = per_tool_hit.get(tool, 0)
        chit = per_tool_cmd_hit.get(tool, 0)
        flag = "" if chit == tot else "  <-- review"
        print(f"  {tool:22s} {thit:>3d}/{tot:<3d} {chit:>3d}/{tot:<3d}{flag}")
    if misses:
        print("-" * 72)
        print(f"command mismatches ({len(misses)}):")
        for query, want_cmd, pred_cmd in misses[:20]:
            print(f"  q={query!r}")
            print(f"     want={want_cmd!r}  pred={pred_cmd!r}")
    print("=" * 72)
    return {
        "tool_accuracy": tool_hits / n,
        "command_accuracy": cmd_hits / n,
        "n": n,
    }


@app.function(
    gpu="A100-80GB",
    image=image,
    volumes={"/models": models_vol, "/root/.cache/huggingface": hf_cache},
    secrets=[hf_secret],
    timeout=60 * 60,
)
def train(
    size: str = "MEDIUM",
    epochs: float = 6.0,
    batch_size: int = 4,
    lr: float = 5e-5,
    seed: int = 42,
) -> str:
    import torch
    from datasets import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )
    from transformers.utils import get_json_schema

    from gemma.dataset import build_conversations
    from gemma.runner import MODEL_ID
    from gemma.tools import TOOLS

    # Same tool surface the local runner shows at inference time: one schema per
    # command verb, built from the typed signatures + docstrings in gemma.tools.
    tools = [get_json_schema(fn) for fn in TOOLS]

    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    convs = build_conversations(size=size, seed=seed)
    n_examples = len(convs)  # the dataset size we tag the checkpoint with
    output_dir = checkpoint_dir(size, n_examples)

    # The 22-tool declaration block alone is ~2.2k tokens, so max_length must
    # comfortably clear it -- at 2048 the assistant tool call (at the very end)
    # gets truncated off, every label becomes -100, and loss collapses to 0.
    max_length = 4096

    def encode(ex):
        # Completion-only loss: mask the prompt (developer + tool declarations +
        # user) so loss is computed ONLY on the assistant tool call. Without this,
        # the declaration block (identical across all examples) dominates and the
        # model learns to emit declarations instead of calls.
        full = tok.apply_chat_template(ex["messages"], tools=tools, tokenize=False)
        prompt = tok.apply_chat_template(
            ex["messages"][:-1], tools=tools, add_generation_prompt=True, tokenize=False
        )
        ids = tok(full, truncation=True, max_length=max_length)["input_ids"]
        n = min(
            len(tok(prompt, truncation=True, max_length=max_length)["input_ids"]),
            len(ids),
        )
        return {
            "input_ids": ids,
            "attention_mask": [1] * len(ids),
            "labels": [-100] * n + ids[n:],
        }

    ds = Dataset.from_list(convs).map(encode, remove_columns=["messages"])
    ds = ds.train_test_split(test_size=0.1, seed=seed)

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        attn_implementation="eager",  # Gemma trains more stably with eager attn
        device_map="auto",            # land on the GPU at load, not after Trainer
    )
    assert torch.cuda.is_available(), "no CUDA -- check the image's torch wheel"
    print(
        f"cuda={torch.cuda.is_available()} device={model.device} | "
        f"{len(ds['train'])} train / {len(ds['test'])} eval | max_length={max_length}"
    )

    args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=16 // batch_size,  # keep effective batch ~16
        learning_rate=lr,
        lr_scheduler_type="constant",
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="no",
        report_to="none",
        optim="adamw_torch_fused",  # fused AdamW is fine on CUDA
        bf16=True,
        seed=seed,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=ds["train"],
        eval_dataset=ds["test"],
        data_collator=DataCollatorForSeq2Seq(tok, label_pad_token_id=-100),
        processing_class=tok,
    )
    trainer.train()
    trainer.save_model(output_dir)
    tok.save_pretrained(output_dir)
    print(f"saved fine-tuned model to volume bgpsim-models:{output_dir}")

    # Evaluate the freshly-trained model in-place (no reload, no fetch needed),
    # on the FIXED held-out set so every size's number is comparable.
    metrics = _evaluate(model, tok, tools, size=EVAL_SIZE, seed=EVAL_SEED)

    # Persist a self-describing record next to the weights so a later report can
    # be assembled straight from the volume -- no need to remember run flags.
    import datetime
    import json

    record = {
        "output_dir": output_dir,
        "dataset_size": size,
        "n_examples": n_examples,
        "n_train": len(ds["train"]),
        "n_eval_split": len(ds["test"]),
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": lr,
        "seed": seed,
        "model_id": MODEL_ID,
        "max_length": max_length,
        "eval_size": EVAL_SIZE,
        "eval_seed": EVAL_SEED,
        "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **metrics,
    }
    with open(f"{output_dir}/metrics.json", "w") as f:
        json.dump(record, f, indent=2)

    models_vol.commit()
    hf_cache.commit()
    return record


@app.function(
    gpu="A100-80GB",
    image=image,
    volumes={"/models": models_vol, "/root/.cache/huggingface": hf_cache},
    secrets=[hf_secret],
    timeout=30 * 60,
)
def eval_only(
    ckpt: str = OUTPUT_DIR, size: str = EVAL_SIZE, seed: int = EVAL_SEED
) -> dict:
    """Evaluate a saved checkpoint on the volume without retraining.

    `ckpt` is the on-volume path, e.g. /models/gemma-bgpsim-ft-large-n1138.
    Defaults to the fixed held-out set so the number matches a training run's.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from transformers.utils import get_json_schema

    from gemma.tools import TOOLS

    tools = [get_json_schema(fn) for fn in TOOLS]
    tok = AutoTokenizer.from_pretrained(ckpt)
    model = AutoModelForCausalLM.from_pretrained(
        ckpt,
        dtype=torch.bfloat16,
        attn_implementation="eager",
        device_map="auto",
    )
    return _evaluate(model, tok, tools, size=size, seed=seed)


@app.local_entrypoint()
def main(
    sizes: str = "SMALL,MEDIUM,LARGE",
    epochs: float = 6.0,
    batch_size: int = 4,
    lr: float = 5e-5,
    seed: int = 42,
):
    """Fine-tune one checkpoint per dataset size and print a comparison table.

    Each size lands in its own size-tagged dir with a metrics.json, so the sweep
    doubles as the data for a later size-vs-accuracy performance report.
    """
    requested = [s.strip().upper() for s in sizes.split(",") if s.strip()]
    results = []
    for size in requested:
        print(f"\n{'#' * 72}\n# training size={size}\n{'#' * 72}")
        res = train.remote(
            size=size, epochs=epochs, batch_size=batch_size, lr=lr, seed=seed
        )
        results.append(res)

    print(f"\n{'=' * 72}\nSWEEP SUMMARY  (held-out {EVAL_SIZE}/seed={EVAL_SEED})")
    print(f"{'size':8s} {'n_examples':>10s} {'tool acc':>9s} {'cmd acc':>9s}  checkpoint")
    for r in results:
        ckpt = r["output_dir"].rsplit("/", 1)[-1]
        print(
            f"{r['dataset_size']:8s} {r['n_examples']:>10d} "
            f"{r['tool_accuracy']:>8.1%} {r['command_accuracy']:>8.1%}  {ckpt}"
        )
    print("=" * 72)
    print("\nfetch a checkpoint to use it in the app (rename to gemma-bgpsim-ft):")
    for r in results:
        ckpt = r["output_dir"].rsplit("/", 1)[-1]
        print(f"  uvx modal volume get bgpsim-models {ckpt} ./{ckpt}")
