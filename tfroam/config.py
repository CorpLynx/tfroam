"""Read Terraform ``.tf`` files for file/line positions (replaces terraform-config-inspect).

This is a port of ``tfconfig.LoadModule`` (v0.0.0-20210511202847-ad33d83d7650): it
extracts variables, outputs, required providers, provider configs, resources, data
sources and module calls, with the file and line each block starts on.

It is built on the python-hcl2 parse tree. Only constant expressions can be
evaluated, as in the Go version (which evaluates with no variables or functions);
anything else in a place the Go loader decodes becomes an error diagnostic and the
module is treated as not loadable.

Known gaps against the Go loader: ``*.tf.json`` files, the legacy-HCL fallback
parser, and constant arithmetic (``1 + 2``) are not supported.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2
from lark import Token, Tree


class NotConstant(Exception):
    """Expression cannot be evaluated without variables or functions."""


@dataclass
class Diagnostic:
    severity: str
    summary: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "summary": self.summary}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class SourcePos:
    filename: str
    line: int

    def to_json(self) -> dict[str, Any]:
        return {"filename": self.filename, "line": self.line}


@dataclass
class ProviderRef:
    name: str
    alias: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.alias:
            out["alias"] = self.alias
        return out


@dataclass
class ProviderRequirement:
    source: str = ""
    version_constraints: list[str] = field(default_factory=list)
    aliases: list[ProviderRef] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.source:
            out["source"] = self.source
        if self.version_constraints:
            out["version_constraints"] = self.version_constraints
        if self.aliases:
            out["aliases"] = [a.to_json() for a in self.aliases]
        return out


@dataclass
class ProviderConfig:
    name: str
    alias: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.alias:
            out["alias"] = self.alias
        return out


@dataclass
class Variable:
    name: str
    pos: SourcePos
    type: str = ""
    description: str = ""
    default: Any = None
    required: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.type:
            out["type"] = self.type
        if self.description:
            out["description"] = self.description
        out["default"] = self.default
        out["required"] = self.required
        out["pos"] = self.pos.to_json()
        return out


@dataclass
class Output:
    name: str
    pos: SourcePos
    description: str = ""
    sensitive: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.description:
            out["description"] = self.description
        if self.sensitive:
            out["sensitive"] = True
        out["pos"] = self.pos.to_json()
        return out


@dataclass
class Resource:
    mode: str  # "managed" | "data"
    type: str
    name: str
    pos: SourcePos
    provider: ProviderRef = field(default_factory=lambda: ProviderRef(""))

    def map_key(self) -> str:
        if self.mode == "managed":
            return f"{self.type}.{self.name}"
        return f"data.{self.type}.{self.name}"

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "type": self.type,
            "name": self.name,
            "provider": self.provider.to_json(),
            "pos": self.pos.to_json(),
        }


@dataclass
class ModuleCall:
    name: str
    pos: SourcePos
    source: str = ""
    version: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "source": self.source}
        if self.version:
            out["version"] = self.version
        out["pos"] = self.pos.to_json()
        return out


@dataclass
class Module:
    path: str
    variables: dict[str, Variable] = field(default_factory=dict)
    outputs: dict[str, Output] = field(default_factory=dict)
    required_core: list[str] = field(default_factory=list)
    required_providers: dict[str, ProviderRequirement] = field(default_factory=dict)
    provider_configs: dict[str, ProviderConfig] = field(default_factory=dict)
    managed_resources: dict[str, Resource] = field(default_factory=dict)
    data_resources: dict[str, Resource] = field(default_factory=dict)
    module_calls: dict[str, ModuleCall] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "variables": {k: v.to_json() for k, v in self.variables.items()},
            "outputs": {k: v.to_json() for k, v in self.outputs.items()},
        }
        if self.required_core:
            out["required_core"] = self.required_core
        out["required_providers"] = {k: v.to_json() for k, v in self.required_providers.items()}
        if self.provider_configs:
            out["provider_configs"] = {k: v.to_json() for k, v in self.provider_configs.items()}
        out["managed_resources"] = {k: v.to_json() for k, v in self.managed_resources.items()}
        out["data_resources"] = {k: v.to_json() for k, v in self.data_resources.items()}
        out["module_calls"] = {k: v.to_json() for k, v in self.module_calls.items()}
        if self.diagnostics:
            out["diagnostics"] = [d.to_json() for d in self.diagnostics]
        return out


# ---------------------------------------------------------------------------
# Expression helpers over the python-hcl2 (lark) tree
# ---------------------------------------------------------------------------

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
_ESCAPE_RE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|(.))", re.DOTALL)


def _unescape(raw: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        if m.group(2):
            return chr(int(m.group(2), 16))
        ch = m.group(3)
        if ch in _ESCAPES:
            return _ESCAPES[ch]
        raise NotConstant(f"invalid escape \\{ch}")

    return _ESCAPE_RE.sub(repl, raw).replace("$${", "${").replace("%%{", "%{")


def _kids(node: Tree[Any]) -> list[Any]:
    """Children without newline/comment filler."""
    return [c for c in node.children if not (isinstance(c, Tree) and c.data == "new_line_or_comment")]


def _unwrap(node: Any) -> Any:
    """Strip ``expr_term`` wrappers and parentheses."""
    while isinstance(node, Tree) and node.data == "expr_term":
        kids = [c for c in node.children if not isinstance(c, Token)]
        if len(kids) == 1:
            node = kids[0]
        else:
            break
    return node


def _heredoc(text: str, trim: bool) -> str:
    lines = text.split("\n")
    body = lines[1:-2] if lines[-1] == "" else lines[1:-1]
    if trim:
        indents = [len(ln) - len(ln.lstrip(" \t")) for ln in body if ln.strip()]
        cut = min(indents) if indents else 0
        body = [ln[cut:] for ln in body]
    out = "\n".join(body) + "\n" if body else ""
    if re.search(r"(?<![$%])[$%]\{", out):
        raise NotConstant("template")
    return out.replace("$${", "${").replace("%%{", "%{")


def eval_const(node: Any) -> Any:
    """Evaluate a constant expression, as ``hcl.Expression.Value(nil)`` would."""
    node = _unwrap(node)
    if isinstance(node, Token):
        raise NotConstant(str(node))
    kind = node.data
    kids = _kids(node)
    if kind == "string":
        parts: list[str] = []
        for part in kids[1:-1]:
            inner = part.children[0] if isinstance(part, Tree) and part.children else part
            if isinstance(inner, Tree):  # interpolation / directive
                raise NotConstant("interpolation")
            parts.append(str(inner))
        return _unescape("".join(parts))
    if kind in ("heredoc_template", "heredoc_template_trim"):
        return _heredoc(str(kids[0]), kind.endswith("trim"))
    if kind == "int_lit":
        return int(str(kids[0]))
    if kind == "float_lit":
        return float(str(kids[0]))
    if kind == "literal_value":
        word = str(kids[0])
        return {"true": True, "false": False, "null": None}[word]
    if kind == "unary_op" and str(kids[0]) == "-":
        val = eval_const(kids[1])
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return -val
        raise NotConstant("unary")
    if kind == "tuple":
        return [eval_const(c) for c in kids if not isinstance(c, Token)]
    if kind == "object":
        return {key: eval_const(val) for key, val in _object_items(node)}
    raise NotConstant(kind)


def _object_items(node: Tree[Any]) -> list[tuple[str, Tree[Any]]]:
    items: list[tuple[str, Tree[Any]]] = []
    for elem in _kids(node):
        if not (isinstance(elem, Tree) and elem.data == "object_elem"):
            continue
        key_node, val_node = [c for c in _kids(elem) if not isinstance(c, Token)][:2]
        key = _unwrap(key_node.children[0] if key_node.data == "object_elem_key" else key_node)
        if isinstance(key, Tree) and key.data == "identifier":
            name = str(key.children[0])
        else:
            value = eval_const(key)
            if not isinstance(value, str):
                raise NotConstant("object key")
            name = value
        items.append((name, val_node))
    return items


def _as_string(node: Any) -> str:
    """``gohcl.DecodeExpression`` into a Go string (numbers and bools convert)."""
    value = eval_const(node)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise NotConstant("not a string")


def _as_bool(node: Any) -> bool:
    value = eval_const(node)
    if isinstance(value, bool):
        return value
    if value in ("true", "false"):
        return bool(value == "true")
    raise NotConstant("not a bool")


def _traversal(node: Any) -> list[str] | None:
    """Absolute traversal ``a.b`` -> ["a", "b"]; None when not a plain traversal."""
    node = _unwrap(node)
    if not isinstance(node, Tree):
        return None
    if node.data == "identifier":
        return [str(node.children[0])]
    if node.data == "get_attr_expr_term":
        head = _traversal(node.children[0])
        step = node.children[1]
        if head is None or not (isinstance(step, Tree) and step.data == "get_attr"):
            return None
        return [*head, str(step.children[-1].children[0])]
    return None


def _source_slice(source: str, node: Tree[Any]) -> str:
    return source[node.meta.start_pos : node.meta.end_pos]


def _provider_ref_from_traversal(parts: list[str]) -> ProviderRef:
    return ProviderRef(parts[0], parts[1] if len(parts) > 1 else "")


# ---------------------------------------------------------------------------
# Block decoding
# ---------------------------------------------------------------------------

_LABEL_COUNTS = {"terraform": 0, "variable": 1, "output": 1, "provider": 1, "resource": 2, "data": 2, "module": 1}


@dataclass
class _Block:
    type: str
    labels: list[str]
    line: int
    attrs: dict[str, Tree[Any]]
    blocks: list[_Block]


def _decode_block(node: Tree[Any]) -> _Block:
    kids = _kids(node)
    btype = str(kids[0].children[0])
    labels: list[str] = []
    body: Tree[Any] | None = None
    for child in kids[1:]:
        if isinstance(child, Token):
            continue
        if child.data == "body":
            body = child
        elif child.data == "identifier":
            labels.append(str(child.children[0]))
        else:
            labels.append(_as_string(child))
    attrs: dict[str, Tree[Any]] = {}
    blocks: list[_Block] = []
    if body is not None:
        for item in _kids(body):
            if not isinstance(item, Tree):
                continue
            if item.data == "attribute":
                parts = _kids(item)
                attrs[str(parts[0].children[0])] = parts[-1]
            elif item.data == "block":
                blocks.append(_decode_block(item))
    return _Block(btype, labels, node.meta.line, attrs, blocks)


class _Loader:
    def __init__(self, module: Module) -> None:
        self.mod = module
        self._source = ""

    def set_source(self, text: str) -> None:
        self._source = text  # raw text, used for unevaluated `type` expressions

    def error(self, summary: str, detail: str = "") -> None:
        self.mod.diagnostics.append(Diagnostic("error", summary, detail))

    def string_attr(self, block: _Block, name: str) -> tuple[bool, str]:
        expr = block.attrs.get(name)
        if expr is None:
            return False, ""
        try:
            return True, _as_string(expr)
        except NotConstant:
            self.error("Unsuitable value type", f"Unsuitable value for {name}: string required")
            return True, ""

    def load_file(self, source: str, filename: str) -> None:
        try:
            tree = hcl2.parses_to_tree(source)
        except Exception as exc:  # lark raises several exception types
            self.error("Failed to parse configuration", f"{filename}: {exc}")
            return
        for item in _kids(tree.children[0]) if tree.children else []:
            if not (isinstance(item, Tree) and item.data == "block"):
                continue
            block = _decode_block(item)
            want = _LABEL_COUNTS.get(block.type)
            if want is None:
                continue  # block types outside the root schema are ignored
            if len(block.labels) != want:
                self.error(f"Wrong label count for {block.type}", f"{filename}:{block.line}")
                continue
            pos = SourcePos(filename, block.line)
            getattr(self, f"_{block.type}")(block, pos)

    def _terraform(self, block: _Block, pos: SourcePos) -> None:
        if "required_version" in block.attrs:
            try:
                self.mod.required_core.append(_as_string(block.attrs["required_version"]))
            except NotConstant:
                self.error("Unsuitable value type", "required_version must be a constant string")
        for inner in block.blocks:
            if inner.type == "required_providers":
                self._required_providers(inner)

    def _required_providers(self, block: _Block) -> None:
        reqs: dict[str, ProviderRequirement] = {}
        for name, expr in block.attrs.items():
            try:
                value = eval_const(expr)
            except NotConstant:
                value = _NOT_PRIMITIVE
            if isinstance(value, (str, int, float, bool)):
                reqs[name] = ProviderRequirement(version_constraints=[_as_string(expr)])
                continue
            node = _unwrap(expr)
            if not (isinstance(node, Tree) and node.data == "object"):
                self.error(
                    "Invalid required_providers object", "Required providers entries must be strings or objects."
                )
                continue
            pr = ProviderRequirement()
            for key, val in _object_items(node):
                if key == "version":
                    try:
                        version = eval_const(val)
                    except NotConstant:
                        version = _NOT_PRIMITIVE
                    if not isinstance(version, str) and version is not None:
                        self.error("Unsuitable value type", "Unsuitable value: string required")
                        continue
                    if version is not None:
                        pr.version_constraints.append(version)
                elif key == "source":
                    try:
                        src = eval_const(val)
                    except NotConstant:
                        src = _NOT_PRIMITIVE
                    if not isinstance(src, str) and src is not None:
                        self.error("Unsuitable value type", "Unsuitable value: string required")
                        continue
                    if src is not None:
                        pr.source = src
                elif key == "configuration_aliases":
                    self._aliases(name, val, pr)
                reqs[name] = pr
        for name, req in reqs.items():
            existing = self.mod.required_providers.get(name)
            if existing is None:
                self.mod.required_providers[name] = req
                continue
            if req.source:
                if existing.source and existing.source != req.source:
                    self.error(
                        "Multiple provider source attributes",
                        f'Found multiple source attributes for provider {name}: "{existing.source}", "{req.source}"',
                    )
                else:
                    existing.source = req.source
            existing.version_constraints.extend(req.version_constraints)

    def _aliases(self, local_name: str, expr: Tree[Any], pr: ProviderRequirement) -> None:
        node = _unwrap(expr)
        if not (isinstance(node, Tree) and node.data == "tuple"):
            self.error("Invalid configuration_aliases value", "expected a list")
            return
        for item in (c for c in _kids(node) if not isinstance(c, Token)):
            parts = _traversal(item)
            if parts is None or len(parts) > 2:
                self.error("Invalid configuration_aliases value", "Configuration aliases can only contain references")
                continue
            ref = _provider_ref_from_traversal(parts)
            if ref.name != local_name:
                self.error(
                    "Invalid configuration_aliases value",
                    f'Configuration aliases must be prefixed with the provider name. Expected "{local_name}", '
                    f'but found "{ref.name}".',
                )
                continue
            pr.aliases.append(ref)

    def _variable(self, block: _Block, pos: SourcePos) -> None:
        name = block.labels[0]
        var = Variable(name=name, pos=pos)
        self.mod.variables[name] = var
        if "type" in block.attrs:
            expr = block.attrs["type"]
            try:
                var.type = _as_string(expr)
            except NotConstant:
                var.type = _source_slice(self._source, _unwrap(expr))
        _, var.description = self.string_attr(block, "description")
        if "default" in block.attrs:
            try:
                var.default = eval_const(block.attrs["default"])
            except NotConstant:
                self.error("Variables not allowed", "Variables may not be used here.")
        else:
            var.required = True

    def _output(self, block: _Block, pos: SourcePos) -> None:
        out = Output(name=block.labels[0], pos=pos)
        self.mod.outputs[out.name] = out
        _, out.description = self.string_attr(block, "description")
        if "sensitive" in block.attrs:
            try:
                out.sensitive = _as_bool(block.attrs["sensitive"])
            except NotConstant:
                self.error("Unsuitable value type", "Unsuitable value: bool required")

    def _provider(self, block: _Block, pos: SourcePos) -> None:
        name = block.labels[0]
        req = self.mod.required_providers.setdefault(name, ProviderRequirement())
        if "version" in block.attrs:
            try:
                req.version_constraints.append(_as_string(block.attrs["version"]))
            except NotConstant:
                self.error("Unsuitable value type", "Unsuitable value: string required")
        key = name
        alias = ""
        if "alias" in block.attrs:
            try:
                alias = _as_string(block.attrs["alias"])
            except NotConstant:
                self.error("Unsuitable value type", "Unsuitable value: string required")
            else:
                if alias:
                    key = f"{name}.{alias}"
        self.mod.provider_configs[key] = ProviderConfig(name=name, alias=alias)

    def _resource(self, block: _Block, pos: SourcePos) -> None:
        res = Resource(mode="managed", type=block.labels[0], name=block.labels[1], pos=pos)
        self._add_resource(block, res, self.mod.managed_resources)

    def _data(self, block: _Block, pos: SourcePos) -> None:
        res = Resource(mode="data", type=block.labels[0], name=block.labels[1], pos=pos)
        self._add_resource(block, res, self.mod.data_resources)

    def _add_resource(self, block: _Block, res: Resource, table: dict[str, Resource]) -> None:
        table[res.map_key()] = res
        expr = block.attrs.get("provider")
        if expr is None:
            res.provider = ProviderRef(res.type.split("_", 1)[0])
            return
        parts = _traversal(expr)
        if parts is None:
            try:
                text = _as_string(expr)
                parts = [p for p in text.split(".") if p]
            except NotConstant:
                parts = None
        if parts:
            res.provider = _provider_ref_from_traversal(parts)
        else:
            self.error(
                "Invalid provider reference",
                'Provider argument requires a provider name followed by an optional alias, like "aws.foo".',
            )

    def _module(self, block: _Block, pos: SourcePos) -> None:
        name = block.labels[0]
        call = ModuleCall(name=name, pos=pos)
        previous = self.mod.module_calls.get(name)
        self.mod.module_calls[name] = call
        _, call.source = self.string_attr(block, "source")
        if not call.source and previous is not None:
            call.source = previous.source
        _, call.version = self.string_attr(block, "version")


_NOT_PRIMITIVE = object()


def _file_ext(name: str) -> str:
    if name.endswith(".tf"):
        return ".tf"
    if name.endswith(".tf.json"):
        return ".tf.json"
    return ""


def _is_ignored(name: str) -> bool:
    return name.startswith(".") or name.endswith("~") or (name.startswith("#") and name.endswith("#"))


def to_posix(path: str, sep: str = os.sep) -> str:
    """Forward slashes in the JSON on every platform (the frontend expects them; spec: Windows paths)."""
    return path.replace("\\", "/") if sep == "\\" else path


def load_module(directory: str) -> Module:
    """Port of ``tfconfig.LoadModule``: never raises; check ``has_errors()``."""
    mod = Module(path=to_posix(directory))
    loader = _Loader(mod)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        loader.error(
            "Failed to read module directory", f"Module directory {directory} does not exist or cannot be read."
        )
        _finish(mod)
        return mod

    primary: list[str] = []
    override: list[str] = []
    for name in names:
        full = os.path.join(directory, name)
        ext = _file_ext(name)
        if os.path.isdir(full) or not ext or _is_ignored(name):
            continue
        base = name[: -len(ext)]
        (override if base == "override" or base.endswith("_override") else primary).append(name)

    for name in [*primary, *override]:
        # filepath.Join cleans the path; positions are relative like the Go output.
        shown = Path(os.path.normpath(os.path.join(directory, name))).as_posix()
        if name.endswith(".tf.json"):
            loader.error("Unsupported file", f"{shown}: JSON configuration is not supported by this port")
            continue
        try:
            text = Path(directory, name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            loader.error("Failed to read file", f'The configuration file "{shown}" could not be read.')
            continue
        loader.set_source(text)
        loader.load_file(text, shown)

    _finish(mod)
    return mod


def _finish(mod: Module) -> None:
    for res in [*mod.managed_resources.values(), *mod.data_resources.values()]:
        mod.required_providers.setdefault(res.provider.name, ProviderRequirement())
