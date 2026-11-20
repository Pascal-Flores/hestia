import ast, json, math, operator, sys

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCTIONS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
             "tan": math.tan, "log": math.log, "ln": math.log,
             "log10": math.log10, "abs": abs, "round": round}
CONSTANTS = {"pi": math.pi, "e": math.e}

def compute(node, depth=0):
    if depth > 18:
        raise ValueError("Expression trop complexe")
    if isinstance(node, ast.Expression):
        return compute(node.body, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        if not math.isfinite(node.value) or abs(node.value) > 1e12:
            raise ValueError("Nombre trop grand")
        return node.value
    if isinstance(node, ast.Name) and node.id in CONSTANTS:
        return CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = compute(node.left, depth + 1), compute(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e9):
            raise ValueError("Puissance excessive")
        result = OPS[type(node.op)](left, right)
        if isinstance(result, complex) or not math.isfinite(result) or abs(result) > 1e15:
            raise ValueError("Résultat hors limites")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](compute(node.operand, depth + 1))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
        if node.keywords or len(node.args) != 1:
            raise ValueError("Une fonction attend un seul argument")
        return FUNCTIONS[node.func.id](compute(node.args[0], depth + 1))
    raise ValueError("Opération interdite")

def run(args):
    expression = args["parameters"]["expression"].strip().replace("^", "**")
    if len(expression) > 160:
        raise ValueError("Expression trop longue")
    try:
        value = compute(ast.parse(expression, mode="eval"))
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return {"success": True, "expression": expression, "result": value}
    except (ValueError, OverflowError, ZeroDivisionError, SyntaxError) as error:
        return {"success": False, "expression": expression, "error": str(error)}

try:
    print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False))
except (KeyError, TypeError, ValueError) as e:
    print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
