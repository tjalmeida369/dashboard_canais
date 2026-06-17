from __future__ import annotations

def execute_block(code: str, namespace: dict[str, object], label: str) -> None:
    namespace.setdefault("__builtins__", __builtins__)
    compiled = compile(code, filename=f"<dashboard_producao:{label}>", mode="exec")
    exec(compiled, namespace, namespace)
