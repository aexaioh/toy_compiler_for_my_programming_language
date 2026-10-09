"""Мономорфизация: сбор специализаций полиморфных функций и их тела."""

from typing import Dict, List, Tuple

from .ast_nodes import (
    Call, BinOp, UnOp, Try, Match, If, For, While, Cmd, InterpStr,
    FieldAccess, MethodCall, ListLit, Lambda, PairLambda,
    Let, Assign, ExprStmt, Break, Continue, Return
)
from .checker import Checker, TypeEnv
from .types import (
    Type, TInt, TStr, TBool, TUnit, TCmdResult,
    TFunc, TCon, TVar, show,
)


def specialize_body(checker, fn, param_types, ret_type) -> Dict[int, Type]:
    """Повторный typecheck тела функции с конкретными типами параметров.
    Возвращает словарь id(expr) -> Type для всех подвыражений."""
    sub = Checker()
    sub.fn_sigs = checker.fn_sigs
    env = TypeEnv()
    for name, sig in checker.fn_sigs.items():
        env.bind(name, sig)
    local = TypeEnv(parent=env)
    for pname, ptype in zip(fn.params, param_types):
        local.bind(pname, ptype)
    sub.current_ret = ret_type
    for stmt in fn.body:
        if isinstance(stmt, Let):
            v_t = sub.check_expr(stmt.value, local)
            local.bind(stmt.name, v_t)
        elif isinstance(stmt, Assign):
            sub.check_expr(stmt.value, local)
        elif isinstance(stmt, Return):
            sub.check_expr(stmt.value, local)
        elif isinstance(stmt, (Break, Continue)):
            pass
        else:
            sub.check_expr(stmt.expr, local)
    for t in list(sub.types.values()):
        sub._default(t)
    sub._default(ret_type)
    return {k: sub.prune(v) for k, v in sub.types.items()}


def mangle_type(t: Type, checker: Checker) -> str:
    t = checker.prune(t)
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
    if isinstance(t, TCon):
        if not t.args:
            return t.name
        return t.name + "_" + "_".join(mangle_type(a, checker) for a in t.args)
    if isinstance(t, TFunc):
        return (
            "Fn_"
            + "_".join(mangle_type(p, checker) for p in t.params)
            + "_R_"
            + mangle_type(t.ret, checker)
        )
    raise RuntimeError(f"cannot mangle type: {show(t)}")


def mangle_spec_name(fn_name: str, arg_types: List[Type], checker: Checker) -> str:
    if not arg_types:
        return f"myl_spec_{fn_name}__0"
    return (
        f"myl_spec_{fn_name}__"
        + "__".join(mangle_type(t, checker) for t in arg_types)
    )


def collect_specializations(checker: Checker, program) -> Dict:
    """BFS по вызовам полиморфных функций. Возвращает словарь:
    key = (fn_name, tuple(mangled_type)) -> (fn, arg_types, ret_type, spec_types).
    """
    fn_by_name = {fn.name: fn for fn in program.functions}
    fn_sigs = checker.fn_sigs

    def is_poly(name):
        sig = fn_sigs.get(name)
        return sig is not None and checker.has_generic(sig)

    result: Dict = {}
    pending: List[Tuple] = []
    queued = set()

    def collect_expr(e, types_map):
        if isinstance(e, Call):
            if is_poly(e.name):
                ok = True
                arg_types: List[Type] = []
                for a in e.args:
                    at = types_map.get(id(a))
                    if at is None:
                        ok = False
                        break
                    at = checker.deep_prune(at)
                    if isinstance(at, TVar):
                        ok = False
                        break
                    arg_types.append(at)
                if ok:
                    rt = types_map.get(id(e))
                    if rt is not None:
                        rt = checker.deep_prune(rt)
                        if not isinstance(rt, TVar):
                            key = (
                                e.name,
                                tuple(mangle_type(t, checker) for t in arg_types),
                            )
                            if key not in queued:
                                queued.add(key)
                                pending.append((e.name, arg_types, rt))
            for a in e.args:
                collect_expr(a, types_map)
        elif isinstance(e, BinOp):
            collect_expr(e.left, types_map)
            collect_expr(e.right, types_map)
        elif isinstance(e, UnOp):
            collect_expr(e.operand, types_map)
        elif isinstance(e, Try):
            collect_expr(e.expr, types_map)
        elif isinstance(e, Match):
            collect_expr(e.scrutinee, types_map)
            for arm in e.arms:
                collect_expr(arm.body, types_map)
        elif isinstance(e, If):
            collect_expr(e.cond, types_map)
            for s in e.then_body:
                collect_stmt(s, types_map)
            for s in e.else_body:
                collect_stmt(s, types_map)
        elif isinstance(e, For):
            collect_expr(e.iterable, types_map)
            for s in e.body:
                collect_stmt(s, types_map)
        elif isinstance(e, While):
            collect_expr(e.cond, types_map)
            for s in e.body:
                collect_stmt(s, types_map)
        elif isinstance(e, Cmd):
            for kind, val in e.parts:
                if kind == "expr":
                    collect_expr(val, types_map)
            if e.stdin_expr is not None:
                collect_expr(e.stdin_expr, types_map)
        elif isinstance(e, InterpStr):
            for kind, val in e.parts:
                if kind == "expr":
                    collect_expr(val, types_map)
        elif isinstance(e, FieldAccess):
            collect_expr(e.obj, types_map)
        elif isinstance(e, MethodCall):
            collect_expr(e.obj, types_map)
            for a in e.args:
                collect_expr(a, types_map)
        elif isinstance(e, ListLit):
            for i in e.items:
                collect_expr(i, types_map)
        elif isinstance(e, Lambda):
            collect_expr(e.body, types_map)
        elif isinstance(e, PairLambda):
            collect_expr(e.body, types_map)

    def collect_stmt(s, types_map):
        if isinstance(s, (Let, Assign)):
            collect_expr(s.value, types_map)
        elif isinstance(s, ExprStmt):
            collect_expr(s.expr, types_map)
        elif isinstance(s, Return):
            collect_expr(s.value, types_map)        

    for fn in program.functions:
        for stmt in fn.body:
            collect_stmt(stmt, checker.types)

    while pending:
        if len(result) > 500:
            raise RuntimeError(
                "too many specializations (>500) — infinite polymorphic recursion?"
            )
        (fn_name, arg_types, ret_type) = pending.pop(0)
        key = (fn_name, tuple(mangle_type(t, checker) for t in arg_types))
        if key in result:
            continue
        fn = fn_by_name[fn_name]
        spec_types = specialize_body(checker, fn, arg_types, ret_type)
        result[key] = (fn, arg_types, ret_type, spec_types)
        for stmt in fn.body:
            collect_stmt(stmt, spec_types)

    return result
