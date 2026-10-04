"""Static (AST) validation of uploaded strategy source. Nothing is imported or executed here."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core.utils import sha256_hex
from app.schemas.strategy import StrategyMetadata

SDK_MODULE = "strategy_sdk"
BANNED_CALLS = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help", "dir", "type", "super_"}
# `type` and `getattr` are blocked to prevent reflection tricks; strategies don't need them.
BANNED_NAMES = {"__builtins__", "__loader__", "__spec__", "__file__"}
BANNED_ATTRS = {"__class__", "__bases__", "__base__", "__mro__", "__subclasses__", "__globals__", "__code__",
                "__closure__", "__dict__", "__builtins__", "__import__", "__getattribute__", "__reduce__",
                "__reduce_ex__", "__self__", "__func__", "__module__", "__loader__", "__spec__", "gi_frame",
                "gi_code", "cr_frame", "cr_code", "f_globals", "f_locals", "f_builtins", "f_back", "tb_frame"}
MAX_SOURCE_LINES = 3000


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    sha256: str = ""
    class_name: str | None = None
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "sha256": self.sha256,
                "class_name": self.class_name, "size": self.size}


class _Visitor(ast.NodeVisitor):
    def __init__(self, allowed_imports: set[str]) -> None:
        self.allowed = allowed_imports | {SDK_MODULE, "__future__"}
        self.errors: list[str] = []
        self.imports: set[str] = set()
        self.strategy_classes: list[str] = []
        self.sdk_names: set[str] = set()

    def err(self, node: ast.AST, msg: str) -> None:
        self.errors.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            root = a.name.split(".")[0]
            self.imports.add(root)
            if root not in self.allowed:
                self.err(node, f"import of '{a.name}' is not allowed")
            elif root == SDK_MODULE:
                self.sdk_names.add((a.asname or a.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.err(node, "relative imports are not allowed")
        root = (node.module or "").split(".")[0]
        self.imports.add(root)
        if root not in self.allowed:
            self.err(node, f"import from '{node.module}' is not allowed")
        for a in node.names:
            if a.name == "*":
                self.err(node, "star imports are not allowed")
            if root == SDK_MODULE and a.name == "BaseStrategy":
                self.sdk_names.add(a.asname or a.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
            self.err(node, f"call to '{f.id}' is not allowed")
        if isinstance(f, ast.Attribute) and f.attr in ("system", "popen", "spawn", "fork", "execv", "execve", "run", "Popen", "check_output", "load", "loads_pickle"):
            if f.attr in ("system", "popen", "spawn", "fork", "execv", "execve", "Popen", "check_output"):
                self.err(node, f"call to '.{f.attr}()' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BANNED_ATTRS or (node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in ("__init__", "__name__", "__doc__")):
            self.err(node, f"access to attribute '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES:
            self.err(node, f"use of '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.err(node, "'global' statements are not allowed (keep state on self.state)")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for b in node.bases:
            name = b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else None)
            if name == "BaseStrategy" or (isinstance(b, ast.Name) and b.id in self.sdk_names):
                self.strategy_classes.append(node.name)
        if node.keywords:
            self.err(node, "class keywords/metaclasses are not allowed")
        for d in node.decorator_list:
            self.err(d, "class decorators are not allowed")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in BANNED_ATTRS | {"__builtins__"}:
            self.err(node, f"string literal '{node.value}' is not allowed")


def validate_source(source: str, *, allowed_imports: set[str], allowed_dependencies: set[str],
                    max_bytes: int, max_dependencies: int) -> ValidationReport:
    raw = source.encode("utf-8", errors="replace")
    rep = ValidationReport(ok=False, sha256=sha256_hex(raw), size=len(raw))
    if len(raw) > max_bytes:
        rep.errors.append(f"file too large ({len(raw)} bytes > {max_bytes})")
        return rep
    if "\x00" in source:
        rep.errors.append("NUL bytes are not allowed")
        return rep
    if source.count("\n") > MAX_SOURCE_LINES:
        rep.errors.append(f"too many lines (> {MAX_SOURCE_LINES})")
        return rep
    try:
        tree = ast.parse(source, filename="<strategy>")
    except SyntaxError as exc:
        rep.errors.append(f"syntax error line {exc.lineno}: {exc.msg}")
        return rep
    v = _Visitor(allowed_imports)
    v.visit(tree)
    rep.errors.extend(v.errors)

    # STRATEGY_INFO literal
    info: Any = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STRATEGY_INFO" for t in node.targets):
            try:
                info = ast.literal_eval(node.value)
            except Exception:
                rep.errors.append("STRATEGY_INFO must be a pure literal dict")
            break
    if info is None and not any("STRATEGY_INFO" in e for e in rep.errors):
        rep.errors.append("module-level STRATEGY_INFO = {...} literal dict is required")
    elif isinstance(info, dict):
        try:
            md = StrategyMetadata(**info)
            rep.metadata = md.model_dump()
            if len(md.dependencies) > max_dependencies:
                rep.errors.append(f"too many dependencies ({len(md.dependencies)} > {max_dependencies})")
            bad = [d for d in md.dependencies if d not in allowed_dependencies]
            if bad:
                rep.errors.append(f"dependencies not in the allow-list: {bad}")
        except ValidationError as exc:
            for e in exc.errors():
                rep.errors.append(f"STRATEGY_INFO.{'.'.join(str(x) for x in e['loc'])}: {e['msg']}")
        except TypeError as exc:
            rep.errors.append(f"STRATEGY_INFO invalid: {exc}")
    elif info is not None:
        rep.errors.append("STRATEGY_INFO must be a dict")

    if len(v.strategy_classes) != 1:
        rep.errors.append(f"exactly one class deriving from BaseStrategy is required (found {len(v.strategy_classes)})")
    else:
        rep.class_name = v.strategy_classes[0]
    if SDK_MODULE not in v.imports:
        rep.errors.append("strategy must 'from strategy_sdk import BaseStrategy'")
    rep.ok = not rep.errors
    return rep
