"""Prompt 模板加载工具。

按名称从 `app/agent/prompts` 目录读取 `.prompt` 文件。
业务节点只需要传入逻辑名称，不需要关心提示词文件的具体路径。
"""

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """读取指定名称的 prompt 模板内容。"""
    prompt_path = _PROMPT_DIR / f"{name}.prompt"
    return prompt_path.read_text(encoding="utf-8")
