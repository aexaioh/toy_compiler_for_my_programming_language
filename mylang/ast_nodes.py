"""AST-узлы mylang."""

from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from .common import Pos


@dataclass
class Program:
    functions: List["Function"]


@dataclass
class Function:
    name: str
    params: List[str]
    body: List["Stmt"]
    pos: Pos


@dataclass
class Let:
    name: str
    value: "Expr"
    pos: Pos


@dataclass
class Assign:
    name: str
    value: "Expr"
    pos: Pos


@dataclass
class ExprStmt:
    expr: "Expr"


@dataclass
class StringLit:
    value: str
    pos: Pos


@dataclass
class InterpStr:
    parts: List[Tuple[str, object]]
    pos: Pos


@dataclass
class IntLit:
    value: int
    pos: Pos


@dataclass
class BoolLit:
    value: bool
    pos: Pos


@dataclass
class VarRef:
    name: str
    pos: Pos


@dataclass
class UnOp:
    op: str
    operand: "Expr"
    pos: Pos


@dataclass
class BinOp:
    op: str
    left: "Expr"
    right: "Expr"
    pos: Pos


@dataclass
class Call:
    name: str
    args: List["Expr"]
    pos: Pos


@dataclass
class Try:
    expr: "Expr"
    pos: Pos


@dataclass
class Pattern:
    ctor: str
    binding: Optional[str]
    pos: Pos


@dataclass
class MatchArm:
    pattern: Pattern
    body: "Expr"


@dataclass
class Match:
    scrutinee: "Expr"
    arms: List[MatchArm]
    pos: Pos


@dataclass
class If:
    cond: "Expr"
    then_body: List["Stmt"]
    else_body: List["Stmt"]
    pos: Pos


@dataclass
class For:
    var: str
    iterable: "Expr"
    body: List["Stmt"]
    pos: Pos


@dataclass
class While:
    cond: "Expr"
    body: List["Stmt"]
    pos: Pos


@dataclass
class Break:
    pos: Pos


@dataclass
class Continue:
    pos: Pos

@dataclass
class Return:
    value: Optional["Expr"]
    pos: Pos

@dataclass
class Cmd:
    parts: List[Tuple[str, object]]
    pos: Pos
    stdin_expr: Optional["Expr"] = None
    use_shell: bool = False


@dataclass
class FieldAccess:
    obj: "Expr"
    field: str
    pos: Pos


@dataclass
class MethodCall:
    obj: "Expr"
    method: str
    args: List["Expr"]
    pos: Pos


@dataclass
class ListLit:
    items: List["Expr"]
    pos: Pos


@dataclass
class Lambda:
    param: str
    body: "Expr"
    pos: Pos


@dataclass
class PairLambda:
    k: str
    v: str
    body: "Expr"
    pos: Pos


Stmt = Union[Let, Assign, ExprStmt, Break, Continue, Return]
Expr = Union[
    StringLit, InterpStr, IntLit, BoolLit, VarRef, UnOp, BinOp, Call, Try,
    Match, If, For, While, Cmd, FieldAccess, MethodCall, ListLit, Lambda,
    PairLambda,
]
