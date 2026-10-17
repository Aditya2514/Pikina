"""
Capability: utility.calculate
Evaluates arithmetic safely by walking the parsed expression tree — no eval().
Supports + - * / // % ** (also ^ and x/× for power and multiplication),
parentheses, and functions/constants from the math module (sqrt, sin, pi, ...).
"""
import ast
import math
import operator
import re

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {k: v for k, v in math.__dict__.items() if callable(v) and not k.startswith("_")}
_FUNCS.update({"abs": abs, "round": round, "max": max, "min": min})
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_EXPONENT = 1000  # stops 9**9**9 from freezing the backend


def _normalize(expr: str) -> str:
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    # "3 x 4" / "3x4" means multiply, but leave names like max/exp alone
    return re.sub(r"(?<=[\d\s)])[xX](?=[\s\d(])", "*", expr)


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS and not node.keywords):
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise ValueError("factorial argument too large")
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"unsupported element: {ast.dump(node)[:40]}")


def run(params: dict) -> dict:
    expr = params.get("expr", "").strip()
    if not expr:
        return {"status": "error", "reason": "Missing math expression"}
    try:
        result = _eval(ast.parse(_normalize(expr), mode="eval"))
    except ZeroDivisionError:
        return {"status": "error", "reason": "Division by zero."}
    except Exception as e:
        return {"status": "error", "reason": f"Failed to evaluate expression: {e}"}
    if isinstance(result, float) and result.is_integer():
        shown = str(int(result))
    else:
        shown = str(result)
    return {
        "status": "ok",
        "tool": "utility.calculate",
        "expr": expr,
        "result": float(result),
        "message": f"Calculation result: {shown}",
    }
