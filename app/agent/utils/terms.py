"""召回词处理工具。"""

from collections.abc import Iterable


def dedupe_terms(values: Iterable[object]) -> list[str]:
    """清洗召回词并保持原始顺序去重。"""
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value).strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def build_recall_terms(*term_groups: Iterable[object]) -> list[str]:
    """按传入顺序合并多组召回词，并保持顺序去重。"""
    values: list[object] = []
    for group in term_groups:
        values.extend(group)
    return dedupe_terms(values)
