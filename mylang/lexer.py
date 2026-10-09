"""Лексер mylang."""

import re
from dataclasses import dataclass
from typing import List


@dataclass
class Token:
    kind: str
    value: str
    line: int
    col: int


KEYWORDS = {
    "fn", "let", "match", "if", "else", "true", "false",
    "for", "in", "while", "break", "continue", "return",
}

TOKEN_SPEC = [
    ("WS", r"[ \t]+"),
    ("COMMENT", r"//[^\n]*"),
    ("NEWLINE", r"\n"),
    ("STRING", r'"(?:[^"\\]|\\.)*"'),
    ("NUMBER", r"\d+"),
    ("FATARROW", r"=>"),
    ("ARROW", r"->"),
    ("QUESTION", r"\?"),
    ("EQEQ", r"=="),
    ("NEQ", r"!="),
    ("SHELLIN", r"<<"),
    ("LE", r"<="),
    ("GE", r">="),
    ("AND", r"&&"),
    ("OR", r"\|\|"),
    ("EQ", r"="),
    ("LT", r"<"),
    ("GT", r">"),
    ("DOLLAR", r"\$"),
    ("DOT", r"\."),
    ("SLASH", r"/"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("COMMA", r","),
    ("COLON", r":"),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("PERCENT", r"%"),
    ("NOT", r"!"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("MISMATCH", r"."),
]


class LexError(Exception):
    def __init__(self, msg, pos=None):
        super().__init__(msg)
        self.msg = msg
        self.pos = pos


def lex(src: str) -> List[Token]:
    tokens: List[Token] = []
    pos = 0
    line = 1
    line_start = 0
    n = len(src)

    while pos < n:
        # Строковые литералы сканируем вручную: regex не умеет вложенные
        # кавычки внутри { ... } интерполяции.
        if src[pos] == '"':
            start_line = line
            start_col = pos - line_start
            i = pos + 1
            depth = 0
            while i < n:
                c = src[i]
                if c == "\n":
                    raise LexError("newline in string literal", (start_line, start_col))
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if depth == 0 and c == '"':
                    break
                if c == "{":
                    if i + 1 < n and src[i + 1] == "{":
                        i += 2
                        continue
                    depth += 1
                elif c == "}":
                    if i + 1 < n and src[i + 1] == "}":
                        i += 2
                        continue
                    if depth > 0:
                        depth -= 1
                i += 1
            if i >= n:
                raise LexError("unterminated string literal", (start_line, start_col))
            text = src[pos:i + 1]
            tokens.append(Token("STRING", text, start_line, start_col))
            pos = i + 1
            continue

        for kind, pattern in TOKEN_SPEC:
            m = re.match(pattern, src[pos:])
            if not m:
                continue
            text = m.group(0)
            col = pos - line_start
            if kind == "NEWLINE":
                line += 1
                line_start = pos + len(text)
            elif kind in ("WS", "COMMENT"):
                pass
            elif kind == "MISMATCH":
                raise LexError(f"unexpected character {text!r}", (line, col))
            else:
                if kind == "IDENT" and text in KEYWORDS:
                    kind = text.upper()
                tokens.append(Token(kind, text, line, col))
            pos += len(text)
            break
        else:
            raise LexError("lexer stuck", (line, pos - line_start))

    tokens.append(Token("EOF", "", line, 0))
    return tokens
