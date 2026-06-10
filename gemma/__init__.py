"""FunctionGemma tool-router for the BGP simulator.

Turns plain-English requests into the flat-grammar command lines that
`commands.apply_command` executes. See gemma/README.md for the train/eval flow.

Light-touch exports only -- importing this package must not drag in torch, so
the Textual UI can probe `GemmaRouter.is_available()` without the heavy stack.
"""

from gemma.tools import DEVELOPER_PROMPT, MODEL_ID

__all__ = ["MODEL_ID", "DEVELOPER_PROMPT"]
