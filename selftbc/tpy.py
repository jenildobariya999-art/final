"""TPY executor: a restricted Python dialect, compiled as a flat script.

NOTE: this is a best-effort sandbox (AST checks + whitelisted builtins). It is meant for
running YOUR OWN bot code on YOUR OWN server. Do not run untrusted users' code with it
without extra isolation (containers / separate OS users).
"""
import ast
import builtins as _b


class ReturnCommand(Exception):
    """raise ReturnCommand -> stop the current command (normal completion)."""


FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars",
    "input", "breakpoint", "memoryview", "setattr", "delattr", "exit", "quit",
}

SAFE_BUILTIN_NAMES = """abs all any bin bool callable chr divmod enumerate filter float format getattr
hasattr hash hex id int isinstance issubclass iter len list map max min next oct ord pow range
reversed round set slice sorted str sum tuple type zip dict repr bytes bytearray print frozenset
Exception ValueError TypeError IndexError KeyError NameError ZeroDivisionError AttributeError
RuntimeError StopIteration ArithmeticError LookupError OverflowError True False None
NotImplementedError AssertionError UnicodeError KeyboardInterrupt""".split()


def _safe_builtins():
    d = {}
    for n in SAFE_BUILTIN_NAMES:
        if hasattr(_b, n):
            d[n] = getattr(_b, n)

    def _getattr(o, name, *default):
        if isinstance(name, str) and name.startswith("_"):
            raise AttributeError(name)
        return getattr(o, name, *default)

    d["getattr"] = _getattr
    d["hasattr"] = lambda o, n: (not str(n).startswith("_")) and hasattr(o, n)
    d["type"] = lambda o: type(o)
    d["print"] = lambda *a, **k: None
    return d


SAFE_BUILTINS = _safe_builtins()


class TPYSyntaxError(Exception):
    pass


def check(code, filename="<tpy>"):
    """Validate TPY source. Returns compiled code object or raises TPYSyntaxError."""
    try:
        tree = ast.parse(code, filename)
    except SyntaxError as e:
        raise TPYSyntaxError(f"SyntaxError line {e.lineno}: {e.msg}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise TPYSyntaxError(f"line {node.lineno}: 'import' is not allowed in TPY")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise TPYSyntaxError(f"line {node.lineno}: access to dunder attribute '{node.attr}' is not allowed")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise TPYSyntaxError(f"line {node.lineno}: '{node.id}' is not allowed in TPY")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            pass
    try:
        return compile(tree, filename, "exec")
    except SyntaxError as e:
        msg = e.msg
        if "return" in msg:
            msg += "  (use `raise ReturnCommand` instead of a top-level return)"
        raise TPYSyntaxError(f"SyntaxError line {e.lineno}: {msg}")


def execute(code, namespace, filename="<tpy>"):
    """Compile+run `code` in `namespace` (dict). ReturnCommand is swallowed."""
    co = check(code, filename)
    namespace["__builtins__"] = SAFE_BUILTINS
    try:
        exec(co, namespace)
    except ReturnCommand:
        pass
    return namespace
