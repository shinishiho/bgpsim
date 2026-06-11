"""Local inference: turn a plain-English request into a command line.

The notebook-faithful counterpart to `gemma/runner.py`. `GemmaRouter` loads
FunctionGemma (the fine-tuned checkpoint when present, otherwise the base
model), shows it the same tool surface it was trained on, generates a single
tool call, and maps that call back to a `commands.py` line.

The one difference from the old runner is the tool-schema block: this one
renders the **plain, per-tool** declarations (one `get_json_schema` per tool,
no cross-tool parameter union), because `gemma_new` trains on that format. The
inference prompt must match training byte-for-byte, so a checkpoint trained by
`gemma_new.train` must be served here, not by `gemma/runner.py` (which unions
the schemas for the old union-trained weights). See `gemma_new/README.md`.

On Apple Silicon, inference runs through MLX (Apple's native runtime) when
`mlx-lm` is installed -- faster per request and a faster cold load than
PyTorch-MPS on this 270M model. Elsewhere (or without mlx-lm) it falls back to
PyTorch. Either backend is imported lazily inside `load()` so merely importing
this module (e.g. to read `MODEL_ID`) is cheap and never pulls in the heavy
stack -- the Textual UI relies on that to stay importable without the `gemma`
extra.
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from pathlib import Path

from gemma_new.tools import (  # re-exported for callers
    DEVELOPER_PROMPT,
    MODEL_ID,
    TOOLS,
    to_command_line,
)

__all__ = ["MODEL_ID", "DEVELOPER_PROMPT", "GemmaRouter", "Translation"]

# Where `modal volume get` drops the fine-tuned checkpoint (see modal_train.py
# and .gitignore). When this directory exists we load it instead of the base.
DEFAULT_ADAPTER_DIR = "gemma-bgpsim-ft"

# `<start_function_call>call:NAME{ ... }<end_function_call>` is FunctionGemma's
# tool-call wire format. We also tolerate the bare `call:NAME{...}` in case the
# special tokens are stripped during decode.
_CALL_RE = re.compile(r"call:\s*([A-Za-z_]\w*)\s*\{(.*?)\}", re.DOTALL)
# Inside the braces: `key:<escape>value<escape>` for strings, or `key:value`
# for bare numbers/enums the model didn't wrap.
_ESCAPED_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*:\s*<escape>(.*?)<escape>", re.DOTALL)
_BARE_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*:\s*([^,{}<]+)")


@dataclass
class Translation:
    """The result of interpreting one natural-language line.

    ok:      whether a tool call was parsed and mapped to a command line.
    command: the flat-grammar line to feed `commands.apply_command` (when ok).
    tool:    the tool/function name the model chose (for display/eval).
    args:    the parsed arguments.
    raw:     the model's raw decoded output (handy when ok is False).
    """

    ok: bool
    command: str = ""
    tool: str = ""
    args: dict | None = None
    raw: str = ""


def _coerce(value: str) -> str | int | None:
    """Numbers come back as ints so command rendering matches the schema.

    A bare ``None`` maps to Python ``None`` and is dropped by the caller. The
    non-union targets `gemma_new` trains on list only the args a verb fills, so
    ``None`` slots are not expected -- but we still tolerate one defensively, in
    case the model echoes an empty slot.
    """
    value = value.strip()
    if value == "None":
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_tool_call(text: str) -> tuple[str, dict] | None:
    """Pull (name, arguments) out of FunctionGemma's decoded output, or None.

    Any ``key:None`` slot is dropped, leaving only the args this verb actually
    fills -- exactly the kwargs ``to_command_line`` expects.
    """
    call = _CALL_RE.search(text)
    if not call:
        return None
    name, body = call.group(1), call.group(2)
    args: dict[str, str | int] = {}
    for key, val in _ESCAPED_ARG_RE.findall(body):
        coerced = _coerce(val)
        if coerced is not None:
            args[key] = coerced
    # Pick up any numeric/enum args that weren't <escape>-wrapped, without
    # clobbering ones we already captured.
    stripped = _ESCAPED_ARG_RE.sub("", body)
    for key, val in _BARE_ARG_RE.findall(stripped):
        coerced = _coerce(val)
        if coerced is not None and key not in args:
            args[key] = coerced
    return name, args


def _mlx_usable() -> bool:
    """True on Apple Silicon with mlx-lm importable.

    MLX only runs on arm64 macOS; the import check also covers a torch-only
    install that never pulled mlx-lm in.
    """
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return False
    return True


def _torch_usable() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


class GemmaRouter:
    """Lazily-loaded FunctionGemma wrapper that emits `commands.py` lines.

    Picks the MLX backend on Apple Silicon (when mlx-lm is present) and falls
    back to PyTorch otherwise; `backend` forces one explicitly (handy in tests).
    """

    def __init__(
        self,
        model_path: str | None = None,
        device: str | None = None,
        backend: str | None = None,
    ):
        # Prefer the fine-tuned checkpoint; fall back to the base model so the
        # plumbing is testable even before a training run finishes.
        self.model_path = model_path or (
            DEFAULT_ADAPTER_DIR if Path(DEFAULT_ADAPTER_DIR).is_dir() else MODEL_ID
        )
        self._device = device
        self._backend = backend  # "mlx" | "torch" | None (auto)
        self._tok = None
        self._model = None
        self._sampler = None  # MLX greedy sampler (set in _load_mlx)
        self._schemas: list[dict] | None = None

    @staticmethod
    def is_available(model_path: str | None = None) -> bool:
        """True when a checkpoint exists and some backend can run it.

        The UI uses this to decide whether to offer natural-language input at
        all -- no surprise model downloads on a stray typo.
        """
        path = model_path or DEFAULT_ADAPTER_DIR
        if not Path(path).is_dir():
            return False
        return _mlx_usable() or _torch_usable()

    def load(self) -> None:
        """Load tokenizer + model once (idempotent)."""
        if self._model is not None:
            return
        if self._backend is None:
            self._backend = "mlx" if _mlx_usable() else "torch"
        if self._backend == "mlx":
            self._load_mlx()
        else:
            self._load_torch()

    def _load_mlx(self) -> None:
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        # mlx-lm reads the HF-format checkpoint directly (no separate convert
        # step) and maps the Gemma 3 weights to MLX on load.
        self._model, self._tok = load(self.model_path)
        self._sampler = make_sampler(temp=0.0)  # greedy, deterministic
        self._device = "mlx"

    def _load_torch(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        # transformers renders a tqdm "Loading weights" bar during
        # from_pretrained; tqdm lazily builds a *multiprocessing* lock, which
        # spawns the resource-tracker via fork_exec. When we run inside the
        # Textual UI (which redirects stdout/stderr to objects whose fileno()
        # is -1), that spawn dies with "bad value(s) in fds_to_keep". Disabling
        # the bar makes transformers use its no-op EmptyTqdm, sidestepping the
        # lock entirely -- and the bar is invisible under the TUI regardless.
        hf_logging.disable_progress_bar()

        if self._device is None:
            if torch.cuda.is_available():
                self._device = "cuda"
            elif torch.backends.mps.is_available():
                self._device = "mps"  # Apple GPU: far cleaner greedy decode than CPU
            else:
                self._device = "cpu"
        # bf16 on CUDA; float32 elsewhere (MPS bf16 is flaky and a 270M model is
        # cheap enough in fp32 that greedy decode stays clean and deterministic).
        dtype = torch.bfloat16 if self._device == "cuda" else torch.float32
        self._tok = AutoTokenizer.from_pretrained(self.model_path)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            dtype=dtype,
        ).to(self._device)
        self._model.eval()

    def _tool_schemas(self) -> list[dict]:
        # The 22-tool schema block is identical every call; build it once. Each
        # tool gets its own plain declaration (its real params only) -- the same
        # non-union block `gemma_new.train.build_tool_schemas` feeds the trainer,
        # so the inference prompt matches what the checkpoint was trained on.
        if self._schemas is None:
            from transformers.utils import get_json_schema

            self._schemas = [get_json_schema(fn) for fn in TOOLS]
        return self._schemas

    def translate(self, query: str, max_new_tokens: int = 160) -> Translation:
        """Interpret one natural-language line into a command (best effort)."""
        self.load()
        messages = [
            {"role": "developer", "content": DEVELOPER_PROMPT},
            {"role": "user", "content": query},
        ]
        if self._backend == "mlx":
            raw = self._generate_mlx(messages, max_new_tokens)
        else:
            raw = self._generate_torch(messages, max_new_tokens)

        parsed = parse_tool_call(raw)
        if parsed is None:
            return Translation(ok=False, raw=raw)
        name, args = parsed
        try:
            command = to_command_line(name, args)
        except (KeyError, TypeError):
            # Unknown tool, or the model dropped a required argument.
            return Translation(ok=False, tool=name, args=args, raw=raw)
        return Translation(ok=True, command=command, tool=name, args=args, raw=raw)

    def _generate_mlx(self, messages: list[dict], max_new_tokens: int) -> str:
        from mlx_lm import generate

        # mlx-lm's tokenizer wraps the HF tokenizer, so the same tool-aware
        # chat template applies; generate returns only the new text.
        prompt = self._tok.apply_chat_template(
            messages, tools=self._tool_schemas(), add_generation_prompt=True
        )
        return generate(
            self._model,
            self._tok,
            prompt=prompt,
            sampler=self._sampler,
            max_tokens=max_new_tokens,
            verbose=False,
        )

    def _generate_torch(self, messages: list[dict], max_new_tokens: int) -> str:
        import torch

        inputs = self._tok.apply_chat_template(
            messages,
            tools=self._tool_schemas(),
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self._device)

        with torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self._tok.eos_token_id,
            )
        generated = out[0][inputs["input_ids"].shape[1]:]
        return self._tok.decode(generated, skip_special_tokens=False)


if __name__ == "__main__":
    # `python -m gemma_new.runner "spin up R7 and peer it with R1"` for a quick try.
    import sys

    router = GemmaRouter()
    print(f"model: {router.model_path}")
    for q in sys.argv[1:] or ["add a router called R9", "peer R1 with R2"]:
        t = router.translate(q)
        if t.ok:
            print(f"{q!r}\n  -> {t.tool} -> `{t.command}`")
        else:
            print(f"{q!r}\n  -> (no call) raw={t.raw!r}")
