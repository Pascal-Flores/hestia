"""Example safe arithmetic tool."""


def run(parameters: dict, context: dict) -> dict:
    a, b = parameters["a"], parameters["b"]
    return {"success": True, "a": a, "b": b, "result": a + b}
