"""Type checker mylang: TypeEnv, Checker, collect_calls."""

from typing import Dict, List, Optional

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
    TFunc, TCon, TVar, TGeneric, show,
)


BUILTINS = {
    "print", "printnnl", "eprint", "eprintnnl",
    "Some", "None", "Ok", "Err", "unit",
    "read_file", "write_file", "args", "getenv", "exit",
    "sleep", "now",
    "abs", "min", "max",
    "sh", "map",
}


class TypeError_(Exception):
    def __init__(self, msg, pos=None):
        super().__init__(msg)
        self.msg = msg
        self.pos = pos


class TypeEnv:
    def __init__(self, parent=None):
        self.vars: Dict[str, Type] = {}
        self.mutable = set()
        self.parent = parent

    def lookup(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        return None

    def bind(self, name, t, mutable=False):
        self.vars[name] = t
        if mutable:
            self.mutable.add(name)

    def has_local(self, name):
        return name in self.vars

    def is_mutable(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return name in e.mutable
            e = e.parent
        return False

    def all_types(self):
        e = self
        while e is not None:
            for t in e.vars.values():
                yield t
            e = e.parent


class Checker:
    def __init__(self):
        self.next_var_id = 0
        self.types: Dict[int, Type] = {}
        self.fn_sigs: Dict[str, Type] = {}
        self.current_ret: Optional[Type] = None
        self.loop_depth = 0
        self.in_main = False

    def fresh(self):
        v = TVar(self.next_var_id)
        self.next_var_id += 1
        return v

    def prune(self, t):
        if isinstance(t, TVar) and t.instance is not None:
            t.instance = self.prune(t.instance)
            return t.instance
        return t

    def deep_prune(self, t):
        t = self.prune(t)
        if isinstance(t, TCon):
            return TCon(t.name, [self.deep_prune(a) for a in t.args])
        if isinstance(t, TFunc):
            return TFunc([self.deep_prune(p) for p in t.params], self.deep_prune(t.ret))
        return t

    def occurs(self, v, t):
        t = self.prune(t)
        if t is v:
            return True
        if isinstance(t, TFunc):
            return any(self.occurs(v, p) for p in t.params) or self.occurs(v, t.ret)
        if isinstance(t, TCon):
            return any(self.occurs(v, a) for a in t.args)
        return False

    def unify(self, a, b, pos=None):
        a = self.prune(a)
        b = self.prune(b)
        if a is b:
            return
        if isinstance(a, TVar):
            if self.occurs(a, b):
                raise TypeError_(f"infinite type: {show(a)} = {show(b)}", pos)
            a.instance = b
            return
        if isinstance(b, TVar):
            if self.occurs(b, a):
                raise TypeError_(f"infinite type: {show(b)} = {show(a)}", pos)
            b.instance = a
            return
        if isinstance(a, TCon) and isinstance(b, TCon):
            if a.name != b.name or len(a.args) != len(b.args):
                raise TypeError_(f"type mismatch: {show(a)} vs {show(b)}", pos)
            for x, y in zip(a.args, b.args):
                self.unify(x, y, pos)
            return
        if type(a) is not type(b):
            raise TypeError_(f"type mismatch: {show(a)} vs {show(b)}", pos)
        if isinstance(a, TFunc):
            if len(a.params) != len(b.params):
                raise TypeError_("arity mismatch", pos)
            for p, q in zip(a.params, b.params):
                self.unify(p, q, pos)
            self.unify(a.ret, b.ret, pos)

    def free_tvars(self, t, acc=None):
        if acc is None:
            acc = set()
        t = self.prune(t)
        if isinstance(t, TVar):
            acc.add(t.id)
        elif isinstance(t, TFunc):
            for p in t.params:
                self.free_tvars(p, acc)
            self.free_tvars(t.ret, acc)
        elif isinstance(t, TCon):
            for a in t.args:
                self.free_tvars(a, acc)
        return acc

    def generalize(self, env, t):
        ftv_t = self.free_tvars(t)
        ftv_env = set()
        for s in env.all_types():
            self.free_tvars(s, ftv_env)
        gen = ftv_t - ftv_env
        return self.replace_tvars(t, gen)

    def replace_tvars(self, t, ids):
        t = self.prune(t)
        if isinstance(t, TVar) and t.id in ids:
            return TGeneric(t.id)
        if isinstance(t, TFunc):
            return TFunc(
                [self.replace_tvars(p, ids) for p in t.params],
                self.replace_tvars(t.ret, ids),
            )
        if isinstance(t, TCon):
            return TCon(t.name, [self.replace_tvars(a, ids) for a in t.args])
        return t

    def instantiate(self, t):
        mapping = {}

        def go(t):
            if isinstance(t, TGeneric):
                if t.id not in mapping:
                    mapping[t.id] = self.fresh()
                return mapping[t.id]
            if isinstance(t, TFunc):
                return TFunc([go(p) for p in t.params], go(t.ret))
            if isinstance(t, TCon):
                return TCon(t.name, [go(a) for a in t.args])
            return t

        return go(t)

    def has_generic(self, t):
        if isinstance(t, TGeneric):
            return True
        if isinstance(t, TFunc):
            return any(self.has_generic(p) for p in t.params) or self.has_generic(t.ret)
        if isinstance(t, TCon):
            return any(self.has_generic(a) for a in t.args)
        return False

    # --- expressions ---

    def check_expr(self, e, env):
        if isinstance(e, IntLit):
            t = TInt()
        elif isinstance(e, StringLit):
            t = TStr()
        elif isinstance(e, InterpStr):
            t = self.check_interp(e, env)
        elif isinstance(e, BoolLit):
            t = TBool()
        elif isinstance(e, VarRef):
            found = env.lookup(e.name)
            if found is None:
                raise TypeError_(f"undefined variable '{e.name}'", e.pos)
            t = self.instantiate(found)
        elif isinstance(e, UnOp):
            t = self.check_unop(e, env)
        elif isinstance(e, If):
            t = self.check_if(e, env)
        elif isinstance(e, For):
            t = self.check_for(e, env)
        elif isinstance(e, While):
            t = self.check_while(e, env)
        elif isinstance(e, BinOp):
            t = self.check_binop(e, env)
        elif isinstance(e, Call):
            t = self.check_call(e, env)
        elif isinstance(e, Try):
            t = self.check_try(e, env)
        elif isinstance(e, Match):
            t = self.check_match(e, env)
        elif isinstance(e, Cmd):
            t = self.check_cmd(e, env)
        elif isinstance(e, FieldAccess):
            t = self.check_field(e, env)
        elif isinstance(e, MethodCall):
            t = self.check_method(e, env)
        elif isinstance(e, ListLit):
            t = self.check_list_lit(e, env)
        elif isinstance(e, Lambda):
            raise TypeError_(
                "lambda is only valid as argument of .map/.filter/.each", e.pos
            )
        elif isinstance(e, PairLambda):
            raise TypeError_(
                "pair lambda is only valid as argument of Map.each", e.pos
            )
        else:
            raise TypeError_(f"unsupported expr: {e!r}")
        self.types[id(e)] = t
        return t

    def check_for(self, e, env):
        it = self.prune(self.check_expr(e.iterable, env))
        if isinstance(it, TVar):
            self.unify(it, TCon("List", [self.fresh()]), e.pos)
            it = self.prune(it)
        if not (isinstance(it, TCon) and it.name == "List"):
            raise TypeError_(f"for expects a list, got {show(it)}", e.pos)
        elem_t = it.args[0]
        local = TypeEnv(parent=env)
        local.bind(e.var, elem_t)
        self.loop_depth += 1
        try:
            for stmt in e.body:
                self.check_stmt(stmt, local)
        finally:
            self.loop_depth -= 1
        return TUnit()

    def check_while(self, e, env):
        cond_t = self.check_expr(e.cond, env)
        self.unify(cond_t, TBool(), e.pos)
        local = TypeEnv(parent=env)
        self.loop_depth += 1
        try:
            for stmt in e.body:
                self.check_stmt(stmt, local)
        finally:
            self.loop_depth -= 1
        return TUnit()

    def check_stmt(self, stmt, env):
        if isinstance(stmt, Let):
            v_t = self.check_expr(stmt.value, env)
            if isinstance(self.prune(v_t), TUnit):
                raise TypeError_(f"cannot bind Unit to '{stmt.name}'", stmt.pos)
            if env.has_local(stmt.name):
                raise TypeError_(f"duplicate binding '{stmt.name}'", stmt.pos)
            env.bind(stmt.name, v_t, mutable=True)
            return TUnit()
        if isinstance(stmt, Assign):
            if not env.is_mutable(stmt.name):
                raise TypeError_(f"cannot assign to '{stmt.name}'", stmt.pos)
            found = env.lookup(stmt.name)
            v_t = self.check_expr(stmt.value, env)
            self.unify(found, v_t, stmt.pos)
            return TUnit()
        if isinstance(stmt, Break):
            if self.loop_depth == 0:
                raise TypeError_("'break' outside of loop", stmt.pos)
            return TUnit()
        if isinstance(stmt, Continue):
            if self.loop_depth == 0:
                raise TypeError_("'continue' outside of loop", stmt.pos)
            return TUnit()
        if isinstance(stmt, Return):
            if self.in_main:
                raise TypeError_(
                    "'return' is not allowed in 'main'; use exit(N)", stmt.pos
                )
            v_t = self.check_expr(stmt.value, env)
            self.unify(v_t, self.current_ret, stmt.pos)
            return TUnit()
        return self.check_expr(stmt.expr, env)

    def check_interp(self, e, env):
        for kind, val in e.parts:
            if kind == "expr":
                t = self.prune(self.check_expr(val, env))
                if isinstance(t, TVar):
                    self.unify(t, TStr(), e.pos)
                elif not isinstance(t, (TStr, TInt, TBool)):
                    raise TypeError_(
                        f"cannot interpolate value of type {show(t)}", e.pos
                    )
        return TStr()

    def check_list_lit(self, e, env):
        if not e.items:
            return TCon("List", [self.fresh()])
        first_t = self.check_expr(e.items[0], env)
        for item in e.items[1:]:
            it = self.check_expr(item, env)
            self.unify(first_t, it, item.pos)
        elem_t = self.prune(first_t)
        if isinstance(elem_t, TCon) and elem_t.name in ("Option", "Result"):
            raise TypeError_("List of Option/Result is not supported", e.pos)
        return TCon("List", [first_t])

    def check_cmd(self, e, env):
        if e.use_shell:
            if len(e.parts) != 1 or e.parts[0][0] != "expr":
                raise TypeError_("internal: sh() must have single expr", e.pos)
            arg_t = self.prune(self.check_expr(e.parts[0][1], env))
            self.unify(arg_t, TStr(), e.pos)
        else:
            for kind, val in e.parts:
                if kind == "expr":
                    t = self.check_expr(val, env)
                    self.unify(t, TStr(), val.pos)
        if e.stdin_expr is not None:
            st = self.prune(self.check_expr(e.stdin_expr, env))
            self.unify(st, TStr(), e.pos)
        return TCmdResult()

    def check_field(self, e, env):
        obj_t = self.prune(self.check_expr(e.obj, env))
        if isinstance(obj_t, TCmdResult):
            if e.field == "status":
                return TInt()
            if e.field in ("stdout", "stderr"):
                return TStr()
            raise TypeError_(f"CmdResult has no field '{e.field}'", e.pos)
        raise TypeError_(f"field access on {show(obj_t)}", e.pos)

    def check_method(self, e, env):
        obj_t = self.prune(self.check_expr(e.obj, env))

        STR_ONLY = {
            "trim", "upper", "lower", "contains", "starts_with",
            "ends_with", "replace", "split", "to_int", "to_str",
            "lines", "repeat", "pad_left", "pad_right",
            "matches", "find_all", "replace_re",
        }
        LIST_ONLY = {
            "map", "filter", "join", "reverse", "take", "drop",
            "each", "first", "last", "sort", "contains", "unique",
            "group_by",
        }
        MAP_ONLY = {"set", "has", "keys", "values", "items", "delete", "each"}
        OPTION_ONLY = {"or"}

        if isinstance(obj_t, TVar):
            m = e.method
            if m == "get":
                if e.args:
                    at = self.prune(self.check_expr(e.args[0], env))
                    if isinstance(at, TStr):
                        self.unify(obj_t, TCon("Map", [TStr(), self.fresh()]), e.pos)
                    elif isinstance(at, TInt):
                        self.unify(obj_t, TCon("List", [self.fresh()]), e.pos)
                    else:
                        self.unify(obj_t, TCon("Map", [TStr(), self.fresh()]), e.pos)
                else:
                    self.unify(obj_t, TCon("Map", [TStr(), self.fresh()]), e.pos)
            elif m == "contains":
                # `contains` есть и у Str, и у List. Разрешаем по типу аргумента:
                # аргумент Str → скорее всего строка; иначе список.
                if len(e.args) != 1:
                    raise TypeError_("contains takes 1 arg", e.pos)
                at = self.prune(self.check_expr(e.args[0], env))
                if isinstance(at, TStr):
                    self.unify(obj_t, TStr(), e.pos)
                else:
                    self.unify(obj_t, TCon("List", [self.fresh()]), e.pos)
            elif m in MAP_ONLY:
                self.unify(obj_t, TCon("Map", [TStr(), self.fresh()]), e.pos)
            elif m in LIST_ONLY:
                self.unify(obj_t, TCon("List", [self.fresh()]), e.pos)
            elif m in STR_ONLY:
                self.unify(obj_t, TStr(), e.pos)
            elif m in OPTION_ONLY:
                self.unify(obj_t, TCon("Option", [self.fresh()]), e.pos)
            else:
                self.unify(obj_t, TCon("List", [self.fresh()]), e.pos)
            obj_t = self.prune(obj_t)

        if isinstance(obj_t, TCon) and obj_t.name == "List":
            return self.check_list_method(e, env, obj_t)
        if isinstance(obj_t, TCon) and obj_t.name == "Map":
            return self.check_map_method(e, env, obj_t)
        if isinstance(obj_t, TStr):
            return self.check_str_method(e, env)
        if isinstance(obj_t, TCon) and obj_t.name == "Option":
            return self.check_option_method(e, env, obj_t)
        if isinstance(obj_t, TInt):
            if e.method == "to_str":
                if e.args:
                    raise TypeError_("to_str takes no args", e.pos)
                return TStr()
            raise TypeError_(f"unknown Int method '{e.method}'", e.pos)
        raise TypeError_(f"method call on {show(obj_t)}", e.pos)

    def check_map_method(self, e, env, map_t):
        val_t = map_t.args[1]
        m = e.method
        if m == "get":
            if len(e.args) != 1:
                raise TypeError_("get takes 1 arg", e.pos)
            k_t = self.check_expr(e.args[0], env)
            self.unify(k_t, TStr(), e.pos)
            return TCon("Option", [val_t])
        if m == "set":
            if len(e.args) != 2:
                raise TypeError_("set takes 2 args", e.pos)
            k_t = self.check_expr(e.args[0], env)
            self.unify(k_t, TStr(), e.pos)
            v_t = self.check_expr(e.args[1], env)
            self.unify(v_t, val_t, e.pos)
            return map_t
        if m == "has":
            if len(e.args) != 1:
                raise TypeError_("has takes 1 arg", e.pos)
            k_t = self.check_expr(e.args[0], env)
            self.unify(k_t, TStr(), e.pos)
            return TBool()
        if m == "keys":
            if e.args:
                raise TypeError_("keys takes no args", e.pos)
            return TCon("List", [TStr()])
        if m == "values":
            if e.args:
                raise TypeError_("values takes no args", e.pos)
            return TCon("List", [val_t])
        if m == "items":
            if e.args:
                raise TypeError_("items takes no args", e.pos)
            return TCon("List", [TStr()])
        if m == "len":
            if e.args:
                raise TypeError_("len takes no args", e.pos)
            return TInt()
        if m == "delete":
            if len(e.args) != 1:
                raise TypeError_("delete takes 1 arg", e.pos)
            k_t = self.check_expr(e.args[0], env)
            self.unify(k_t, TStr(), e.pos)
            return map_t
        if m == "each":
            if len(e.args) != 1:
                raise TypeError_("each takes 1 arg", e.pos)
            lam = e.args[0]
            if not isinstance(lam, PairLambda):
                raise TypeError_("Map.each expects (k, v) => ...", e.pos)
            local = TypeEnv(parent=env)
            local.bind(lam.k, TStr())
            local.bind(lam.v, val_t)
            self.check_expr(lam.body, local)
            return TUnit()
        raise TypeError_(f"unknown Map method '{m}'", e.pos)

    def check_option_method(self, e, env, opt_t):
        elem_t = opt_t.args[0]
        if e.method == "or":
            if len(e.args) != 1:
                raise TypeError_("or takes 1 arg", e.pos)
            default_t = self.check_expr(e.args[0], env)
            self.unify(default_t, elem_t, e.pos)
            return elem_t
        raise TypeError_(f"unknown Option method '{e.method}'", e.pos)

    def check_str_method(self, e, env):
        m = e.method
        if m == "len":
            if e.args:
                raise TypeError_("len takes no args", e.pos)
            return TInt()
        if m in ("trim", "upper", "lower"):
            if e.args:
                raise TypeError_(f"{m} takes no args", e.pos)
            return TStr()
        if m in ("contains", "starts_with", "ends_with"):
            if len(e.args) != 1:
                raise TypeError_(f"{m} takes 1 arg", e.pos)
            a_t = self.check_expr(e.args[0], env)
            self.unify(a_t, TStr(), e.pos)
            return TBool()
        if m == "replace":
            if len(e.args) != 2:
                raise TypeError_("replace takes 2 args", e.pos)
            for a in e.args:
                at = self.check_expr(a, env)
                self.unify(at, TStr(), e.pos)
            return TStr()
        if m == "split":
            if len(e.args) != 1:
                raise TypeError_("split takes 1 arg", e.pos)
            at = self.check_expr(e.args[0], env)
            self.unify(at, TStr(), e.pos)
            return TCon("List", [TStr()])
        if m == "lines":
            if e.args:
                raise TypeError_("lines takes no args", e.pos)
            return TCon("List", [TStr()])
        if m == "repeat":
            if len(e.args) != 1:
                raise TypeError_("repeat takes 1 arg", e.pos)
            at = self.check_expr(e.args[0], env)
            self.unify(at, TInt(), e.pos)
            return TStr()
        if m in ("pad_left", "pad_right"):
            if len(e.args) != 1:
                raise TypeError_(f"{m} takes 1 arg", e.pos)
            at = self.check_expr(e.args[0], env)
            self.unify(at, TInt(), e.pos)
            return TStr()
        if m == "to_int":
            if e.args:
                raise TypeError_("to_int takes no args", e.pos)
            return TCon("Option", [TInt()])
        if m == "to_str":
            if e.args:
                raise TypeError_("to_str takes no args", e.pos)
            return TStr()
        if m == "matches":
            if len(e.args) != 1:
                raise TypeError_("matches takes 1 arg", e.pos)
            at = self.check_expr(e.args[0], env)
            self.unify(at, TStr(), e.pos)
            return TBool()
        if m == "find_all":
            if len(e.args) != 1:
                raise TypeError_("find_all takes 1 arg", e.pos)
            at = self.check_expr(e.args[0], env)
            self.unify(at, TStr(), e.pos)
            return TCon("List", [TStr()])
        if m == "replace_re":
            if len(e.args) != 2:
                raise TypeError_("replace_re takes 2 args", e.pos)
            for a in e.args:
                at = self.check_expr(a, env)
                self.unify(at, TStr(), e.pos)
            return TStr()
        raise TypeError_(f"unknown string method '{m}'", e.pos)

    def check_list_method(self, e, env, list_t):
        elem_t = list_t.args[0]
        m = e.method
        if m == "len":
            if e.args:
                raise TypeError_("len takes no args", e.pos)
            return TInt()
        if m == "each":
            if len(e.args) != 1:
                raise TypeError_("each takes 1 arg", e.pos)
            lam = e.args[0]
            if not isinstance(lam, Lambda):
                raise TypeError_("each expects a lambda", e.pos)
            local = TypeEnv(parent=env)
            local.bind(lam.param, elem_t)
            self.check_expr(lam.body, local)
            self.types[id(lam)] = TFunc([elem_t], TUnit())
            return TUnit()
        if m == "map":
            if len(e.args) != 1:
                raise TypeError_("map takes 1 arg", e.pos)
            lam = e.args[0]
            if not isinstance(lam, Lambda):
                raise TypeError_("map expects a lambda", e.pos)
            local = TypeEnv(parent=env)
            local.bind(lam.param, elem_t)
            body_t = self.check_expr(lam.body, local)
            self.types[id(lam)] = TFunc([elem_t], body_t)
            bt = self.prune(body_t)
            if isinstance(bt, TCon) and bt.name in ("Option", "Result"):
                raise TypeError_(
                    "map producing Option/Result is not supported", e.pos
                )
            return TCon("List", [body_t])
        if m == "filter":
            if len(e.args) != 1:
                raise TypeError_("filter takes 1 arg", e.pos)
            lam = e.args[0]
            if not isinstance(lam, Lambda):
                raise TypeError_("filter expects a lambda", e.pos)
            local = TypeEnv(parent=env)
            local.bind(lam.param, elem_t)
            body_t = self.check_expr(lam.body, local)
            self.unify(body_t, TBool(), e.pos)
            self.types[id(lam)] = TFunc([elem_t], TBool())
            return list_t
        if m == "group_by":
            if len(e.args) != 1:
                raise TypeError_("group_by takes 1 arg", e.pos)
            lam = e.args[0]
            if not isinstance(lam, Lambda):
                raise TypeError_("group_by expects a lambda", e.pos)
            local = TypeEnv(parent=env)
            local.bind(lam.param, elem_t)
            body_t = self.check_expr(lam.body, local)
            self.unify(body_t, TStr(), e.pos)
            self.types[id(lam)] = TFunc([elem_t], TStr())
            et = self.prune(elem_t)
            if isinstance(et, TVar):
                self.unify(et, TStr(), e.pos)
                et = TStr()
            if not isinstance(et, (TInt, TStr, TBool)):
                raise TypeError_(
                    f"group_by requires Int, Str, or Bool elements, got {show(et)}",
                    e.pos,
                )
            return TCon("Map", [TStr(), TCon("List", [elem_t])])
        if m == "join":
            if len(e.args) != 1:
                raise TypeError_("join takes 1 arg", e.pos)
            self.unify(elem_t, TStr(), e.pos)
            sep_t = self.check_expr(e.args[0], env)
            self.unify(sep_t, TStr(), e.pos)
            return TStr()
        if m == "reverse":
            if e.args:
                raise TypeError_("reverse takes no args", e.pos)
            return list_t
        if m in ("take", "drop"):
            if len(e.args) != 1:
                raise TypeError_(f"{m} takes 1 arg", e.pos)
            n_t = self.check_expr(e.args[0], env)
            self.unify(n_t, TInt(), e.pos)
            return list_t
        if m == "first":
            if e.args:
                raise TypeError_("first takes no args", e.pos)
            return TCon("Option", [elem_t])
        if m == "last":
            if e.args:
                raise TypeError_("last takes no args", e.pos)
            return TCon("Option", [elem_t])
        if m == "get":
            if len(e.args) != 1:
                raise TypeError_("get takes 1 arg", e.pos)
            n_t = self.check_expr(e.args[0], env)
            self.unify(n_t, TInt(), e.pos)
            return TCon("Option", [elem_t])
        if m == "sort":
            if e.args:
                raise TypeError_("sort takes no args", e.pos)
            et = self.prune(elem_t)
            if isinstance(et, TVar):
                self.unify(et, TStr(), e.pos)
                et = TStr()
            if not isinstance(et, (TInt, TStr)):
                raise TypeError_(
                    f"sort requires Int or Str elements, got {show(et)}", e.pos
                )
            return list_t
        if m == "contains":
            if len(e.args) != 1:
                raise TypeError_("contains takes 1 arg", e.pos)
            a_t = self.check_expr(e.args[0], env)
            self.unify(a_t, elem_t, e.pos)
            pt = self.prune(elem_t)
            if isinstance(pt, TVar):
                self.unify(pt, TStr(), e.pos)
                pt = TStr()
            if not isinstance(pt, (TInt, TStr, TBool)):
                raise TypeError_(
                    f"contains requires Int, Str, or Bool elements, got {show(pt)}",
                    e.pos,
                )
            return TBool()
        if m == "unique":
            if e.args:
                raise TypeError_("unique takes no args", e.pos)
            et = self.prune(elem_t)
            if isinstance(et, TVar):
                self.unify(et, TStr(), e.pos)
                et = TStr()
            if not isinstance(et, (TInt, TStr)):
                raise TypeError_(
                    f"unique requires Int or Str elements, got {show(et)}", e.pos
                )
            return list_t
        raise TypeError_(f"unknown list method '{m}'", e.pos)

    def check_unop(self, e, env):
        if e.op == "!":
            t = self.check_expr(e.operand, env)
            self.unify(t, TBool(), e.pos)
            return TBool()
        if e.op == "-":
            t = self.check_expr(e.operand, env)
            self.unify(t, TInt(), e.pos)
            return TInt()
        raise TypeError_(f"unknown unary operator '{e.op}'", e.pos)

    def check_binop(self, e, env):
        lt = self.check_expr(e.left, env)
        rt = self.check_expr(e.right, env)
        if e.op == "+":
            lt_p = self.prune(lt)
            if isinstance(lt_p, TCon) and lt_p.name == "List":
                self.unify(lt, rt, e.pos)
                return lt
            self.unify(lt, TInt(), e.pos)
            self.unify(rt, TInt(), e.pos)
            return TInt()
        if e.op in ("-", "*", "/", "%"):
            self.unify(lt, TInt(), e.pos)
            self.unify(rt, TInt(), e.pos)
            return TInt()
        if e.op in ("<", ">", "<=", ">="):
            self.unify(lt, TInt(), e.pos)
            self.unify(rt, TInt(), e.pos)
            return TBool()
        if e.op in ("==", "!="):
            self.unify(lt, rt, e.pos)
            return TBool()
        if e.op in ("&&", "||"):
            self.unify(lt, TBool(), e.pos)
            self.unify(rt, TBool(), e.pos)
            return TBool()
        raise TypeError_(f"unknown operator '{e.op}'", e.pos)

    def check_block(self, stmts, env):
        local = TypeEnv(parent=env)
        result_t = TUnit()
        for stmt in stmts:
            result_t = self.check_stmt(stmt, local)
        return result_t

    def check_if(self, e, env):
        cond_t = self.check_expr(e.cond, env)
        self.unify(cond_t, TBool(), e.pos)
        then_t = self.check_block(e.then_body, env)
        if e.else_body:
            else_t = self.check_block(e.else_body, env)
            self.unify(then_t, else_t, e.pos)
            return then_t
        self.unify(then_t, TUnit(), e.pos)
        return TUnit()

    def check_call(self, call, env):
        if call.name in ("print", "printnnl", "eprint", "eprintnnl"):
            if len(call.args) != 1:
                raise TypeError_("print expects 1 arg", call.pos)
            self.check_expr(call.args[0], env)
            return TUnit()
        if call.name == "unit":
            if call.args:
                raise TypeError_("unit expects 0 args", call.pos)
            return TUnit()
        if call.name == "Some":
            if len(call.args) != 1:
                raise TypeError_("Some expects 1 arg", call.pos)
            return TCon("Option", [self.check_expr(call.args[0], env)])
        if call.name == "None":
            if call.args:
                raise TypeError_("None expects 0 args", call.pos)
            return TCon("Option", [self.fresh()])
        if call.name == "Ok":
            if len(call.args) != 1:
                raise TypeError_("Ok expects 1 arg", call.pos)
            return TCon("Result", [self.check_expr(call.args[0], env), self.fresh()])
        if call.name == "Err":
            if len(call.args) != 1:
                raise TypeError_("Err expects 1 arg", call.pos)
            return TCon("Result", [self.fresh(), self.check_expr(call.args[0], env)])

        if call.name == "read_file":
            if len(call.args) != 1:
                raise TypeError_("read_file expects 1 arg", call.pos)
            at = self.check_expr(call.args[0], env)
            self.unify(at, TStr(), call.pos)
            return TCon("Result", [TStr(), TStr()])
        if call.name == "write_file":
            if len(call.args) != 2:
                raise TypeError_("write_file expects 2 args", call.pos)
            a1 = self.check_expr(call.args[0], env)
            self.unify(a1, TStr(), call.pos)
            a2 = self.check_expr(call.args[1], env)
            self.unify(a2, TStr(), call.pos)
            return TCon("Result", [TUnit(), TStr()])
        if call.name == "args":
            if call.args:
                raise TypeError_("args expects 0 args", call.pos)
            return TCon("List", [TStr()])
        if call.name == "getenv":
            if len(call.args) != 1:
                raise TypeError_("getenv expects 1 arg", call.pos)
            at = self.check_expr(call.args[0], env)
            self.unify(at, TStr(), call.pos)
            return TCon("Option", [TStr()])
        if call.name == "exit":
            if len(call.args) != 1:
                raise TypeError_("exit expects 1 arg", call.pos)
            at = self.check_expr(call.args[0], env)
            self.unify(at, TInt(), call.pos)
            return TUnit()
        if call.name == "sleep":
            if len(call.args) != 1:
                raise TypeError_("sleep expects 1 arg", call.pos)
            at = self.check_expr(call.args[0], env)
            self.unify(at, TInt(), call.pos)
            return TUnit()
        if call.name == "now":
            if call.args:
                raise TypeError_("now expects 0 args", call.pos)
            return TInt()
        if call.name == "abs":
            if len(call.args) != 1:
                raise TypeError_("abs expects 1 arg", call.pos)
            at = self.check_expr(call.args[0], env)
            self.unify(at, TInt(), call.pos)
            return TInt()
        if call.name in ("min", "max"):
            if len(call.args) != 2:
                raise TypeError_(f"{call.name} expects 2 args", call.pos)
            a_t = self.check_expr(call.args[0], env)
            b_t = self.check_expr(call.args[1], env)
            self.unify(a_t, b_t, call.pos)
            pt = self.prune(a_t)
            if isinstance(pt, TVar):
                self.unify(pt, TInt(), call.pos)
                pt = TInt()
            if not isinstance(pt, (TInt, TStr)):
                raise TypeError_(
                    f"{call.name} requires Int or Str, got {show(pt)}", call.pos
                )
            return pt
        if call.name == "map":
            if call.args:
                raise TypeError_("map expects 0 args", call.pos)
            return TCon("Map", [TStr(), self.fresh()])

        f_t = env.lookup(call.name)
        if f_t is None:
            raise TypeError_(f"undefined function '{call.name}'", call.pos)
        f_t = self.prune(self.instantiate(f_t))
        if not isinstance(f_t, TFunc):
            raise TypeError_(f"'{call.name}' is not a function", call.pos)
        if len(call.args) != len(f_t.params):
            raise TypeError_(
                f"'{call.name}' expects {len(f_t.params)} args, got {len(call.args)}",
                call.pos,
            )
        for arg, param_t in zip(call.args, f_t.params):
            arg_t = self.check_expr(arg, env)
            self.unify(arg_t, param_t, arg.pos)
        return f_t.ret

    def check_try(self, e, env):
        inner = self.prune(self.check_expr(e.expr, env))
        ret_t = self.prune(self.current_ret)
        if isinstance(inner, TCon) and inner.name == "Result":
            T, E = inner.args
            self.unify(ret_t, TCon("Result", [self.fresh(), E]), e.pos)
            return T
        if isinstance(inner, TCon) and inner.name == "Option":
            T = inner.args[0]
            self.unify(ret_t, TCon("Option", [T]), e.pos)
            return T
        if isinstance(inner, TCmdResult):
            self.unify(ret_t, TCon("Result", [self.fresh(), TStr()]), e.pos)
            return TCmdResult()
        raise TypeError_(
            f"'?' on non-Result/Option/CmdResult: {show(inner)}", e.pos
        )

    def check_match(self, e, env):
        scrut = self.prune(self.check_expr(e.scrutinee, env))
        if not (isinstance(scrut, TCon) and scrut.name in ("Option", "Result")):
            raise TypeError_(f"match on non-Option/Result: {show(scrut)}", e.pos)
        if len(e.arms) != 2:
            raise TypeError_("match requires exactly 2 arms", e.pos)
        result_t = self.fresh()
        seen = set()
        for arm in e.arms:
            pat = arm.pattern
            if scrut.name == "Option":
                if pat.ctor == "Some":
                    tag, b_t = 0, scrut.args[0]
                elif pat.ctor == "None":
                    tag, b_t = 1, None
                else:
                    raise TypeError_(f"bad pattern '{pat.ctor}' for Option", pat.pos)
            else:
                if pat.ctor == "Ok":
                    tag, b_t = 0, scrut.args[0]
                elif pat.ctor == "Err":
                    tag, b_t = 1, scrut.args[1]
                else:
                    raise TypeError_(f"bad pattern '{pat.ctor}' for Result", pat.pos)
            if tag in seen:
                raise TypeError_("duplicate pattern", pat.pos)
            seen.add(tag)
            arm_env = TypeEnv(parent=env)
            if b_t is not None:
                if pat.binding is None:
                    raise TypeError_(
                        f"pattern '{pat.ctor}' needs binding", pat.pos
                    )
                arm_env.bind(pat.binding, b_t)
            arm_t = self.check_expr(arm.body, arm_env)
            self.unify(arm_t, result_t, arm.body.pos)
        return result_t

    def _default(self, t):
        t = self.prune(t)
        if isinstance(t, TVar):
            t.instance = TInt()
        elif isinstance(t, TFunc):
            self._default(t.ret)
            for p in t.params:
                self._default(p)
        elif isinstance(t, TCon):
            for a in t.args:
                self._default(a)

    # --- program ---

    def check_program(self, program):
        sigs = {}
        for fn in program.functions:
            if fn.name in BUILTINS:
                raise TypeError_(f"cannot redefine builtin '{fn.name}'", fn.pos)
            if fn.name in sigs:
                raise TypeError_(f"duplicate function '{fn.name}'", fn.pos)
            param_ts = [self.fresh() for _ in fn.params]
            ret_t = self.fresh()
            sigs[fn.name] = TFunc(param_ts, ret_t)

        env = TypeEnv()
        fn_by_name = {fn.name: fn for fn in program.functions}
        for name in self.topo_order(program):
            fn = fn_by_name[name]
            sig = sigs[name]
            env.vars[name] = sig
            local = TypeEnv(parent=env)
            for pname, ptype in zip(fn.params, sig.params):
                if pname in BUILTINS:
                    raise TypeError_(f"builtin as parameter", fn.pos)
                if local.has_local(pname):
                    raise TypeError_(f"duplicate parameter '{pname}'", fn.pos)
                local.bind(pname, ptype)
            self.current_ret = sig.ret
            self.in_main = (name == "main")
            body_t = TUnit()
            last_stmt_pos = fn.pos
            for stmt in fn.body:
                body_t = self.check_stmt(stmt, local)
                if isinstance(stmt, ExprStmt):
                    last_stmt_pos = getattr(stmt.expr, "pos", fn.pos)
                elif isinstance(stmt, (Let, Assign, Return)):
                    last_stmt_pos = stmt.pos
            self.unify(body_t, sig.ret, last_stmt_pos)
            self.in_main = False
            if fn.body and isinstance(fn.body[-1], Return):
                raise TypeError_(
                    "'return' as last statement if redundant; "
                    "use the final expression instead",
                    fn.body[-1].pos,
                )
            env.vars.pop(name)
            if name == "main":
                gen_sig = sig
            else:
                gen_sig = self.generalize(env, sig)
            env.vars[name] = gen_sig
            self.fn_sigs[name] = gen_sig
        return self.fn_sigs

    def topo_order(self, program):
        names = {fn.name for fn in program.functions}
        deps = {fn.name: collect_calls(fn) & names for fn in program.functions}
        order, visited, visiting = [], set(), set()

        def visit(name):
            if name in visited:
                return True
            if name in visiting:
                return False
            visiting.add(name)
            for d in deps[name]:
                if not visit(d):
                    return False
            visiting.discard(name)
            visited.add(name)
            order.append(name)
            return True

        for fn in program.functions:
            if not visit(fn.name):
                return [f.name for f in program.functions]
        return order


def collect_calls(fn):
    """Множество имён функций, которые `fn` вызывает напрямую или транзитивно
    через вложенные выражения."""
    acc = set()

    def walk(e):
        if isinstance(e, Call):
            acc.add(e.name)
            for a in e.args:
                walk(a)
        elif isinstance(e, BinOp):
            walk(e.left)
            walk(e.right)
        elif isinstance(e, UnOp):
            walk(e.operand)
        elif isinstance(e, Try):
            walk(e.expr)
        elif isinstance(e, Match):
            walk(e.scrutinee)
            for arm in e.arms:
                walk(arm.body)
        elif isinstance(e, If):
            walk(e.cond)
            for s in e.then_body:
                if isinstance(s, (Let, Assign)):
                    walk(s.value)
                elif isinstance(s, Return):
                    walk(s.value)
                elif isinstance(s, ExprStmt):
                    walk(s.expr)
            for s in e.else_body:
                if isinstance(s, (Let, Assign)):
                    walk(s.value)
                elif isinstance(s, Return):
                    walk(s.value)
                elif isinstance(s, ExprStmt):
                    walk(s.expr)
        elif isinstance(e, For):
            walk(e.iterable)
            for s in e.body:
                if isinstance(s, (Let, Assign)):
                    walk(s.value)
                elif isinstance(s, Return):
                    walk(s.value)
                elif isinstance(s, ExprStmt):
                    walk(s.expr)
        elif isinstance(e, While):
            walk(e.cond)
            for s in e.body:
                if isinstance(s, (Let, Assign)):
                    walk(s.value)
                elif isinstance(s, Return):
                    walk(s.value)
                elif isinstance(s, ExprStmt):
                    walk(s.expr)
        elif isinstance(e, Cmd):
            for kind, val in e.parts:
                if kind == "expr":
                    walk(val)
            if e.stdin_expr is not None:
                walk(e.stdin_expr)
        elif isinstance(e, InterpStr):
            for kind, val in e.parts:
                if kind == "expr":
                    walk(val)
        elif isinstance(e, FieldAccess):
            walk(e.obj)
        elif isinstance(e, MethodCall):
            walk(e.obj)
            for a in e.args:
                walk(a)
        elif isinstance(e, ListLit):
            for i in e.items:
                walk(i)
        elif isinstance(e, Lambda):
            walk(e.body)
        elif isinstance(e, PairLambda):
            walk(e.body)

    for stmt in fn.body:
        if isinstance(stmt, (Let, Assign)):
            walk(stmt.value)
        elif isinstance(stmt, ExprStmt):
            walk(stmt.expr)
        elif isinstance(stmt, Return):
            walk(stmt.value)
    return acc
