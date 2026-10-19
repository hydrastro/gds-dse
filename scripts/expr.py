"""Tiny, safe arithmetic/comparison evaluator for config constraints and derived metrics.

Supports numbers, variable names, + - * / ** %, unary -, comparisons (chained),
and/or/not, parentheses and abs/min/max/log/sqrt. No attribute access or calls
to anything else, so experiment files can never execute code.
"""
import ast
import math
import operator

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}
_FUN = {"abs": abs, "min": min, "max": max, "log": math.log, "sqrt": math.sqrt}


class Unresolved(KeyError):
    """A referenced name has no value."""


def names(expression):
    """Variable names referenced by an expression."""
    return sorted({node.id for node in ast.walk(ast.parse(expression, mode="eval"))
                   if isinstance(node, ast.Name) and node.id not in _FUN})


def evaluate(expression, lookup):
    """Evaluate with lookup(name) -> value (raise Unresolved for missing names)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
            return node.value
        if isinstance(node, ast.Name):
            value = lookup(node.id)
            if value is None:
                raise Unresolved(node.id)
            return value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            value = ev(node.operand)
            return -value if isinstance(node.op, ast.USub) else (+value if isinstance(node.op, ast.UAdd) else not value)
        if isinstance(node, ast.BoolOp):
            values = [ev(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = ev(comparator)
                if type(op) not in _CMP or not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUN
                and not node.keywords):
            return _FUN[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"Unsupported expression element: {ast.dump(node)[:60]}")
    return ev(ast.parse(expression, mode="eval"))
