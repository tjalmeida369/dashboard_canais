from __future__ import annotations

import os
import time


_COMPILED_BLOCKS: dict[str, tuple[int, object]] = {}
_PERF_LOG_ENABLED = os.getenv("DASHBOARD_PERF_LOG", "0").strip().lower() in {
    "1", "true", "yes", "on"
}


def execute_block(code: str, namespace: dict[str, object], label: str) -> None:
    namespace.setdefault('__builtins__', __builtins__)
    code_id = hash(code)
    cached = _COMPILED_BLOCKS.get(label)
    if cached is None or cached[0] != code_id:
        compiled = compile(code, filename=f'<dashboard_a9:{label}>', mode='exec')
        _COMPILED_BLOCKS[label] = (code_id, compiled)
    else:
        compiled = cached[1]
    inicio = time.perf_counter()
    exec(compiled, namespace, namespace)
    if _PERF_LOG_ENABLED:
        print(f"[perf] exec.{label}={time.perf_counter() - inicio:.3f}s")
