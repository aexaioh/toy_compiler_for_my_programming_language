"""mylang — компилятор языка mylang.

Публичный API:
    compile_source(src) -> str   — исходник mylang в C-код
    build(src, out)              — .myl → нативный бинарник
"""

from .lexer import lex, LexError, Token
from .parser import Parser, ParseError
from .checker import Checker, TypeError_, TypeEnv
from .specialize import collect_specializations, mangle_type, mangle_spec_name
from .codegen import CodeGen, get_runtime
from .driver import compile_source, build

__all__ = [
    "lex", "LexError", "Token",
    "Parser", "ParseError",
    "Checker", "TypeError_", "TypeEnv",
    "collect_specializations", "mangle_type", "mangle_spec_name",
    "CodeGen", "get_runtime",
    "compile_source", "build",
]
