from __future__ import annotations


_COMPILED_BLOCKS: dict[str, tuple[int, object]] = {}


def execute_block(code: str, namespace: dict[str, object], label: str) -> None:
    namespace.setdefault('__builtins__', __builtins__)
    code_id = hash(code)
    cached = _COMPILED_BLOCKS.get(label)
    if cached is None or cached[0] != code_id:
        compiled = compile(code, filename=f'<dashboard_a9:{label}>', mode='exec')
        _COMPILED_BLOCKS[label] = (code_id, compiled)
    else:
        compiled = cached[1]
    exec(compiled, namespace, namespace)
