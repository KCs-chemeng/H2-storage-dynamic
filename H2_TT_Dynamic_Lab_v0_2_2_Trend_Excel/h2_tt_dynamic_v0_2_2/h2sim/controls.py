"""Safe data-only condition/action evaluation; no eval/exec of user expressions."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Any
from .config import RuleSpec, Condition

PRIORITIES = {'AUTO': 20, 'EVENT': 40, 'NORMAL': 50,
              'MANUAL': 60, 'INTERLOCK': 70, 'PROTECTION': 80, 'STOP': 100}


def matches(c: Condition, values: dict) -> bool:
    a, b = values[c.variable], c.value
    if isinstance(a, str):
        return a == str(b) if c.op == '==' else a != str(b)
    a, b = float(a), float(b)
    if c.op == '>=': return a >= b
    if c.op == '<=': return a <= b
    if c.op == '>': return a > b
    if c.op == '<': return a < b
    if c.op == '==': return abs(a-b) <= 1e-9
    if c.op == '!=': return abs(a-b) > 1e-9
    raise ValueError('Unknown condition operator')


def expression(conditions, combine, values):
    if not conditions:
        return False
    result = [matches(c, values) for c in conditions]
    return all(result) if combine == 'ALL' else any(result)

@dataclass
class RuleState:
    active: bool = False
    fired_count: int = 0
    since: float | None = None
    reset_since: float | None = None
    trigger_values: dict = field(default_factory=dict)
    last_fired: float | None = None

    def evaluate(self, spec: RuleSpec, values: dict, now: float) -> tuple[bool, bool]:
        """Returns (rising, falling). Output overrides persist while active.

        WHILE: condition-held; optional reset expression supplies hysteresis.
        LATCH: until reset expression/operator reset; can retrigger.
        ONCE: output retained until reset, then disarmed until operator reset.
        """
        before = self.active
        in_scope = (spec.enabled and (spec.mode == 'ALL' or spec.mode == values['mode'])
                    and (spec.phase == 'ANY'
                         or spec.phase == 'EQ' and values['phase'].startswith('EQ_')
                         or spec.phase == 'RECOVERY' and values['phase'].startswith('REC_')))
        trigger = expression(spec.conditions, spec.combine, values) if in_scope else False
        reset = expression(spec.reset_conditions, spec.reset_combine, values)
        if not spec.enabled:
            self.active = False; self.since = None; self.reset_since = None
            return False, before
        if self.active:
            should_clear = (spec.behavior == 'WHILE' and not in_scope)
            if spec.reset_conditions:
                self.reset_since = (now if self.reset_since is None else self.reset_since) if reset else None
                should_clear |= self.reset_since is not None and now-self.reset_since >= spec.reset_hold_s-1e-9
            elif spec.behavior == 'WHILE':
                should_clear |= not trigger
            if should_clear:
                self.active = False; self.since = None; self.reset_since = None
                return False, True
            return False, False
        if reset:
            self.since = None
            return False, False
        if spec.behavior == 'ONCE' and self.fired_count:
            return False, False
        self.since = (now if self.since is None else self.since) if trigger else None
        if self.since is not None and now-self.since >= spec.hold_s-1e-9:
            self.active = True; self.fired_count += 1; self.last_fired = now
            self.trigger_values = {c.variable: values[c.variable] for c in spec.conditions}
            return True, False
        return False, False

@dataclass
class Request:
    value: float
    priority: int
    source: str


def arbitrate(requests: list[Request]) -> Request:
    """Highest authority; equal authority resolves to the lower/closed output."""
    if not requests:
        return Request(0, 0, 'DEFAULT CLOSED')
    return sorted(requests, key=lambda x: (-x.priority, x.value, x.source))[0]
