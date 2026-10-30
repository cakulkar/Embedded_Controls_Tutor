"""Editable model document with undo/redo. Qt-free so every editor action is unit-testable.

The document *is* the ECSim model JSON (plus a "pos" [x, y] per block for layout). The
canvas is only a view of it. Every mutation goes through a command so it can be undone.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ecsim.model.blocks import LIB, ModelError, block_type, validate_params
from ecsim.model.core import FORMAT, Compiled, compile_model, load_model

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PALETTE: dict[str, list[str]] = {
    "Sources": ["Inport", "Constant", "Ground"],
    "Sinks": ["Outport", "Terminator"],
    "Math Operations": ["Gain", "Sum", "Product", "Abs", "MinMax"],
    "Discontinuities": ["Saturation", "RateLimiter"],
    "Discrete": ["UnitDelay", "DiscreteIntegrator", "DiscreteTransferFcn"],
    "Logic and Routing": ["RelationalOperator", "LogicalOperator", "Switch"],
    "Lookup Tables": ["Lookup1D"],
    "Ports & Subsystems": ["Subsystem"],
}

# Parameters that start a new block in a sensible state (beyond library defaults).
_NEW_PARAMS: dict[str, dict] = {
    "Constant": {"value": 1.0},
    "Lookup1D": {"breakpoints": [0.0, 1.0], "table": [0.0, 1.0]},
    "Subsystem": {"model": None},   # filled with an empty inner model
}


def empty_model(name: str = "untitled", step: float = 0.01) -> dict:
    return {"format": FORMAT, "version": 1, "name": name,
            "solver": {"type": "FixedStepDiscrete", "step": step}, "blocks": [], "lines": []}


def _empty_subsystem() -> dict:
    m = empty_model("subsystem")
    m["blocks"] = [{"id": "In1", "type": "Inport", "params": {"port": 1}, "pos": [40, 80]},
                   {"id": "Out1", "type": "Outport", "params": {"port": 1}, "pos": [320, 80]}]
    m["lines"] = [{"src": "In1:1", "dst": "Out1:1"}]
    return m


def port_counts(b: dict) -> tuple[int, int]:
    if b["type"] == "Subsystem":
        inner = b["params"]["model"]
        return (sum(1 for x in inner["blocks"] if x["type"] == "Inport"),
                sum(1 for x in inner["blocks"] if x["type"] == "Outport"))
    bt = block_type(b["type"])
    p = dict(bt.defaults or {})
    p.update(b.get("params", {}))
    return bt.n_in(p), bt.n_out(p)


def _prune(m: dict, bid: str) -> None:
    """Drop lines attached to ports that no longer exist on block `bid`."""
    b = next(x for x in m["blocks"] if x["id"] == bid)
    n_in, n_out = port_counts(b)

    def ok(ln):
        sb, sp = ln["src"].rsplit(":", 1)
        db, dp = ln["dst"].rsplit(":", 1)
        return not ((sb == bid and int(sp) > n_out) or (db == bid and int(dp) > n_in))
    m["lines"] = [ln for ln in m["lines"] if ok(ln)]


def coerce_param(type_name: str, key: str, text: str) -> Any:
    """Turn editor text into a parameter value of the right kind, or raise ModelError."""
    defaults = dict(LIB[type_name].defaults or {})
    defaults.update(_NEW_PARAMS.get(type_name, {}))
    sample = defaults.get(key)
    t = text.strip()
    if isinstance(sample, list) or t.startswith("["):
        try:
            v = json.loads(t)
        except json.JSONDecodeError:
            raise ModelError(f"{key}: enter a list like [0, 1, 2]") from None
        if not isinstance(v, list) or not all(isinstance(x, (int, float)) for x in v):
            raise ModelError(f"{key}: list of numbers expected")
        return [float(x) for x in v]
    if isinstance(sample, str):
        return t
    if key in ("port", "inputs"):
        try:
            return int(t)
        except ValueError:
            raise ModelError(f"{key}: whole number expected") from None
    try:
        return float(t)
    except ValueError:
        raise ModelError(f"{key}: number expected (got {t!r})") from None


@dataclass
class _Cmd:
    label: str
    do: Callable[[], None]
    undo: Callable[[], None]


@dataclass
class ModelDocument:
    model: dict = field(default_factory=empty_model)
    path: str | None = None
    dirty: bool = False
    _undo: list[_Cmd] = field(default_factory=list)
    _redo: list[_Cmd] = field(default_factory=list)
    listeners: list[Callable[[], None]] = field(default_factory=list)

    # ---- I/O -------------------------------------------------------------------
    @classmethod
    def from_json(cls, text: str, path: str | None = None) -> "ModelDocument":
        m = load_model(text)
        for i, b in enumerate(m["blocks"]):
            b.setdefault("pos", [60 + 160 * (i % 5), 60 + 110 * (i // 5)])
        return cls(model=m, path=path)

    def to_json(self) -> str:
        return json.dumps(self.model, indent=2)

    def compile(self) -> Compiled:
        return compile_model(self.model)

    # ---- queries ---------------------------------------------------------------
    def block(self, bid: str) -> dict:
        for b in self.model["blocks"]:
            if b["id"] == bid:
                return b
        raise ModelError(f"no block '{bid}'")

    def ports(self, bid: str) -> tuple[int, int]:
        return port_counts(self.block(bid))

    def unique_id(self, base: str) -> str:
        ids = {b["id"] for b in self.model["blocks"]}
        if base not in ids:
            return base
        k = 1
        while f"{base}{k}" in ids:
            k += 1
        return f"{base}{k}"

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # ---- command machinery -------------------------------------------------------
    def _snapshot_cmd(self, label: str, mutate: Callable[[dict], None]) -> None:
        before = copy.deepcopy(self.model)
        after = copy.deepcopy(self.model)
        mutate(after)

        def do():
            self.model = copy.deepcopy(after)

        def undo():
            self.model = copy.deepcopy(before)
        cmd = _Cmd(label, do, undo)
        cmd.do()
        self._undo.append(cmd)
        self._redo.clear()
        self._changed()

    def undo(self) -> str | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        self._changed()
        return cmd.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.do()
        self._undo.append(cmd)
        self._changed()
        return cmd.label

    def _changed(self) -> None:
        self.dirty = True
        for fn in list(self.listeners):
            fn()

    # ---- edits -------------------------------------------------------------------
    def add_block(self, type_name: str, pos: tuple[float, float] = (100, 100), bid: str | None = None,
                  params: dict | None = None) -> str:
        bt = block_type(type_name)
        new_id = bid or self.unique_id(type_name)
        if not _ID.match(new_id):
            raise ModelError(f"invalid block name {new_id!r}: letters, digits and _ only")
        if any(b["id"] == new_id for b in self.model["blocks"]):
            raise ModelError(f"a block named '{new_id}' already exists")
        p = copy.deepcopy(_NEW_PARAMS.get(type_name, {}))
        if type_name == "Subsystem":
            p["model"] = _empty_subsystem()
        if type_name in ("Inport", "Outport"):
            n = sum(1 for b in self.model["blocks"] if b["type"] == type_name)
            p["port"] = n + 1
            p["name"] = new_id
        p.update(params or {})
        merged = dict(bt.defaults or {})
        merged.update(p)
        if type_name != "Subsystem":
            validate_params(type_name, merged)
        blk = {"id": new_id, "type": type_name, "params": p, "pos": [float(pos[0]), float(pos[1])]}
        self._snapshot_cmd(f"Add {type_name}", lambda m: m["blocks"].append(blk))
        return new_id

    def remove_block(self, bid: str) -> None:
        self.block(bid)

        def mut(m):
            m["blocks"] = [b for b in m["blocks"] if b["id"] != bid]
            m["lines"] = [ln for ln in m["lines"]
                          if ln["src"].rsplit(":", 1)[0] != bid and ln["dst"].rsplit(":", 1)[0] != bid]
            for kind in ("Inport", "Outport"):     # keep port numbers contiguous, like Simulink
                ports = sorted((b for b in m["blocks"] if b["type"] == kind), key=lambda b: b["params"]["port"])
                for k, b in enumerate(ports, 1):
                    b["params"]["port"] = k
        self._snapshot_cmd(f"Delete {bid}", mut)

    def rename_block(self, bid: str, new_id: str) -> None:
        if not _ID.match(new_id):
            raise ModelError(f"invalid block name {new_id!r}: letters, digits and _ only")
        if new_id != bid and any(b["id"] == new_id for b in self.model["blocks"]):
            raise ModelError(f"a block named '{new_id}' already exists")
        self.block(bid)

        def mut(m):
            for b in m["blocks"]:
                if b["id"] == bid:
                    b["id"] = new_id
                    if b["type"] in ("Inport", "Outport") and b["params"].get("name", bid) == bid:
                        b["params"]["name"] = new_id
            for ln in m["lines"]:
                for end in ("src", "dst"):
                    blk, port = ln[end].rsplit(":", 1)
                    if blk == bid:
                        ln[end] = f"{new_id}:{port}"
        self._snapshot_cmd(f"Rename {bid}", mut)

    def move_block(self, bid: str, x: float, y: float) -> None:
        self.block(bid)

        def mut(m):
            for b in m["blocks"]:
                if b["id"] == bid:
                    b["pos"] = [float(x), float(y)]
        self._snapshot_cmd(f"Move {bid}", mut)

    def set_param(self, bid: str, key: str, value: Any) -> None:
        b = self.block(bid)
        bt = block_type(b["type"])
        merged = dict(bt.defaults or {})
        merged.update(b.get("params", {}))
        merged[key] = value
        try:
            validate_params(b["type"], merged)
        except KeyError as exc:
            raise ModelError(f"missing parameter {exc}") from None
        if key == "port":
            same = [x for x in self.model["blocks"] if x["type"] == b["type"]]
            if not 1 <= int(value) <= len(same):
                raise ModelError(f"port must be between 1 and {len(same)}")

        def mut(m):
            for x in m["blocks"]:
                if x["id"] == bid:
                    if key == "port":       # swap with the block currently holding that port number
                        for y in m["blocks"]:
                            if y["type"] == x["type"] and y["params"].get("port") == int(value):
                                y["params"]["port"] = x["params"].get("port", 1)
                    x.setdefault("params", {})[key] = value
            _prune(m, bid)     # fewer inputs (e.g. Sum signs) must not leave dangling lines
        self._snapshot_cmd(f"Set {bid}.{key}", mut)

    def set_sample_time(self, bid: str, ts: float) -> None:
        self.block(bid)
        if not (ts == -1 or ts > 0):
            raise ModelError("sample time must be -1 (inherited) or > 0")

        def mut(m):
            for x in m["blocks"]:
                if x["id"] == bid:
                    x["sample_time"] = float(ts)
        self._snapshot_cmd(f"Sample time {bid}", mut)

    def set_step(self, step: float) -> None:
        if not step > 0:
            raise ModelError("fixed step must be > 0")
        self._snapshot_cmd("Solver step", lambda m: m.setdefault("solver", {}).update(step=float(step),
                                                                                       type="FixedStepDiscrete"))

    def connect(self, src: str, dst: str) -> None:
        sb, sp = src.rsplit(":", 1)
        db, dp = dst.rsplit(":", 1)
        _, n_out = self.ports(sb)
        n_in, _ = self.ports(db)
        if not 1 <= int(sp) <= n_out:
            raise ModelError(f"'{sb}' has no output port {sp}")
        if not 1 <= int(dp) <= n_in:
            raise ModelError(f"'{db}' has no input port {dp}")
        if any(ln["dst"] == dst for ln in self.model["lines"]):
            raise ModelError(f"input {dst} is already connected; delete that line first")
        self._snapshot_cmd(f"Connect {src} → {dst}", lambda m: m["lines"].append({"src": src, "dst": dst}))

    def disconnect(self, dst: str) -> None:
        if not any(ln["dst"] == dst for ln in self.model["lines"]):
            raise ModelError(f"nothing connected to {dst}")
        self._snapshot_cmd(f"Disconnect {dst}",
                           lambda m: m.__setitem__("lines", [ln for ln in m["lines"] if ln["dst"] != dst]))

    def set_subsystem_model(self, bid: str, inner: dict) -> None:
        """Replace a subsystem's contents (used when editing inside a subsystem)."""
        b = self.block(bid)
        if b["type"] != "Subsystem":
            raise ModelError(f"'{bid}' is not a subsystem")
        load_model(inner)

        def mut(m):
            for x in m["blocks"]:
                if x["id"] == bid:
                    x["params"]["model"] = copy.deepcopy(inner)
            _prune(m, bid)
        self._snapshot_cmd(f"Edit {bid}", mut)

    def group_into_subsystem(self, ids: list[str], name: str = "Subsystem") -> str:
        """Create a subsystem from selected blocks; boundary lines become subsystem ports."""
        sel = set(ids)
        if not sel:
            raise ModelError("select at least one block")
        for b in sel:
            if self.block(b)["type"] in ("Inport", "Outport"):
                raise ModelError("model Inport/Outport blocks cannot be grouped")
        sid = self.unique_id(name)

        def mut(m):
            inner = empty_model(sid, m.get("solver", {}).get("step", 0.01))
            moved = [b for b in m["blocks"] if b["id"] in sel]
            inner["blocks"] = copy.deepcopy(moved)
            in_map: dict[str, int] = {}
            out_map: dict[str, int] = {}
            new_lines = []
            for ln in m["lines"]:
                s_in = ln["src"].rsplit(":", 1)[0] in sel
                d_in = ln["dst"].rsplit(":", 1)[0] in sel
                if s_in and d_in:
                    inner["lines"].append(dict(ln))
                elif d_in:                      # enters the group
                    first = ln["src"] not in in_map
                    k = in_map.setdefault(ln["src"], len(in_map) + 1)
                    inner["lines"].append({"src": f"In{k}:1", "dst": ln["dst"]})
                    if first:                   # one subsystem port per external source
                        new_lines.append({"src": ln["src"], "dst": f"{sid}:{k}"})
                elif s_in:                      # leaves the group
                    k = out_map.setdefault(ln["src"], len(out_map) + 1)
                    new_lines.append({"src": f"{sid}:{k}", "dst": ln["dst"]})
                else:
                    new_lines.append(ln)
            for src, k in in_map.items():
                inner["blocks"].append({"id": f"In{k}", "type": "Inport", "params": {"port": k}, "pos": [20, 60 * k]})
            for src, k in out_map.items():
                inner["blocks"].append({"id": f"Out{k}", "type": "Outport", "params": {"port": k}, "pos": [600, 60 * k]})
                inner["lines"].append({"src": src, "dst": f"Out{k}:1"})
            xs = [b["pos"][0] for b in moved]
            ys = [b["pos"][1] for b in moved]
            m["blocks"] = [b for b in m["blocks"] if b["id"] not in sel]
            m["blocks"].append({"id": sid, "type": "Subsystem", "params": {"model": inner},
                                "pos": [sum(xs) / len(xs), sum(ys) / len(ys)]})
            m["lines"] = new_lines
        self._snapshot_cmd(f"Create subsystem {sid}", mut)
        return sid
