"""Notebook-faithful FunctionGemma fine-tuning for the BGP simulator.

A clean rewrite of the training pipeline that follows Google's
``Finetune FunctionGemma 270M for Mobile Actions`` notebook end to end --
dataset building, prompt/completion formatting, ``completion_only_loss`` SFT, and
name/argument scoring -- sourcing data from our own template engine instead of a
downloaded jsonl. See README.md for the flow.

Kept import-light on purpose: importing this package must not pull in torch, so
`tools` (the pure-Python tool surface) can be read without the heavy stack.
"""
