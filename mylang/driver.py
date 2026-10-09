"""Драйвер компилятора: parse → check → specialize → codegen → cc."""

import subprocess
import sys
import tempfile
from pathlib import Path

from .lexer import lex, LexError
from .parser import Parser, ParseError
from .checker import Checker, TypeError_
from .specialize import collect_specializations
from .codegen import CodeGen


def compile_source(src: str) -> str:
    """Компилирует исходник mylang в строку C-кода."""
    tokens = lex(src)
    program = Parser(tokens).parse_program()
    checker = Checker()
    fn_sigs = checker.check_program(program)
    specs = collect_specializations(checker, program)
    return CodeGen(checker, fn_sigs, program, specs).gen(program)


def build(src_path: Path, out_path: Path, cc: str = "cc") -> None:
    """Компилирует файл .myl в нативный бинарник."""
    src = src_path.read_text(encoding="utf-8")
    c_src = compile_source(src)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".c", delete=False, encoding="utf-8"
    ) as f:
        f.write(c_src)
        c_path = Path(f.name)
    try:
        subprocess.run(
            [
                cc,
                "-O2",
                "-std=gnu99",
                f'-DMYLANG_SOURCE_FILE="{src_path}"',
                "-o", str(out_path),
                str(c_path),
            ],
            check=True,
        )
    finally:
        c_path.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: mylangc <source.myl> [-o output]", file=sys.stderr)
        sys.exit(1)
    src_path = Path(sys.argv[1])
    out_path = Path("a.out")
    if len(sys.argv) >= 4 and sys.argv[2] == "-o":
        out_path = Path(sys.argv[3])
    try:
        build(src_path, out_path)
    except (LexError, ParseError, TypeError_) as e:
        print(f"error: {e.msg}", file=sys.stderr)
        if e.pos:
            print(f"  at {e.pos[0]}:{e.pos[1]}", file=sys.stderr)
        sys.exit(1)
    print(f"built: {out_path}")
