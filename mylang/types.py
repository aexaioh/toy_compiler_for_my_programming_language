"""Типы mylang и их отображение в строку."""

from dataclasses import dataclass
from typing import List, Optional


class Type:
    pass


@dataclass
class TInt(Type):
    pass


@dataclass
class TStr(Type):
    pass


@dataclass
class TBool(Type):
    pass


@dataclass
class TUnit(Type):
    pass


@dataclass
class TCmdResult(Type):
    pass


@dataclass
class TFunc(Type):
    params: List[Type]
    ret: Type


@dataclass
class TCon(Type):
    name: str
    args: List[Type]


@dataclass
class TVar(Type):
    id: int
    instance: Optional[Type] = None


@dataclass
class TGeneric(Type):
    id: int


def show(t: Type) -> str:
    if isinstance(t, TVar):
        return show(t.instance) if t.instance else f"'a{t.id}"
    if isinstance(t, TInt):
        return "Int"
    if isinstance(t, TStr):
        return "Str"
    if isinstance(t, TBool):
        return "Bool"
    if isinstance(t, TUnit):
        return "Unit"
    if isinstance(t, TCmdResult):
        return "CmdResult"
    if isinstance(t, TGeneric):
        return f"'g{t.id}"
    if isinstance(t, TFunc):
        return "(" + ", ".join(show(p) for p in t.params) + ") -> " + show(t.ret)
    if isinstance(t, TCon):
        return t.name + ("[" + ", ".join(show(a) for a in t.args) + "]" if t.args else "")
    return repr(t)
