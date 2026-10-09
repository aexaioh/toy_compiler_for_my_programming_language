"""Парсер mylang."""

from typing import List, Optional, Tuple
import re

from .common import Pos, unquote
from .lexer import Token, lex
from .ast_nodes import (
    Program, Function, Let, Assign, ExprStmt,
    StringLit, InterpStr, IntLit, BoolLit, VarRef,
    UnOp, BinOp, Call, Try,
    Pattern, MatchArm, Match,
    If, For, While, Break, Continue, Return,
    Cmd, FieldAccess, MethodCall, ListLit, Lambda, PairLambda,
)


class ParseError(Exception):
    def __init__(self, msg, pos=None):
        super().__init__(msg)
        self.msg = msg
        self.pos = pos


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def peek_at(self, off: int) -> Token:
        idx = self.pos + off
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def next(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, kind: str) -> Token:
        t = self.next()
        if t.kind != kind:
            raise ParseError(
                f"expected {kind}, got {t.kind} ({t.value!r})",
                (t.line, t.col),
            )
        return t

    # --- program ---

    def parse_program(self) -> Program:
        funcs = []
        while self.peek().kind != "EOF":
            funcs.append(self.parse_function())
        return Program(funcs)

    def parse_function(self) -> Function:
        fn_tok = self.expect("FN")
        pos = (fn_tok.line, fn_tok.col)
        name = self.expect("IDENT").value
        self.expect("LPAREN")
        params: List[str] = []
        if self.peek().kind != "RPAREN":
            params.append(self.expect("IDENT").value)
            while self.peek().kind == "COMMA":
                self.next()
                params.append(self.expect("IDENT").value)
        self.expect("RPAREN")
        self.expect("EQ")
        body = self.parse_block()
        return Function(name, params, body, pos)

    def parse_block(self):
        self.expect("LBRACE")
        stmts = []
        while self.peek().kind != "RBRACE":
            stmts.append(self.parse_stmt())
        self.expect("RBRACE")
        return stmts

    def parse_stmt(self):
        t = self.peek()
        if t.kind == "LET":
            lt = self.next()
            pos = (lt.line, lt.col)
            name = self.expect("IDENT").value
            self.expect("EQ")
            value = self.parse_expr()
            return Let(name, value, pos)
        if t.kind == "BREAK":
            bt = self.next()
            return Break((bt.line, bt.col))
        if t.kind == "CONTINUE":
            ct = self.next()
            return Continue((ct.line, ct.col))
        if t.kind == "RETURN":
            rt = self.next()
            pos = (rt.line, rt.col)
            value = self.parse_expr()
            return Return(value, pos)
        # Блок-выражения, которые закрываются `}`. В отличие от общего пути
        # через parse_expr, здесь мы не даём следующему `-`/`+` на новой
        # строке превратиться в бинарный оператор, применённый к блоку.
        if t.kind == "FOR":
            return ExprStmt(self.parse_for())
        if t.kind == "WHILE":
            return ExprStmt(self.parse_while())
        if t.kind == "IF":
            return ExprStmt(self.parse_if())
        if t.kind == "IDENT" and self.tokens[self.pos + 1].kind == "EQ":
            name_tok = self.next()
            pos = (name_tok.line, name_tok.col)
            self.expect("EQ")
            value = self.parse_expr()
            return Assign(name_tok.value, value, pos)
        return ExprStmt(self.parse_expr())

    # --- expressions ---

    def parse_expr(self):
        if self.peek().kind == "IDENT" and self.peek_at(1).kind == "FATARROW":
            return self.parse_lambda()
        if (
            self.peek().kind == "LPAREN"
            and self.peek_at(1).kind == "IDENT"
            and self.peek_at(2).kind == "COMMA"
            and self.peek_at(3).kind == "IDENT"
            and self.peek_at(4).kind == "RPAREN"
            and self.peek_at(5).kind == "FATARROW"
        ):
            return self.parse_pair_lambda()
        return self.parse_or()

    def parse_lambda(self):
        param_tok = self.expect("IDENT")
        self.expect("FATARROW")
        body = self.parse_expr()
        return Lambda(param_tok.value, body, (param_tok.line, param_tok.col))

    def parse_pair_lambda(self):
        lp = self.expect("LPAREN")
        pos = (lp.line, lp.col)
        k = self.expect("IDENT").value
        self.expect("COMMA")
        v = self.expect("IDENT").value
        self.expect("RPAREN")
        self.expect("FATARROW")
        body = self.parse_expr()
        return PairLambda(k, v, body, pos)

    def parse_or(self):
        left = self.parse_and()
        while self.peek().kind == "OR":
            op = self.next()
            right = self.parse_and()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_and(self):
        left = self.parse_equality()
        while self.peek().kind == "AND":
            op = self.next()
            right = self.parse_equality()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_equality(self):
        left = self.parse_comparison()
        while self.peek().kind in ("EQEQ", "NEQ"):
            op = self.next()
            right = self.parse_comparison()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_comparison(self):
        left = self.parse_additive()
        while self.peek().kind in ("LT", "GT", "LE", "GE"):
            op = self.next()
            right = self.parse_additive()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.peek().kind in ("PLUS", "MINUS"):
            op = self.next()
            right = self.parse_multiplicative()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.peek().kind in ("STAR", "SLASH", "PERCENT"):
            op = self.next()
            right = self.parse_unary()
            left = BinOp(op.value, left, right, (op.line, op.col))
        return left

    def parse_unary(self):
        if self.peek().kind == "NOT":
            t = self.next()
            operand = self.parse_unary()
            return UnOp("!", operand, (t.line, t.col))
        if self.peek().kind == "MINUS":
            t = self.next()
            operand = self.parse_unary()
            return UnOp("-", operand, (t.line, t.col))
        return self.parse_postfix()

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            if self.peek().kind == "DOT":
                dot = self.next()
                name = self.expect("IDENT").value
                pos = (dot.line, dot.col)
                if self.peek().kind == "LPAREN":
                    self.next()
                    args = []
                    if self.peek().kind != "RPAREN":
                        args.append(self.parse_expr())
                        while self.peek().kind == "COMMA":
                            self.next()
                            args.append(self.parse_expr())
                    self.expect("RPAREN")
                    e = MethodCall(e, name, args, pos)
                else:
                    e = FieldAccess(e, name, pos)
            elif self.peek().kind == "QUESTION":
                t = self.next()
                e = Try(e, (t.line, t.col))
            elif self.peek().kind == "SHELLIN":
                t = self.next()
                if not isinstance(e, Cmd):
                    raise ParseError(
                        "'<<' is only valid after $() or sh(...)",
                        (t.line, t.col),
                    )
                if e.stdin_expr is not None:
                    raise ParseError("'<<' specified more than once", (t.line, t.col))
                stdin_e = self.parse_expr()
                e = Cmd(e.parts, e.pos, stdin_e, e.use_shell)
            else:
                break
        return e

    def parse_primary(self):
        t = self.peek()
        if t.kind == "FOR":
            return self.parse_for()
        if t.kind == "WHILE":
            return self.parse_while()
        if t.kind == "STRING":
            self.next()
            return self.parse_string(t.value[1:-1], (t.line, t.col))
        if t.kind == "NUMBER":
            self.next()
            return IntLit(int(t.value), (t.line, t.col))
        if t.kind == "TRUE":
            self.next()
            return BoolLit(True, (t.line, t.col))
        if t.kind == "FALSE":
            self.next()
            return BoolLit(False, (t.line, t.col))
        if t.kind == "DOLLAR":
            return self.parse_cmd()
        if t.kind == "LBRACKET":
            self.next()
            items = []
            if self.peek().kind != "RBRACKET":
                items.append(self.parse_expr())
                while self.peek().kind == "COMMA":
                    self.next()
                    items.append(self.parse_expr())
            self.expect("RBRACKET")
            return ListLit(items, (t.line, t.col))
        if t.kind == "LPAREN":
            self.next()
            if self.peek().kind == "RPAREN":
                self.next()
                return Call("unit", [], (t.line, t.col))
            e = self.parse_expr()
            self.expect("RPAREN")
            return e
        if t.kind == "MATCH":
            return self.parse_match()
        if t.kind == "IF":
            return self.parse_if()
        if t.kind == "IDENT":
            name_tok = self.next()
            name = name_tok.value
            pos = (name_tok.line, name_tok.col)
            if name == "sh" and self.peek().kind == "LPAREN":
                self.next()
                arg = self.parse_expr()
                self.expect("RPAREN")
                return Cmd([("expr", arg)], pos, None, True)
            if name == "None" and self.peek().kind != "LPAREN":
                return Call("None", [], pos)
            if self.peek().kind == "LPAREN":
                self.next()
                args = []
                if self.peek().kind != "RPAREN":
                    args.append(self.parse_expr())
                    while self.peek().kind == "COMMA":
                        self.next()
                        args.append(self.parse_expr())
                self.expect("RPAREN")
                return Call(name, args, pos)
            return VarRef(name, pos)
        raise ParseError(
            f"unexpected token {t.kind} ({t.value!r})",
            (t.line, t.col),
        )

    # --- control flow ---

    def parse_for(self):
        tok = self.expect("FOR")
        pos = (tok.line, tok.col)
        var = self.expect("IDENT").value
        self.expect("IN")
        iterable = self.parse_expr()
        body = self.parse_block()
        return For(var, iterable, body, pos)

    def parse_while(self):
        tok = self.expect("WHILE")
        pos = (tok.line, tok.col)
        cond = self.parse_expr()
        body = self.parse_block()
        return While(cond, body, pos)

    def parse_if(self):
        tok = self.expect("IF")
        pos = (tok.line, tok.col)
        cond = self.parse_expr()
        then_body = self.parse_block()
        if self.peek().kind == "ELSE":
            self.next()
            else_body = self.parse_block()
        else:
            else_body = []
        return If(cond, then_body, else_body, pos)

    def parse_match(self):
        tok = self.expect("MATCH")
        pos = (tok.line, tok.col)
        scrut = self.parse_expr()
        self.expect("LBRACE")
        arms = []
        while self.peek().kind != "RBRACE":
            pat = self.parse_pattern()
            self.expect("FATARROW")
            body = self.parse_expr()
            arms.append(MatchArm(pat, body))
        self.expect("RBRACE")
        return Match(scrut, arms, pos)

    def parse_pattern(self):
        tok = self.expect("IDENT")
        name = tok.value
        pos = (tok.line, tok.col)
        if self.peek().kind == "LPAREN":
            self.next()
            binding = self.expect("IDENT").value
            self.expect("RPAREN")
            return Pattern(name, binding, pos)
        return Pattern(name, None, pos)

    # --- strings and commands ---

    _QUANT_RE = re.compile(r"^\d+$|^\d+,\d*$|^,\d+$")

    def parse_string(self, raw: str, pos: Pos):
        """raw — содержимое между кавычками, ещё экранированное."""
        has_interp = False
        i = 0
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                i += 2
                continue
            if c == "{":
                if i + 1 < len(raw) and raw[i + 1] == "{":
                    i += 2
                    continue
                has_interp = True
                break
            i += 1

        if not has_interp:
            return StringLit(unquote('"' + raw + '"'), pos)

        parts: List[Tuple[str, object]] = []
        buf: List[str] = []
        i = 0
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                buf.append(raw[i:i + 2])
                i += 2
                continue
            if c == "{" and i + 1 < len(raw) and raw[i + 1] == "{":
                buf.append("{")
                i += 2
                continue
            if c == "}" and i + 1 < len(raw) and raw[i + 1] == "}":
                buf.append("}")
                i += 2
                continue
            if c == "{":
                depth = 1
                j = i + 1
                while j < len(raw) and depth > 0:
                    if raw[j] == "\\" and j + 1 < len(raw):
                        j += 2
                        continue
                    if raw[j] == "{":
                        depth += 1
                    elif raw[j] == "}":
                        depth -= 1
                    j += 1
                if depth != 0:
                    raise ParseError("unmatched '{' in string interpolation", pos)
                expr_src = raw[i + 1:j - 1]
                # Regex-квантификатор {N}, {N,M}, {N,}, {,M} — литерал
                if self._QUANT_RE.match(expr_src):
                    buf.append(raw[i:j])
                    i = j
                    continue
                if buf:
                    parts.append(("str", unquote('"' + "".join(buf) + '"')))
                    buf = []
                sub_tokens = lex(expr_src)
                sub_parser = Parser(sub_tokens)
                try:
                    expr = sub_parser.parse_expr()
                except ParseError as pe:
                    raise ParseError(f"in interpolation: {pe.msg}", pos)
                if sub_parser.peek().kind != "EOF":
                    raise ParseError("trailing tokens in interpolation", pos)
                parts.append(("expr", expr))
                i = j
                continue
            buf.append(c)
            i += 1
        if buf:
            parts.append(("str", unquote('"' + "".join(buf) + '"')))
        return InterpStr(parts, pos)    

    def parse_cmd(self):
        dollar = self.expect("DOLLAR")
        pos = (dollar.line, dollar.col)
        self.expect("LPAREN")
        parts: List[Tuple[str, object]] = []
        state = {"chunk": "", "last_line": None, "last_end": None}

        def flush():
            if state["chunk"]:
                parts.append(("str", state["chunk"]))
                state["chunk"] = ""
            state["last_line"] = None
            state["last_end"] = None

        while self.peek().kind != "RPAREN":
            t = self.peek()
            if t.kind == "LBRACE":
                flush()
                self.next()
                e = self.parse_expr()
                self.expect("RBRACE")
                parts.append(("expr", e))
                continue
            if t.kind == "STRING":
                flush()
                self.next()
                parts.append(("str", unquote(t.value)))
                continue
            if t.kind == "NEWLINE":
                raise ParseError("newline inside $()", (t.line, t.col))
            self.next()
            if state["last_line"] == t.line and state["last_end"] == t.col:
                state["chunk"] += t.value
            else:
                flush()
                state["chunk"] = t.value
            state["last_line"] = t.line
            state["last_end"] = t.col + len(t.value)

        self.expect("RPAREN")
        flush()
        if not parts:
            raise ParseError("empty $() is not allowed", pos)
        return Cmd(parts, pos)
