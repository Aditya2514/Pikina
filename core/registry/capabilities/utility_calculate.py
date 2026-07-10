import math

def run(params: dict) -> dict:
    expr = params.get("expr", "").strip()
    if not expr:
        return {"status": "error", "reason": "Missing math expression"}
    
    # Safe evaluation of basic math expressions using a restricted environment
    allowed_names = {
        k: v for k, v in math.__dict__.items() if not k.startswith("__")
    }
    allowed_names.update({
        "abs": abs, "round": round, "max": max, "min": min
    })
    
    try:
        # Sanitize input: replace x/X division symbols with standard asterisks
        sanitized = expr.replace("x", "*").replace("X", "*")
        
        # Only evaluate if it contains allowed characters to prevent arbitrary code execution
        # Allow numbers, math operators (+ - * / . ( ) ), spaces, and functions
        clean_expr = "".join(c for c in sanitized if c.isalnum() or c in " +-*/.()")
        
        result = eval(clean_expr, {"__builtins__": None}, allowed_names)
        return {
            "status": "ok",
            "tool": "utility.calculate",
            "expr": expr,
            "result": float(result),
            "message": f"Calculation result: {result}"
        }
    except Exception as e:
        return {"status": "error", "reason": f"Failed to evaluate expression: {e}"}
