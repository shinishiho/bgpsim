"""Thin Modal wrapper around the notebook-faithful trainer.

All the real work lives in `gemma_new.train.run_training` / `gemma_new.eval`; this
module only provides the Modal substrate (a B200 container, the shared model and
HF-cache volumes, secrets) and a small sweep entrypoint.

The image is intentionally identical to `gemma/modal_train.py`'s so Modal reuses
the already-built layers (no rebuild). The notebook-faithful path just doesn't
touch the flash-attn / Liger / bitsandbytes that happen to be installed -- it
trains with plain eager attention and ``completion_only_loss``.

    uvx modal run gemma_new/modal_train.py                       # default sweep
    uvx modal run gemma_new/modal_train.py --sizes SMALL,MEDIUM  # pick sizes
    uvx modal run gemma_new/modal_train.py::eval_only --ckpt /models/...  # score only
"""

import modal

app = modal.App("bgpsim-functiongemma-new")

image = (
    modal.Image.from_registry("nvidia/cuda:13.0.3-devel-ubuntu24.04", add_python="3.14")
    .pip_install(
        "torch>=2.12",
        "transformers>=5.10.2",
        "datasets>=5.0.0",
        "accelerate>=1.13.0",
        "huggingface_hub",
        "flash-attn-4[cu13]>=4.0.0b16",
        "trl>=1.5.1",
        "bitsandbytes>=0.49.2",
        "liger-kernel>=0.5",
    )
    .add_local_python_source("gemma_new")
)

models_vol = modal.Volume.from_name("bgpsim-models", create_if_missing=True)
hf_cache = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
hf_secret = modal.Secret.from_name("huggingface-secret")

# Fixed held-out set for the standalone eval: a different seed than training, so
# the phrasings are unseen, and a fixed size so every checkpoint's number is
# comparable.
EVAL_SIZE = "SMALL"
EVAL_SEED = 98765


def checkpoint_dir(size: str) -> str:
    """Size-tagged checkpoint path so a multi-size sweep doesn't clobber."""
    return f"/models/gemma-bgpsim-ft-new-{size.lower()}"


@app.function(
    gpu="B200",
    cpu=2,
    image=image,
    volumes={"/models": models_vol, "/root/.cache/huggingface": hf_cache},
    secrets=[hf_secret],
    timeout=12 * 60 * 60,
)
def train(
    size: str = "MEDIUM",
    epochs: float = 2.0,
    batch_size: int = 4,
    effective_batch: int = 32,
    lr: float = 1e-5,
    seed: int = 42,
) -> dict:
    import os

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    from gemma_new.train import run_training

    record = run_training(
        checkpoint_dir(size),
        size=size,
        epochs=epochs,
        batch_size=batch_size,
        effective_batch=effective_batch,
        lr=lr,
        seed=seed,
    )
    models_vol.commit()
    hf_cache.commit()
    return record


@app.function(
    gpu="L40S",
    image=image,
    volumes={"/models": models_vol, "/root/.cache/huggingface": hf_cache},
    secrets=[hf_secret],
    timeout=30 * 60,
)
def eval_only(ckpt: str, size: str = EVAL_SIZE, seed: int = EVAL_SEED) -> dict:
    """Score a saved checkpoint on a fresh held-out set, no retraining.

    `ckpt` is the on-volume path, e.g. /models/gemma-bgpsim-ft-new-medium.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from gemma_new.dataset import build_dataset
    from gemma_new.eval import score_dataset
    from gemma_new.train import build_tool_schemas

    tok = AutoTokenizer.from_pretrained(ckpt)
    model = AutoModelForCausalLM.from_pretrained(
        ckpt, dtype=torch.bfloat16, attn_implementation="eager", device_map="auto"
    )
    records = build_dataset(size=size, seed=seed)
    return score_dataset(records, tok, model, build_tool_schemas())


@app.local_entrypoint()
def main(
    sizes: str = "SMALL,MEDIUM,LARGE",
    epochs: float = 6.0,
    batch_size: int = 4,
    effective_batch: int = 32,
    lr: float = 5e-5,
    seed: int = 42,
):
    """Fine-tune one checkpoint per dataset size and print a comparison table.

    Each size lands in its own dir with a metrics.json, so the sweep doubles as
    the data for a size-vs-accuracy report.
    """
    requested = [s.strip().upper() for s in sizes.split(",") if s.strip()]
    results = []
    for size in requested:
        print(f"\n{'#' * 72}\n# training size={size}\n{'#' * 72}")
        res = train.remote(
            size=size,
            epochs=epochs,
            batch_size=batch_size,
            effective_batch=effective_batch,
            lr=lr,
            seed=seed,
        )
        results.append(res)

    print(f"\n{'=' * 72}\nSWEEP SUMMARY")
    print(f"{'size':8s} {'n':>7s} {'name':>7s} {'args':>7s} {'cmd':>7s}  checkpoint")
    for r in results:
        ckpt = r["output_dir"].rsplit("/", 1)[-1]
        print(
            f"{r['dataset_size']:8s} {r['n_examples']:>7d} "
            f"{r.get('name_accuracy', 0):>6.1%} {r.get('argument_accuracy', 0):>6.1%} "
            f"{r.get('command_accuracy', 0):>6.1%}  {ckpt}"
        )
    print("=" * 72)
    print("\nfetch a checkpoint to use it in the app (rename to gemma-bgpsim-ft):")
    for r in results:
        ckpt = r["output_dir"].rsplit("/", 1)[-1]
        print(f"  uvx modal volume get bgpsim-models {ckpt} ./{ckpt}")
