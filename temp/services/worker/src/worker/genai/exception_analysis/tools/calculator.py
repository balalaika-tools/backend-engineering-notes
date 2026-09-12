"""Bounded arithmetic tool for exception analysis."""

import ast
import math
import operator
from collections.abc import Callable

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from worker.genai.shared.limits import consume_tool_call

Number = int | float
BinaryOperation = Callable[[Number, Number], Number]
UnaryOperation = Callable[[Number], Number]

_BINARY_OPERATIONS: dict[type[ast.operator], BinaryOperation] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY_OPERATIONS: dict[type[ast.unaryop], UnaryOperation] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Number]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
}
_CONSTANTS: dict[str, Number] = {"pi": math.pi, "e": math.e}
_MAX_INTEGER_BITS = 4096
_MAX_EXPONENT = 1000


class CalculatorInput(BaseModel):
    expression: str = Field(
        min_length=1,
        max_length=500,
        description="Arithmetic expression using numbers and allowlisted math functions.",
    )


def build_calculator() -> BaseTool:
    async def calculator(expression: str) -> str:
        consume_tool_call()
        try:
            return str(_evaluate(expression))
        except (ArithmeticError, SyntaxError, TypeError, ValueError) as exc:
            return f"Error: {exc}"

    return StructuredTool.from_function(
        coroutine=calculator,
        name="calculator",
        description="Safely evaluate arithmetic used to verify quantities and ratios.",
        args_schema=CalculatorInput,
    )


def _evaluate(expression: str) -> Number:
    if len(expression) > 500:
        raise ValueError("Expression is too long")
    return _evaluate_node(ast.parse(expression, mode="eval").body)


def _evaluate_node(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric literals are allowed")
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATIONS:
        return _UNARY_OPERATIONS[type(node.op)](_evaluate_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATIONS:
        left, right = _evaluate_node(node.left), _evaluate_node(node.right)
        if isinstance(node.op, ast.Pow):
            return _safe_power(left, right)
        return _bounded(_BINARY_OPERATIONS[type(node.op)](left, right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "pow" and not node.keywords and len(node.args) == 2:
            return _safe_power(_evaluate_node(node.args[0]), _evaluate_node(node.args[1]))
        function = _FUNCTIONS.get(node.func.id)
        if function is not None and not node.keywords and len(node.args) <= 20:
            return _bounded(function(*(_evaluate_node(argument) for argument in node.args)))
    raise ValueError("Expression uses an unsupported operation")


def _safe_power(base: Number, exponent: Number) -> Number:
    if abs(exponent) > _MAX_EXPONENT:
        raise ValueError("Exponent is too large")
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
        if max(1, base.bit_length()) * exponent > _MAX_INTEGER_BITS:
            raise ValueError("Result is too large")
    return _bounded(pow(base, exponent))


def _bounded(value: Number) -> Number:
    if isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS:
        raise ValueError("Result is too large")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Result must be finite")
    return value
