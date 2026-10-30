"""Model format, compiler and fixed-step discrete simulator.

Model JSON (version 1)::

    {
      "format": "ecsim-model", "version": 1, "name": "speed_ctrl",
      "solver": {"type": "FixedStepDiscrete", "step": 0.01},
      "blocks": [ {"id": "e", "type": "Sum", "params": {"signs": "+-"}, "sample_time": -1}, ... ],
      "lines":  [ {"src": "sp:1", "dst": "e:1"}, ... ]
    }

Ports are 1-based like Simulink ("block:port"). Subsystem blocks carry a nested model in
params.model and are flattened at compile time (virtual subsystems).

Execution follows Simulink's discrete fixed-step semantics:
1. Sample times are resolved (-1 = inherited from the fastest driving block); every sample time
   must be an integer multiple of the base step.
2. Blocks are sorted so each block runs after the blocks feeding its *direct-feedthrough*
   inputs. A cycle through feedthrough ports only is an **algebraic loop** and is rejected.
3. Each step: outputs of blocks with a sample hit in sorted order, then state updates.
   Blocks without a hit hold their previous outputs (zero-order hold).
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any

from ecsim.model import blocks as _blocks
from ecsim.model.blocks import STATEFUL, BlockCtx, ModelError, block_type

FORMAT = "ecsim-model"


# ---- format -------------------------------------------------------------------
def load_model(text_or_dict: str | dict) -> dict:
    m = json.loads(text_or_dict) if isinstance(text_or_dict, str) else copy.deepcopy(text_or_dict)
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise ModelError(f"not an ECSim model (expected \"format\": \"{FORMAT}\")")
    if int(m.get("version", 0)) != 1:
        raise ModelError("unsupported model version")
    for key in ("name", "blocks", "lines"):
        if key not in m:
            raise ModelError(f"model is missing '{key}'")
    return m


def dump_model(m: dict) -> str:
    return json.dumps(m, indent=2)


def _parse_port(ref: str, where: str) -> tuple[str, int]:
    if not isinstance(ref, str) or ":" not in ref:
        raise ModelError(f"{where}: port reference must look like 'block:1', got {ref!r}")
    blk, port = ref.rsplit(":", 1)
    try:
        n = int(port)
    except ValueError:
        raise ModelError(f"{where}: port number must be an integer in {ref!r}") from None
    if n < 1:
        raise ModelError(f"{where}: ports are 1-based, got {ref!r}")
    return blk, n


# ---- compile ------------------------------------------------------------------
@dataclass
class CBlock:
    id: str
    type: str
    params: dict
    st_param: float
    n_in: int
    n_out: int
    feedthrough: list[bool]
    inputs: list[tuple[str, int] | None] = field(default_factory=list)   # source (block, out port idx 0-based)
    ts: float = 0.0
    every: int = 1


@dataclass
class Compiled:
    name: str
    step: float
    blocks: dict[str, CBlock]
    order: list[str]
    inports: list[str]              # block ids ordered by port number
    outports: list[str]
    warnings: list[str]

    @property
    def inport_names(self) -> list[str]:
        return [self.blocks[b].params.get("name", b) for b in self.inports]

    @property
    def outport_names(self) -> list[str]:
        return [self.blocks[b].params.get("name", b) for b in self.outports]


def _flatten(m: dict, prefix: str = "") -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (blocks, lines) with subsystems inlined. Subsystem ports are resolved away."""
    blocks: list[dict] = []
    lines: list[tuple[str, str]] = []
    sub_maps: dict[str, tuple[dict[int, list[str]], dict[int, str]]] = {}
    ids = set()
    for b in m["blocks"]:
        bid = b.get("id")
        if not bid or not isinstance(bid, str) or "/" in bid or ":" in bid:
            raise ModelError(f"invalid block id {bid!r} in '{m.get('name')}' (no '/' or ':')")
        if bid in ids:
            raise ModelError(f"duplicate block id '{bid}' in '{m.get('name')}'")
        ids.add(bid)
        full = prefix + bid
        if b["type"] == "Subsystem":
            inner = load_model(b.get("params", {}).get("model", {}))
            iblocks, ilines = _flatten(inner, full + "/")
            # Map inner Inport/Outport (by port number) to what they connect to.
            in_targets: dict[int, list[str]] = {}
            out_sources: dict[int, str] = {}
            inner_ports = {x["id"]: x for x in inner["blocks"] if x["type"] in ("Inport", "Outport")}
            port_of = {full + "/" + k: int(v.get("params", {}).get("port", 1)) for k, v in inner_ports.items()}
            kept_lines = []
            for src, dst in ilines:
                sblk, _ = src.rsplit(":", 1)
                dblk, _ = dst.rsplit(":", 1)
                if sblk in port_of and inner_ports[sblk.rsplit("/", 1)[1]]["type"] == "Inport":
                    in_targets.setdefault(port_of[sblk], []).append(dst)
                elif dblk in port_of and inner_ports[dblk.rsplit("/", 1)[1]]["type"] == "Outport":
                    out_sources[port_of[dblk]] = src
                else:
                    kept_lines.append((src, dst))
            blocks += [x for x in iblocks if x["id"] not in port_of]
            lines += kept_lines
            sub_maps[full] = (in_targets, out_sources)
        else:
            nb = dict(b)
            nb["id"] = full
            blocks.append(nb)
    for i, ln in enumerate(m["lines"]):
        src, dst = ln.get("src"), ln.get("dst")
        sb, sp = _parse_port(src, f"line {i + 1}")
        db, dp = _parse_port(dst, f"line {i + 1}")
        if sb not in ids:
            raise ModelError(f"line {i + 1}: unknown source block '{sb}'")
        if db not in ids:
            raise ModelError(f"line {i + 1}: unknown destination block '{db}'")
        lines.append((f"{prefix}{sb}:{sp}", f"{prefix}{db}:{dp}"))
    # Resolve connections through subsystem boundaries.
    resolved: list[tuple[str, str]] = []
    for src, dst in lines:
        sb, sp = src.rsplit(":", 1)
        if sb in sub_maps:
            s2 = sub_maps[sb][1].get(int(sp))
            if s2 is None:
                raise ModelError(f"subsystem '{sb}' has no Outport {sp} connected inside")
            src = s2
        db, dp = dst.rsplit(":", 1)
        if db in sub_maps:
            for t in sub_maps[db][0].get(int(dp), []):
                resolved.append((src, t))
            continue
        resolved.append((src, dst))
    return blocks, resolved


def compile_model(model: str | dict) -> Compiled:
    m = load_model(model)
    solver = m.get("solver", {"type": "FixedStepDiscrete", "step": 0.01})
    if solver.get("type") != "FixedStepDiscrete":
        raise ModelError("only the FixedStepDiscrete solver is supported (continuous plants run outside the model)")
    step = float(solver.get("step", 0))
    if not step > 0:
        raise ModelError("solver step must be > 0")
    raw_blocks, lines = _flatten(m)
    blocks: dict[str, CBlock] = {}
    for b in raw_blocks:
        bt = block_type(b["type"])
        params = dict(bt.defaults or {})
        params.update(b.get("params", {}))
        try:
            _blocks.validate_params(b["type"], params)
            n_in, n_out = bt.n_in(params), bt.n_out(params)
            ft = bt.feedthrough(params)
        except KeyError as exc:
            raise ModelError(f"block '{b['id']}' ({b['type']}): missing parameter {exc}") from None
        except ModelError as exc:
            raise ModelError(f"block '{b['id']}' ({b['type']}): {exc}") from None
        blocks[b["id"]] = CBlock(b["id"], b["type"], params, float(b.get("sample_time", -1)),
                                 n_in, n_out, ft, [None] * n_in)
    for src, dst in lines:
        sb, sp = src.rsplit(":", 1)
        db, dp = dst.rsplit(":", 1)
        sp_i, dp_i = int(sp), int(dp)
        if sp_i > blocks[sb].n_out:
            raise ModelError(f"'{sb}' has {blocks[sb].n_out} output port(s), line uses port {sp_i}")
        if dp_i > blocks[db].n_in:
            raise ModelError(f"'{db}' has {blocks[db].n_in} input port(s), line uses port {dp_i}")
        if blocks[db].inputs[dp_i - 1] is not None:
            raise ModelError(f"input '{db}:{dp_i}' is driven by more than one line")
        blocks[db].inputs[dp_i - 1] = (sb, sp_i - 1)
    warnings: list[str] = []
    for b in blocks.values():
        for i, src in enumerate(b.inputs):
            if src is None:
                raise ModelError(f"input port '{b.id}:{i + 1}' is not connected")
    used = {(s[0], s[1]) for b in blocks.values() for s in b.inputs if s}
    for b in blocks.values():
        for k in range(b.n_out):
            if (b.id, k) not in used and b.type != "Inport":
                warnings.append(f"output '{b.id}:{k + 1}' is not connected")

    _resolve_sample_times(blocks, step, warnings)
    order = _sort(blocks)
    inports = sorted((b for b in blocks.values() if b.type == "Inport" and "/" not in b.id),
                     key=lambda b: int(b.params["port"]))
    outports = sorted((b for b in blocks.values() if b.type == "Outport" and "/" not in b.id),
                      key=lambda b: int(b.params["port"]))
    for kind, ports in (("Inport", inports), ("Outport", outports)):
        nums = [int(p.params["port"]) for p in ports]
        if nums != list(range(1, len(nums) + 1)):
            raise ModelError(f"{kind} port numbers must be 1..{len(nums)} without gaps, got {nums}")
    return Compiled(m["name"], step, blocks, order, [b.id for b in inports], [b.id for b in outports], warnings)


def _resolve_sample_times(blocks: dict[str, CBlock], step: float, warnings: list[str]) -> None:
    resolved: dict[str, float] = {}
    for b in blocks.values():
        if b.st_param > 0:
            resolved[b.id] = b.st_param
        elif not b.inputs:
            resolved[b.id] = step
    changed = True
    while changed:
        changed = False
        for b in blocks.values():
            if b.id in resolved:
                continue
            srcs = [resolved.get(s[0]) for s in b.inputs if s]
            known = [t for t in srcs if t is not None]
            if known and len(known) == len(srcs):
                resolved[b.id] = min(known)
                changed = True
    for b in blocks.values():
        ts = resolved.get(b.id, step)   # loops of inherited blocks fall back to the base rate
        ratio = ts / step
        if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
            raise ModelError(f"block '{b.id}': sample time {ts:g} s is not a multiple of the base step {step:g} s")
        b.ts, b.every = ts, int(round(ratio))
    for b in blocks.values():
        for s in b.inputs:
            if s and blocks[s[0]].every != b.every and b.type not in ("Outport", "Terminator"):
                src = blocks[s[0]]
                warnings.append(f"rate transition {src.id} ({src.ts:g} s) -> {b.id} ({b.ts:g} s) without a "
                                "Rate Transition block; values are held (zero-order hold)")


def _sort(blocks: dict[str, CBlock]) -> list[str]:
    deps: dict[str, set[str]] = {bid: set() for bid in blocks}
    for b in blocks.values():
        for i, s in enumerate(b.inputs):
            if s and b.feedthrough[i]:
                deps[b.id].add(s[0])
    order: list[str] = []
    ready = sorted(bid for bid, d in deps.items() if not d)
    remaining = {bid: set(d) for bid, d in deps.items()}
    while ready:
        nxt = ready.pop(0)
        order.append(nxt)
        del remaining[nxt]
        newly = []
        for bid, d in remaining.items():
            if nxt in d:
                d.discard(nxt)
                if not d:
                    newly.append(bid)
        ready = sorted(ready + newly)
    if remaining:
        loop = _find_cycle(remaining)
        raise ModelError("algebraic loop through direct-feedthrough blocks: " + " -> ".join(loop)
                         + ". Break it with a Unit Delay (or a Forward-Euler integrator).")
    return order


def _find_cycle(deps: dict[str, set[str]]) -> list[str]:
    start = sorted(deps)[0]
    path, seen = [start], {start}
    cur = start
    while True:
        nxt = sorted(d for d in deps[cur] if d in deps)[0]
        if nxt in seen:
            i = path.index(nxt)
            cyc = path[i:] + [nxt]
            return list(reversed(cyc))
        path.append(nxt)
        seen.add(nxt)
        cur = nxt


# ---- simulate -----------------------------------------------------------------
class Simulator:
    def __init__(self, compiled: Compiled) -> None:
        self.c = compiled
        self.k = 0
        self.ctx = {bid: BlockCtx(b.params, b.ts) for bid, b in compiled.blocks.items()}
        self.state: dict[str, Any] = {}
        self.out: dict[str, list[float]] = {}
        for bid, b in compiled.blocks.items():
            bt = block_type(b.type)
            self.state[bid] = bt.init(self.ctx[bid])
            self.out[bid] = [0.0] * b.n_out
        self._ext = [0.0] * len(compiled.inports)

    @property
    def t(self) -> float:
        return self.k * self.c.step

    def _u(self, b: CBlock) -> list[float]:
        return [self.out[s[0]][s[1]] for s in b.inputs]

    def step(self, inputs: list[float] | dict[str, float]) -> list[float]:
        """Advance one base step. Returns the outport values at time t (before the step)."""
        c = self.c
        if isinstance(inputs, dict):
            missing = [n for n in c.inport_names if n not in inputs]
            if missing:
                raise ModelError(f"missing input values for {missing}")
            vals = [float(inputs[n]) for n in c.inport_names]
        else:
            if len(inputs) != len(c.inports):
                raise ModelError(f"model has {len(c.inports)} inport(s), got {len(inputs)} values")
            vals = [float(v) for v in inputs]
        for bid, v in zip(c.inports, vals):
            if not math.isfinite(v):
                raise ModelError(f"non-finite input value for '{bid}'")
            self.out[bid] = [v]
        hits = [bid for bid in c.order if self.k % c.blocks[bid].every == 0]
        for bid in hits:
            b = c.blocks[bid]
            if b.type == "Inport":
                continue
            bt = block_type(b.type)
            y = bt.output(self.ctx[bid], self.state[bid], self._u(b))
            if y:
                self.out[bid] = [float(v) for v in y]
        result = [self.out[c.blocks[o].inputs[0][0]][c.blocks[o].inputs[0][1]] for o in c.outports]
        for bid in hits:
            b = c.blocks[bid]
            if b.type in STATEFUL:
                bt = block_type(b.type)
                self.state[bid] = bt.update(self.ctx[bid], self.state[bid], self._u(b))
        self.k += 1
        return result

    def signal(self, block_id: str, port: int = 1) -> float:
        """Current value of any block output (for debugging/plots), 1-based port."""
        return self.out[block_id][port - 1]
