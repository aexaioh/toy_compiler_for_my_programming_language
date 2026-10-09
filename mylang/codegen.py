"""Кодогенерация C из AST mylang."""

from pathlib import Path
from typing import Dict, Optional

from .ast_nodes import (
    Program, Function, Let, Assign, ExprStmt,
    StringLit, InterpStr, IntLit, BoolLit, VarRef,
    UnOp, BinOp, Call, Try,
    Pattern, MatchArm, Match,
    If, For, While, Break, Continue, Return,
    Cmd, FieldAccess, MethodCall, ListLit, Lambda, PairLambda,
)
from .types import (
    Type, TInt, TStr, TBool, TUnit, TCmdResult,
    TFunc, TCon, TVar, show,
)
from .checker import Checker
from .specialize import mangle_type, mangle_spec_name


_RUNTIME_PATH = Path(__file__).parent / "runtime.c"
_RUNTIME_CACHE: Optional[str] = None


def get_runtime() -> str:
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        _RUNTIME_CACHE = _RUNTIME_PATH.read_text(encoding="utf-8")
    return _RUNTIME_CACHE


def c_string_literal(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class CodeGen:
    def __init__(self, checker: Checker, fn_sigs, program: Program, specializations):
        self.checker = checker
        self.fn_sigs = fn_sigs
        self.fn_by_name = {fn.name: fn for fn in program.functions}
        self.specs = specializations
        self.mangled_names = {
            k: mangle_spec_name(k[0], v[1], checker) for k, v in specializations.items()
        }
        self.lines = []
        self.indent = 0
        self.tmp_counter = 0
        self.types = dict(checker.types)
        self.current_c_ret = "void"

    def new_tmp(self) -> int:
        n = self.tmp_counter
        self.tmp_counter += 1
        return n

    def emit(self, line: str = "") -> None:
        self.lines.append("  " * self.indent + line)

    def type_of(self, e):
        t = self.types.get(id(e))
        if t is None:
            raise RuntimeError(f"no type for {e!r}")
        return self.checker.prune(t)

    def _map_value_kind(self, v_t) -> int:
        v_t = self.checker.prune(v_t)
        if isinstance(v_t, TInt):
            return 0
        if isinstance(v_t, TStr):
            return 1
        if isinstance(v_t, TBool):
            return 2
        if isinstance(v_t, TCon) and v_t.name == "List":
            et = self.checker.prune(v_t.args[0])
            if isinstance(et, TStr):
                return 3
            if isinstance(et, TInt):
                return 4
        raise RuntimeError(f"cannot handle Map value type: {show(v_t)}")

    def c_type(self, t) -> str:
        t = self.checker.prune(t)
        if isinstance(t, TInt):
            return "long long"
        if isinstance(t, TStr):
            return "const char*"
        if isinstance(t, TBool):
            return "int"
        if isinstance(t, TUnit):
            return "void"
        if isinstance(t, TCmdResult):
            return "CmdResult*"
        if isinstance(t, TCon) and t.name == "List":
            return "MList*"
        if isinstance(t, TCon) and t.name == "Map":
            return "MMap*"
        if isinstance(t, TCon) and t.name in ("Option", "Result"):
            return "MVal"
        raise RuntimeError(f"cannot codegen type: {show(t)}")

    def is_mval(self, t) -> bool:
        t = self.checker.prune(t)
        return isinstance(t, TCon) and t.name in ("Option", "Result")

    def is_polymorphic(self, name: str) -> bool:
        sig = self.fn_sigs.get(name)
        return sig is not None and self.checker.has_generic(sig)

    def _read_from_mval(self, t, mv: str) -> str:
        t = self.checker.prune(t)
        if isinstance(t, (TInt, TBool)):
            return f"({mv}).i"
        if isinstance(t, TStr):
            return f"({mv}).s"
        if isinstance(t, TCmdResult):
            return f"(CmdResult*)({mv}).p"
        if isinstance(t, TCon) and t.name == "List":
            return f"(MList*)({mv}).p"
        if isinstance(t, TCon) and t.name == "Map":
            return f"(MMap*)({mv}).p"
        raise RuntimeError(f"cannot read from MVal: {show(t)}")

    def _wrap_mval(self, t, c: str) -> str:
        t = self.checker.prune(t)
        if isinstance(t, (TInt, TBool)):
            return f"((MVal){{0, {c}, NULL, NULL}})"
        if isinstance(t, TStr):
            return f"((MVal){{0, 0, {c}, NULL}})"
        if isinstance(t, TCmdResult):
            return f"((MVal){{0, 0, NULL, (void*)({c})}})"
        if isinstance(t, TCon) and t.name == "List":
            return f"((MVal){{0, 0, NULL, (void*)({c})}})"
        if isinstance(t, TCon) and t.name == "Map":
            return f"((MVal){{0, 0, NULL, (void*)({c})}})"
        raise RuntimeError(f"cannot wrap into MVal: {show(t)}")

    def gen(self, program: Program) -> str:
        self.emit("/* generated by mylangc v28 */")
        self.emit(get_runtime())
        self.emit("")

        for fn in program.functions:
            if fn.name == "main":
                continue
            if self.is_polymorphic(fn.name):
                continue
            self.emit_forward_decl(fn)
        for key, (fn, arg_types, ret_type, _) in self.specs.items():
            self.emit_spec_forward_decl(self.mangled_names[key], fn, arg_types, ret_type)
        self.emit("")

        for fn in program.functions:
            if fn.name == "main":
                continue
            if self.is_polymorphic(fn.name):
                continue
            self.gen_function(fn)
            self.emit("")

        for key, (fn, arg_types, ret_type, spec_types) in self.specs.items():
            self.gen_specialized_function(
                self.mangled_names[key], fn, arg_types, ret_type, spec_types
            )
            self.emit("")

        for fn in program.functions:
            if fn.name == "main":
                self.gen_function(fn)
                self.emit("")
        return "\n".join(self.lines)

    def emit_forward_decl(self, fn: Function) -> None:
        sig = self.fn_sigs[fn.name]
        ret_t = self.checker.prune(sig.ret)
        c_ret = "void" if isinstance(ret_t, TUnit) else self.c_type(ret_t)
        if fn.params:
            c_params = ", ".join(
                f"{self.c_type(self.checker.prune(p))} {n}"
                for n, p in zip(fn.params, sig.params)
            )
        else:
            c_params = "void"
        self.emit(f"{c_ret} {fn.name}({c_params});")

    def emit_spec_forward_decl(self, mangled, fn, arg_types, ret_type) -> None:
        c_ret = "void" if isinstance(ret_type, TUnit) else self.c_type(ret_type)
        if fn.params:
            c_params = ", ".join(
                f"{self.c_type(t)} {n}" for n, t in zip(fn.params, arg_types)
            )
        else:
            c_params = "void"
        self.emit(f"{c_ret} {mangled}({c_params});")

    def gen_specialized_function(self, mangled, fn, arg_types, ret_type, spec_types) -> None:
        old_types = self.types
        saved_c_ret = self.current_c_ret
        self.types = spec_types
        try:
            c_ret = "void" if isinstance(ret_type, TUnit) else self.c_type(ret_type)
            self.current_c_ret = c_ret
            if fn.params:
                c_params = ", ".join(
                    f"{self.c_type(t)} {n}" for n, t in zip(fn.params, arg_types)
                )
            else:
                c_params = "void"
            self.emit(f"{c_ret} {mangled}({c_params}) {{")
            self.indent += 1
            n = len(fn.body)
            for i, stmt in enumerate(fn.body):
                is_last = (i == n - 1)
                if is_last and isinstance(stmt, ExprStmt) and not isinstance(ret_type, TUnit):
                    self.emit(f"return {self.gen_expr(stmt.expr)};")
                else:
                    self.gen_stmt(stmt)
            self.indent -= 1
            self.emit("}")
        finally:
            self.types = old_types
            self.current_c_ret = saved_c_ret

    def gen_function(self, fn: Function) -> None:
        sig = self.fn_sigs[fn.name]
        ret_t = self.checker.prune(sig.ret)
        is_main = fn.name == "main"

        if is_main and self.is_mval(ret_t):
            self.gen_main_mval(fn)
            return

        if is_main:
            cname = "main"
            c_ret = "int"
            c_params = "int argc, char** argv"
            default_return = "return 0;"
        else:
            cname = fn.name
            c_ret = "void" if isinstance(ret_t, TUnit) else self.c_type(ret_t)
            if fn.params:
                c_params = ", ".join(
                    f"{self.c_type(self.checker.prune(p))} {n}"
                    for n, p in zip(fn.params, sig.params)
                )
            else:
                c_params = "void"
            default_return = None

        saved_c_ret = self.current_c_ret
        self.current_c_ret = c_ret
        self.emit(f"{c_ret} {cname}({c_params}) {{")
        self.indent += 1
        if is_main:
            self.emit("mylang_init_args(argc, argv);")
        n = len(fn.body)
        for i, stmt in enumerate(fn.body):
            is_last = (i == n - 1)
            if (
                is_last
                and isinstance(stmt, ExprStmt)
                and not isinstance(ret_t, TUnit)
                and not is_main
            ):
                self.emit(f"return {self.gen_expr(stmt.expr)};")
            else:
                self.gen_stmt(stmt)
        if default_return:
            self.emit(default_return)
        self.indent -= 1
        self.emit("}")
        self.current_c_ret = saved_c_ret

    def gen_main_mval(self, fn: Function) -> None:
        self.emit("MVal __mylang_main(int argc, char** argv);")
        self.emit("")
        self.emit("int main(int argc, char** argv) {")
        self.indent += 1
        self.emit("MVal __r = __mylang_main(argc, argv);")
        self.emit("if (__r.tag != 0) {")
        self.indent += 1
        self.emit('if (__r.s) fprintf(stderr, "error: %s\\n", __r.s);')
        self.emit("return 1;")
        self.indent -= 1
        self.emit("}")
        self.emit("return 0;")
        self.indent -= 1
        self.emit("}")
        self.emit("")
        self.emit("MVal __mylang_main(int argc, char** argv) {")
        self.indent += 1
        saved_c_ret = self.current_c_ret
        self.current_c_ret = "MVal"
        self.emit("mylang_init_args(argc, argv);")
        for stmt in fn.body:
            self.gen_stmt(stmt)
        self.emit("MVal __default = {0, 0, NULL, NULL};")
        self.emit("return __default;")
        self.indent -= 1
        self.emit("}")
        self.current_c_ret = saved_c_ret

    def gen_stmt(self, stmt) -> None:
        if isinstance(stmt, Let):
            n = self.new_tmp()
            ctype = self.c_type(self.type_of(stmt.value))
            expr_c = self.gen_expr(stmt.value)
            self.emit(f"{ctype} __t{n} = {expr_c};")
            self.emit(f"{ctype} {stmt.name} = __t{n};")
        elif isinstance(stmt, Assign):
            c = self.gen_expr(stmt.value)
            self.emit(f"{stmt.name} = {c};")
        elif isinstance(stmt, Break):
            self.emit("break;")
        elif isinstance(stmt, Continue):
            self.emit("continue;")
        elif isinstance(stmt, Return):
            self.gen_return(stmt)
        elif isinstance(stmt, ExprStmt):
            self.emit(f"{self.gen_expr(stmt.expr)};")

    def gen_stmt_str(self, stmt) -> str:
        if isinstance(stmt, Let):
            n = self.new_tmp()
            ctype = self.c_type(self.type_of(stmt.value))
            expr_c = self.gen_expr(stmt.value)
            return f"{ctype} __t{n} = {expr_c}; {ctype} {stmt.name} = __t{n};"
        if isinstance(stmt, Assign):
            c = self.gen_expr(stmt.value)
            return f"{stmt.name} = {c};"
        if isinstance(stmt, Break):
            return "break;"
        if isinstance(stmt, Continue):
            return "continue;"
        if isinstance(stmt, Return):
            t = self.checker.prune(self.type_of(stmt.value))
            if isinstance(t, TUnit):
                if self.current_c_ret == "int":
                    return "return 0;"
                return "return;"
            return f"return {self.gen_expr(stmt.value)};"
        if isinstance(stmt, ExprStmt):
            return f"{self.gen_expr(stmt.expr)};"
        raise NotImplementedError(stmt)

    def gen_return(self, stmt: Return) -> None:
        t = self.checker.prune(self.type_of(stmt.value))
        if isinstance(t, TUnit):
            if self.current_c_ret == "int":
                self.emit("return 0;")
            else:
                self.emit("return;")
            return
        c = self.gen_expr(stmt.value)
        self.emit(f"return {c};")

    def gen_expr(self, e) -> str:
        if isinstance(e, StringLit):
            return c_string_literal(e.value)
        if isinstance(e, InterpStr):
            return self.gen_interp_str(e)
        if isinstance(e, IntLit):
            return str(e.value)
        if isinstance(e, BoolLit):
            return "1" if e.value else "0"
        if isinstance(e, VarRef):
            return e.name
        if isinstance(e, UnOp):
            return f"({e.op}{self.gen_expr(e.operand)})"
        if isinstance(e, BinOp):
            return self.gen_binop(e)
        if isinstance(e, If):
            return self.gen_if(e)
        if isinstance(e, For):
            return self.gen_for(e)
        if isinstance(e, While):
            return self.gen_while(e)
        if isinstance(e, Call):
            return self.gen_call(e)
        if isinstance(e, Try):
            return self.gen_try(e)
        if isinstance(e, Match):
            return self.gen_match(e)
        if isinstance(e, Cmd):
            return self.gen_cmd(e)
        if isinstance(e, FieldAccess):
            return self.gen_field(e)
        if isinstance(e, MethodCall):
            return self.gen_method(e)
        if isinstance(e, ListLit):
            return self.gen_list_lit(e)
        if isinstance(e, Lambda):
            raise RuntimeError("lambda outside method context")
        if isinstance(e, PairLambda):
            raise RuntimeError("pair lambda outside Map.each")
        raise NotImplementedError(e)

    def _interp_expr_to_str(self, expr) -> str:
        t = self.type_of(expr)
        c = self.gen_expr(expr)
        if isinstance(t, TStr):
            return c
        if isinstance(t, TInt):
            return f"mylang_int_to_str({c})"
        if isinstance(t, TBool):
            return f"mylang_bool_to_str({c})"
        raise RuntimeError(f"cannot interpolate type: {show(t)}")

    def gen_interp_str(self, e: InterpStr) -> str:
        n = self.new_tmp()
        chunks = []
        for kind, val in e.parts:
            if kind == "str":
                chunks.append(c_string_literal(val))
            else:
                chunks.append(self._interp_expr_to_str(val))
        if not chunks:
            return 'mylang_str_dup("")'
        stmts = [f"({{ char* __s{n} = mylang_str_dup({chunks[0]});"]
        for c in chunks[1:]:
            stmts.append(f"__s{n} = mylang_str_append(__s{n}, {c});")
        stmts.append(f"__s{n}; }})")
        return " ".join(stmts)

    def gen_binop(self, e: BinOp) -> str:
        l = self.gen_expr(e.left)
        r = self.gen_expr(e.right)
        if e.op in ("==", "!="):
            lt = self.checker.prune(self.type_of(e.left))
            if isinstance(lt, TStr):
                return f"(strcmp({l}, {r}) {e.op} 0)"
        if e.op == "+":
            lt = self.checker.prune(self.type_of(e.left))
            if isinstance(lt, TCon) and lt.name == "List":
                return f"mylang_list_concat({l}, {r})"
        if e.op == "/":
            return f"mylang_div({l}, {r}, {e.pos[0]}, {e.pos[1]})"
        if e.op == "%":
            return f"mylang_mod({l}, {r}, {e.pos[0]}, {e.pos[1]})"
        return f"({l} {e.op} {r})"

    def gen_if(self, e: If) -> str:
        cond = self.gen_expr(e.cond)
        result_t = self.checker.prune(self.type_of(e))
        is_unit = isinstance(result_t, TUnit)
        tmp = self.new_tmp()
        parts = ["({"]
        if is_unit:
            parts.append(f"if ({cond}) {{")
            self._gen_block_into(parts, e.then_body, True, None)
            parts.append("} else {")
            self._gen_block_into(parts, e.else_body, True, None)
            parts.append("}")
            parts.append("(void)0; })")
        else:
            ctype = self.c_type(result_t)
            parts.append(f"{ctype} __r{tmp};")
            parts.append(f"if ({cond}) {{")
            self._gen_block_into(parts, e.then_body, False, tmp)
            parts.append("} else {")
            self._gen_block_into(parts, e.else_body, False, tmp)
            parts.append("}")
            parts.append(f"__r{tmp}; }})")
        return " ".join(parts)

    def gen_for(self, e: For) -> str:
        list_c = self.gen_expr(e.iterable)
        it = self.checker.prune(self.type_of(e.iterable))
        elem_t = self.checker.prune(it.args[0])
        elem_ct = self.c_type(elem_t)
        n = self.new_tmp()
        parts = [f"({{ MList* __src{n} = {list_c};"]
        parts.append(f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) {{")
        read_c = self._read_from_mval(elem_t, f"__src{n}->items[__i{n}]")
        parts.append(f"{elem_ct} {e.var} = {read_c};")
        for stmt in e.body:
            parts.append(self.gen_stmt_str(stmt))
        parts.append("}")
        parts.append("(void)0; })")
        return " ".join(parts)

    def gen_while(self, e: While) -> str:
        cond = self.gen_expr(e.cond)
        parts = ["({"]
        parts.append(f"while ({cond}) {{")
        for stmt in e.body:
            parts.append(self.gen_stmt_str(stmt))
        parts.append("}")
        parts.append("(void)0; })")
        return " ".join(parts)

    def _gen_block_into(self, parts, stmts, is_unit, tmp) -> None:
        if not stmts:
            if not is_unit:
                parts.append(f"__r{tmp} = 0;")
            return
        for stmt in stmts[:-1]:
            parts.append(self.gen_stmt_str(stmt))
        last = stmts[-1]
        if isinstance(last, ExprStmt):
            if is_unit:
                parts.append(self.gen_stmt_str(last))
            else:
                parts.append(f"__r{tmp} = {self.gen_expr(last.expr)};")
        else:
            parts.append(self.gen_stmt_str(last))
            if not is_unit:
                parts.append(f"__r{tmp} = 0;")

    def _payload_field(self, t) -> Optional[str]:
        t = self.checker.prune(t)
        if isinstance(t, (TInt, TBool)):
            return "i"
        if isinstance(t, TStr):
            return "s"
        if isinstance(t, TCmdResult):
            return "p"
        if isinstance(t, TCon) and t.name == "List":
            return "p"
        if isinstance(t, TCon) and t.name == "Map":
            return "p"
        if isinstance(t, TUnit):
            return None
        raise RuntimeError(f"unsupported payload: {show(t)}")

    def gen_cmd(self, e: Cmd) -> str:
        if e.use_shell:
            cmd_c = self.gen_expr(e.parts[0][1])
            if e.stdin_expr is not None:
                stdin_c = self.gen_expr(e.stdin_expr)
                return f"mylang_run_sh_stdin({cmd_c}, {stdin_c})"
            return f"mylang_run_sh({cmd_c})"
        parts_c = []
        for kind, val in e.parts:
            if kind == "str":
                parts_c.append(c_string_literal(val))
            else:
                parts_c.append(self.gen_expr(val))
        n = len(parts_c)
        argv_items = ", ".join(parts_c) + ", NULL"
        if e.stdin_expr is not None:
            stdin_c = self.gen_expr(e.stdin_expr)
            return (
                f"({{ const char* __argv[] = {{ {argv_items} }}; "
                f"const char* __sd = {stdin_c}; "
                f"mylang_run_cmd_stdin(__argv, {n}, __sd); }})"
            )
        return f"({{ const char* __argv[] = {{ {argv_items} }}; mylang_run_cmd(__argv, {n}); }})"

    def gen_field(self, e: FieldAccess) -> str:
        obj_c = self.gen_expr(e.obj)
        obj_t = self.checker.prune(self.type_of(e.obj))
        if isinstance(obj_t, TCmdResult):
            if e.field == "status":
                return f"({{ CmdResult* __o = {obj_c}; __o ? __o->status : -1; }})"
            if e.field == "stdout":
                return f'({{ CmdResult* __o = {obj_c}; (__o && __o->out) ? __o->out : ""; }})'
            if e.field == "stderr":
                return f'({{ CmdResult* __o = {obj_c}; (__o && __o->err) ? __o->err : ""; }})'
        raise RuntimeError(f"unknown field: {e.field}")

    def gen_method(self, e: MethodCall) -> str:
        obj_c = self.gen_expr(e.obj)
        obj_t = self.checker.prune(self.type_of(e.obj))
        if isinstance(obj_t, TCon) and obj_t.name == "List":
            return self.gen_list_method(e, obj_t, obj_c)
        if isinstance(obj_t, TCon) and obj_t.name == "Map":
            return self.gen_map_method(e, obj_t, obj_c)
        if isinstance(obj_t, TStr):
            return self.gen_str_method(e, obj_c)
        if isinstance(obj_t, TCon) and obj_t.name == "Option":
            return self.gen_option_method(e, obj_t, obj_c)
        if isinstance(obj_t, TInt):
            if e.method == "to_str":
                return f"({{ long long __o = {obj_c}; mylang_int_to_str_dup(__o); }})"
        raise RuntimeError(f"unknown method on {show(obj_t)}")

    def gen_map_method(self, e: MethodCall, map_t, obj_c: str) -> str:
        val_t = self.checker.prune(map_t.args[1])
        m = e.method
        if m == "get":
            k_c = self.gen_expr(e.args[0])
            return f"({{ MMap* __o = {obj_c}; const char* __k = {k_c}; mylang_map_get(__o, __k); }})"
        if m == "set":
            k_c = self.gen_expr(e.args[0])
            v_t = self.checker.prune(self.type_of(e.args[1]))
            v_c = self.gen_expr(e.args[1])
            wrapped = self._wrap_mval(v_t, v_c)
            return (
                f"({{ MMap* __o = {obj_c}; const char* __k = {k_c}; "
                f"MVal __v = {wrapped}; mylang_map_set(__o, __k, __v); }})"
            )
        if m == "has":
            k_c = self.gen_expr(e.args[0])
            return f"({{ MMap* __o = {obj_c}; const char* __k = {k_c}; mylang_map_has(__o, __k); }})"
        if m == "keys":
            return f"({{ MMap* __o = {obj_c}; mylang_map_keys(__o); }})"
        if m == "values":
            return f"({{ MMap* __o = {obj_c}; mylang_map_values(__o); }})"
        if m == "items":
            kind = self._map_value_kind(val_t)
            return f"({{ MMap* __o = {obj_c}; mylang_map_items(__o, {kind}); }})"
        if m == "len":
            return f"({{ MMap* __o = {obj_c}; mylang_map_len(__o); }})"
        if m == "delete":
            k_c = self.gen_expr(e.args[0])
            return f"({{ MMap* __o = {obj_c}; const char* __k = {k_c}; mylang_map_delete(__o, __k); }})"
        if m == "each":
            lam = e.args[0]
            body_c = self.gen_expr(lam.body)
            n = self.new_tmp()
            val_ct = self.c_type(val_t)
            read_c = self._read_from_mval(val_t, f"__o{n}->entries[__i{n}].value")
            return (
                f"({{ MMap* __o{n} = {obj_c}; "
                f"for (int __i{n} = 0; __i{n} < __o{n}->len; __i{n}++) {{ "
                f"const char* {lam.k} = __o{n}->entries[__i{n}].key; "
                f"{val_ct} {lam.v} = {read_c}; "
                f"{body_c}; }} "
                f"(void)0; }})"
            )
        raise RuntimeError(f"unknown Map method: {m}")

    def gen_option_method(self, e: MethodCall, opt_t, obj_c: str) -> str:
        elem_t = self.checker.prune(opt_t.args[0])
        if e.method == "or":
            default_c = self.gen_expr(e.args[0])
            read_c = self._read_from_mval(elem_t, "__o")
            return f"({{ MVal __o = {obj_c}; (__o.tag == 0) ? {read_c} : {default_c}; }})"
        raise RuntimeError(f"unknown Option method: {e.method}")

    def gen_str_method(self, e: MethodCall, obj_c: str) -> str:
        m = e.method
        if m == "len":
            return f"({{ const char* __o = {obj_c}; (int)strlen(__o); }})"
        if m == "trim":
            return f"({{ const char* __o = {obj_c}; mylang_str_trim(__o); }})"
        if m == "upper":
            return f"({{ const char* __o = {obj_c}; mylang_str_upper(__o); }})"
        if m == "lower":
            return f"({{ const char* __o = {obj_c}; mylang_str_lower(__o); }})"
        if m == "contains":
            arg = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __a = {arg}; strstr(__o, __a) != NULL; }})"
        if m == "starts_with":
            arg = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __a = {arg}; strncmp(__o, __a, strlen(__a)) == 0; }})"
        if m == "ends_with":
            arg = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __a = {arg}; mylang_str_ends_with(__o, __a); }})"
        if m == "replace":
            a = self.gen_expr(e.args[0])
            b = self.gen_expr(e.args[1])
            return f"({{ const char* __o = {obj_c}; const char* __a = {a}; const char* __b = {b}; mylang_str_replace(__o, __a, __b); }})"
        if m == "split":
            sep = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __s = {sep}; mylang_str_split(__o, __s); }})"
        if m == "lines":
            return f"({{ const char* __o = {obj_c}; mylang_str_lines(__o); }})"
        if m == "repeat":
            n_c = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; long long __n = {n_c}; mylang_str_repeat(__o, (int)__n); }})"
        if m == "pad_left":
            n_c = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; long long __n = {n_c}; mylang_str_pad_left(__o, (int)__n); }})"
        if m == "pad_right":
            n_c = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; long long __n = {n_c}; mylang_str_pad_right(__o, (int)__n); }})"
        if m == "to_int":
            return f"({{ const char* __o = {obj_c}; mylang_str_to_int(__o); }})"
        if m == "to_str":
            return f"({{ const char* __o = {obj_c}; mylang_str_dup_wrap(__o); }})"
        if m == "matches":
            arg = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __p = {arg}; mylang_regex_matches(__o, __p, {e.pos[0]}, {e.pos[1]}); }})"
        if m == "find_all":
            arg = self.gen_expr(e.args[0])
            return f"({{ const char* __o = {obj_c}; const char* __p = {arg}; mylang_regex_find_all(__o, __p, {e.pos[0]}, {e.pos[1]}); }})"
        if m == "replace_re":
            a = self.gen_expr(e.args[0])
            b = self.gen_expr(e.args[1])
            return f"({{ const char* __o = {obj_c}; const char* __p = {a}; const char* __r = {b}; mylang_regex_replace(__o, __p, __r, {e.pos[0]}, {e.pos[1]}); }})"
        raise RuntimeError(f"unknown string method: {m}")

    def gen_list_method(self, e: MethodCall, list_t, obj_c: str) -> str:
        elem_t = self.checker.prune(list_t.args[0])
        m = e.method
        if m == "len":
            return f"({{ MList* __o = {obj_c}; __o->len; }})"
        if m == "join":
            sep = self.gen_expr(e.args[0])
            return f"({{ MList* __o = {obj_c}; const char* __sep = {sep}; mylang_list_join(__o, __sep); }})"
        if m == "reverse":
            n = self.new_tmp()
            return (
                f"({{ MList* __src{n} = {obj_c}; MList* __dst{n} = mylang_list_new(); "
                f"for (int __i{n} = __src{n}->len - 1; __i{n} >= 0; __i{n}--) "
                f"mylang_list_push(__dst{n}, __src{n}->items[__i{n}]); "
                f"__dst{n}; }})"
            )
        if m in ("take", "drop"):
            n = self.new_tmp()
            cnt = self.gen_expr(e.args[0])
            if m == "take":
                loop = f"for (int __i{n} = 0; __i{n} < __n{n} && __i{n} < __src{n}->len; __i{n}++)"
            else:
                loop = f"for (int __i{n} = __n{n}; __i{n} < __src{n}->len; __i{n}++)"
            return (
                f"({{ MList* __src{n} = {obj_c}; long long __n{n} = {cnt}; "
                f"MList* __dst{n} = mylang_list_new(); "
                f"{loop} mylang_list_push(__dst{n}, __src{n}->items[__i{n}]); "
                f"__dst{n}; }})"
            )
        if m == "sort":
            n = self.new_tmp()
            cmp = "mylang_cmp_int" if isinstance(elem_t, TInt) else "mylang_cmp_str"
            return (
                f"({{ MList* __src{n} = {obj_c}; "
                f"MList* __dst{n} = mylang_list_new(); "
                f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) mylang_list_push(__dst{n}, __src{n}->items[__i{n}]); "
                f"qsort(__dst{n}->items, __dst{n}->len, sizeof(MVal), {cmp}); "
                f"__dst{n}; }})"
            )
        if m == "unique":
            if isinstance(elem_t, TInt):
                return f"mylang_list_unique_int({obj_c})"
            return f"mylang_list_unique_str({obj_c})"
        if m == "group_by":
            lam = e.args[0]
            body_c = self.gen_expr(lam.body)
            n = self.new_tmp()
            read_c = self._read_from_mval(elem_t, f"__src{n}->items[__i{n}]")
            elem_ct = self.c_type(elem_t)
            return (
                f"({{ MList* __src{n} = {obj_c}; MMap* __r{n} = mylang_map_new(); "
                f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) {{ "
                f"{elem_ct} {lam.param} = {read_c}; "
                f"const char* __k{n} = {body_c}; "
                f"MVal __v{n} = mylang_map_get(__r{n}, __k{n}); "
                f"MList* __lst{n} = (__v{n}.tag == 0) ? (MList*)__v{n}.p : mylang_list_new(); "
                f"mylang_list_push(__lst{n}, __src{n}->items[__i{n}]); "
                f"MVal __w{n} = {{0, 0, NULL, (void*)__lst{n}}}; "
                f"__r{n} = mylang_map_set(__r{n}, __k{n}, __w{n}); }} "
                f"__r{n}; }})"
            )
        if m in ("first", "last"):
            n = self.new_tmp()
            f = self._payload_field(elem_t)
            if f is None:
                inner = "(MVal){0, 0, NULL, NULL}"
            elif f == "i":
                inner = f"((MVal){{0, __src{n}->items[__idx{n}].i, NULL, NULL}})"
            elif f == "s":
                inner = f"((MVal){{0, 0, __src{n}->items[__idx{n}].s, NULL}})"
            elif f == "p":
                inner = f"((MVal){{0, 0, NULL, __src{n}->items[__idx{n}].p}})"
            idx = "0" if m == "first" else f"__src{n}->len - 1"
            return (
                f"({{ MList* __src{n} = {obj_c}; int __idx{n} = {idx}; "
                f"(__src{n}->len > 0) ? {inner} : ((MVal){{1, 0, NULL, NULL}}); }})"
            )
        if m == "get":
            n = self.new_tmp()
            idx_c = self.gen_expr(e.args[0])
            f = self._payload_field(elem_t)
            if f is None:
                inner = "(MVal){0, 0, NULL, NULL}"
            elif f == "i":
                inner = f"((MVal){{0, __src{n}->items[__idx{n}].i, NULL, NULL}})"
            elif f == "s":
                inner = f"((MVal){{0, 0, __src{n}->items[__idx{n}].s, NULL}})"
            elif f == "p":
                inner = f"((MVal){{0, 0, NULL, __src{n}->items[__idx{n}].p}})"
            return (
                f"({{ MList* __src{n} = {obj_c}; long long __idx{n} = {idx_c}; "
                f"(__idx{n} >= 0 && __idx{n} < __src{n}->len) ? {inner} : ((MVal){{1, 0, NULL, NULL}}); }})"
            )
        if m == "contains":
            arg = self.gen_expr(e.args[0])
            if isinstance(elem_t, TInt):
                return f"({{ MList* __o = {obj_c}; mylang_list_contains_int(__o, {arg}); }})"
            if isinstance(elem_t, TBool):
                return f"({{ MList* __o = {obj_c}; mylang_list_contains_bool(__o, {arg}); }})"
            return f"({{ MList* __o = {obj_c}; mylang_list_contains_str(__o, {arg}); }})"
        if m == "each":
            lam = e.args[0]
            body_c = self.gen_expr(lam.body)
            n = self.new_tmp()
            read_c = self._read_from_mval(elem_t, f"__src{n}->items[__i{n}]")
            elem_ct = self.c_type(elem_t)
            return (
                f"({{ MList* __src{n} = {obj_c}; "
                f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) {{ "
                f"{elem_ct} {lam.param} = {read_c}; "
                f"{body_c}; }} "
                f"(void)0; }})"
            )
        if m in ("map", "filter"):
            lam = e.args[0]
            body_t = self.checker.prune(self.types[id(lam)].ret)
            body_c = self.gen_expr(lam.body)
            param = lam.param
            n = self.new_tmp()
            read_c = self._read_from_mval(elem_t, f"__src{n}->items[__i{n}]")
            elem_ct = self.c_type(elem_t)
            if m == "map":
                wrapped = self._wrap_mval(body_t, body_c)
                return (
                    f"({{ MList* __src{n} = {obj_c}; MList* __dst{n} = mylang_list_new(); "
                    f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) {{ "
                    f"{elem_ct} {param} = {read_c}; "
                    f"mylang_list_push(__dst{n}, {wrapped}); }} "
                    f"__dst{n}; }})"
                )
            else:
                return (
                    f"({{ MList* __src{n} = {obj_c}; MList* __dst{n} = mylang_list_new(); "
                    f"for (int __i{n} = 0; __i{n} < __src{n}->len; __i{n}++) {{ "
                    f"{elem_ct} {param} = {read_c}; "
                    f"if ({body_c}) mylang_list_push(__dst{n}, __src{n}->items[__i{n}]); }} "
                    f"__dst{n}; }})"
                )
        raise RuntimeError(f"unknown list method: {m}")

    def gen_list_lit(self, e: ListLit) -> str:
        n = self.new_tmp()
        parts = [f"({{ MList* __l{n} = mylang_list_new();"]
        for item in e.items:
            item_t = self.checker.prune(self.type_of(item))
            item_c = self.gen_expr(item)
            wrapped = self._wrap_mval(item_t, item_c)
            parts.append(f"mylang_list_push(__l{n}, {wrapped});")
        parts.append(f"__l{n}; }})")
        return " ".join(parts)

    def _gen_print_like(self, call: Call) -> str:
        arg = call.args[0]
        t = self.type_of(arg)
        c = self.gen_expr(arg)
        to_stderr = call.name.startswith("e")
        no_newline = call.name.endswith("nnl")
        nl = "" if no_newline else "\\n"
        prefix = "fprintf(stderr, " if to_stderr else "printf("
        if isinstance(t, TInt):
            return f'{prefix}"%lld{nl}", {c})'
        if isinstance(t, TBool):
            return f'{prefix}"%d{nl}", {c})'
        if isinstance(t, TStr):
            return f'{prefix}"%s{nl}", {c})'
        raise RuntimeError(f"{call.name}: unsupported type {show(t)}")

    def gen_call(self, call: Call) -> str:
        if call.name in ("print", "printnnl", "eprint", "eprintnnl"):
            return self._gen_print_like(call)
        if call.name == "unit":
            return "((MVal){0, 0, NULL, NULL})"

        if call.name == "read_file":
            return f"mylang_read_file({self.gen_expr(call.args[0])})"
        if call.name == "write_file":
            return f"mylang_write_file({self.gen_expr(call.args[0])}, {self.gen_expr(call.args[1])})"
        if call.name == "args":
            return "mylang_args()"
        if call.name == "getenv":
            return f"mylang_getenv({self.gen_expr(call.args[0])})"
        if call.name == "exit":
            return f"exit({self.gen_expr(call.args[0])})"
        if call.name == "sleep":
            return f"mylang_sleep_ms({self.gen_expr(call.args[0])})"
        if call.name == "now":
            return "mylang_now_ms()"
        if call.name == "abs":
            return f"mylang_abs({self.gen_expr(call.args[0])})"
        if call.name in ("min", "max"):
            a = self.gen_expr(call.args[0])
            b = self.gen_expr(call.args[1])
            t = self.checker.prune(self.type_of(call.args[0]))
            kind = "int" if isinstance(t, TInt) else "str"
            return f"mylang_{call.name}_{kind}({a}, {b})"
        if call.name == "map":
            return "mylang_map_new()"

        for ctor, tag in (("Some", 0), ("None", 1), ("Ok", 0), ("Err", 1)):
            if call.name == ctor:
                if ctor == "None":
                    return "((MVal){1, 0, NULL, NULL})"
                arg = call.args[0]
                t = self.type_of(arg)
                f = self._payload_field(t)
                if f is None:
                    return f"((MVal){{{tag}, 0, NULL, NULL}})"
                inner = self.gen_expr(arg)
                if f == "i":
                    return f"((MVal){{{tag}, {inner}, NULL, NULL}})"
                if f == "s":
                    return f"((MVal){{{tag}, 0, {inner}, NULL}})"
                if f == "p":
                    return f"((MVal){{{tag}, 0, NULL, (void*)({inner})}})"

        if self.is_polymorphic(call.name):
            arg_types = [self.checker.deep_prune(self.type_of(a)) for a in call.args]
            key = (call.name, tuple(mangle_type(t, self.checker) for t in arg_types))
            mangled = self.mangled_names.get(key)
            if mangled is None:
                raise RuntimeError(
                    f"missing specialization for '{call.name}': "
                    f"{[show(t) for t in arg_types]}"
                )
            args = ", ".join(self.gen_expr(a) for a in call.args)
            return f"{mangled}({args})"

        args = ", ".join(self.gen_expr(a) for a in call.args)
        return f"{call.name}({args})"

    def gen_try(self, e: Try) -> str:
        inner_t = self.checker.prune(self.type_of(e.expr))
        inner_c = self.gen_expr(e.expr)

        if isinstance(inner_t, TCmdResult):
            return (
                f"({{ CmdResult* __c = {inner_c}; "
                f'if (__c->status != 0) return ((MVal){{1, 0, __c->err ? __c->err : "command failed", NULL}}); '
                f"__c; }})"
            )

        t = self.checker.prune(self.type_of(e))
        if isinstance(t, TUnit):
            return f"({{ MVal __t = {inner_c}; if (__t.tag != 0) return __t; 0; }})"
        read_c = self._read_from_mval(t, "__t")
        return f"({{ MVal __t = {inner_c}; if (__t.tag != 0) return __t; {read_c}; }})"

    def gen_match(self, e: Match) -> str:
        scrut = self.gen_expr(e.scrutinee)
        scrut_t = self.checker.prune(self.type_of(e.scrutinee))
        result_t = self.checker.prune(self.type_of(e))
        c_ret = "int" if isinstance(result_t, TUnit) else self.c_type(result_t)
        tmp = self.new_tmp()
        parts = [f"({{ {c_ret} __r{tmp};", f"MVal __m = {scrut};"]
        for i, arm in enumerate(e.arms):
            pat = arm.pattern
            if pat.ctor in ("Some", "Ok"):
                tag, inner_t = 0, scrut_t.args[0]
            else:
                tag = 1
                inner_t = scrut_t.args[1] if scrut_t.name == "Result" else None
            parts.append(f"if (__m.tag == {tag}) {{" if i == 0 else "else {")
            if pat.binding and inner_t is not None:
                inner_t = self.checker.prune(inner_t)
                if isinstance(inner_t, TUnit):
                    parts.append(f"int {pat.binding} = 0; (void){pat.binding};")
                else:
                    ct = self.c_type(inner_t)
                    read_c = self._read_from_mval(inner_t, "__m")
                    parts.append(f"{ct} {pat.binding} = {read_c};")
            body = self.gen_expr(arm.body)
            parts.append(f"__r{tmp} = {body};")
            parts.append("}")
        parts.append(f"__r{tmp}; }})")
        return " ".join(parts)
