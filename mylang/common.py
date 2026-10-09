"""Общие утилиты: тип Pos и разэкранирование строк."""

from typing import Tuple

Pos = Tuple[int, int]


def unquote(s: str) -> str:
    """Убирает кавычки и разэкранирует строковый литерал.

    Ожидает строку вида '"..."' на входе.
    """
    inner = s[1:-1]
    out = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
