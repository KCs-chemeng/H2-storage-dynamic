"""Versioned model inputs; live setpoints are intentionally a separate API."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import copy
import json
import math
import re

CORE_VALVES = ('MOV-101', 'BYPASS', 'PRV-102', 'COMP-OUT')

@dataclass
class VesselSpec:
    name: str
    volume_m3: float
    pressure_barg: float
    temperature_c: float = 45.0
    target_barg: float = 190.0
    hh_barg: float | None = None

@dataclass
class ValveSpec:
    cv: float
    stroke_s: float = 3.0
    characteristic: str = 'Linear'
    rangeability: float = 50.0
    close_s: float | None = None

@dataclass
class Condition:
    variable: str
    op: str = '>='
    value: float | str | bool = 0.0

@dataclass
class Action:
    action: str
    target: str = ''
    value: float | str | bool = 0.0

@dataclass
class RuleSpec:
    name: str
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    combine: str = 'ALL'
    hold_s: float = 0.0
    mode: str = 'ALL'
    phase: str = 'ANY'
    behavior: str = 'WHILE'
    priority: str = 'NORMAL'
    enabled: bool = True
    reset_conditions: list[Condition] = field(default_factory=list)
    reset_combine: str = 'ALL'
    reset_hold_s: float = 0.0

    @classmethod
    def from_dict(cls, data):
        d = copy.deepcopy(data)
        d['conditions'] = [Condition(**c) if isinstance(c, dict) else c for c in d.get('conditions', [])]
        d['reset_conditions'] = [Condition(**c) if isinstance(c, dict) else c for c in d.get('reset_conditions', [])]
        d['actions'] = [Action(**a) if isinstance(a, dict) else a for a in d.get('actions', [])]
        return cls(**d)

@dataclass
class Config:
    version: str = '0.2.0'
    eos: str = 'Leachman'
    dt_s: float = 0.5
    sample_s: float = 2.0
    tt: VesselSpec = field(default_factory=lambda: VesselSpec('TT', 22.9, 200.0, 45.0, 0.0))
    banks: list[VesselSpec] = field(default_factory=lambda: [
        VesselSpec('A', 11.7, 20.0), VesselSpec('B', 11.7, 20.0), VesselSpec('C', 10.4, 20.0)])
    valves: dict[str, ValveSpec] = field(default_factory=lambda: {
        'MOV-101': ValveSpec(1.5, 2.0), 'BYPASS': ValveSpec(.30, 4.0),
        'PRV-102': ValveSpec(1.35, 3.0, 'Equal percentage'), 'COMP-OUT': ValveSpec(.50, 3.0),
        'BANK-A': ValveSpec(.5), 'BANK-B': ValveSpec(.5), 'BANK-C': ValveSpec(.5)})
    k: float = 1.4
    xt: float = .72
    fp: float = 1.0
    tt_stop_barg: float = 10.0
    bank_hh_barg: float = 210.0
    equalize_dp_bar: float = .30
    equalize_hold_s: float = 3.0
    equalize_order: list[str] = field(default_factory=lambda: ['C', 'A', 'B'])
    recovery_order: list[str] = field(default_factory=lambda: ['C', 'A'])
    compressor_kg_h: float = 22.48
    suction_set_barg: float = 7.0
    suction_min_barg: float = 1.0
    compressor_max_barg: float = 200.0
    compressor_ramp_s: float = 5.0
    compressor_reference_kw: float = 75.0
    compressor_temperature_c: float = 45.0
    aftercooler_temperature_c: float = 30.0
    compressor_enabled: bool = True
    compressor_stop_s: float = 2.0
    shutdown_mode: str = 'DELAYED'
    manual_target_policy: str = 'AUTO_ONLY'
    sequence_mode: str = 'EQ_RECOVERY'
    actuator_timeout_s: float = 120.0
    no_flow_delay_s: float = 10.0
    numerical_max_barg: float = 350.0
    events: list[dict] = field(default_factory=list)
    rules: list[RuleSpec] = field(default_factory=list)

    def normalize(self):
        """Only fill derived valve entries; never silently discard a rule or change a SP."""
        for b in self.banks:
            self.valves.setdefault('BANK-' + b.name, ValveSpec(.5))
        self.version = '0.2.0'
        return self

    def hh(self, b):
        return self.bank_hh_barg if b.hh_barg is None else b.hh_barg

    def variable_names(self):
        names = ['time_s', 'tt.pressure', 'tt.mass', 'flow_kg_h', 'suction.pressure',
                 'selected.pressure', 'selected.target', 'selected.dp', 'compressor.speed',
                 'mode', 'phase', 'route', 'tripped', 'setpoint.tt_stop_barg',
                 'setpoint.equalize_dp_bar', 'setpoint.suction_set_barg']
        for b in self.banks:
            names += [f'bank.{b.name}.{x}' for x in ('pressure', 'mass', 'target', 'hh', 'dp')]
        for tag in self.valves:
            names += [f'valve.{tag}.{x}' for x in ('opening', 'command')]
        return names

    def validate_action(self, a: Action, protection=False):
        if a.action == 'valve':
            if a.target not in self.valves or not finite(a.value) or not 0 <= float(a.value) <= 100:
                raise ValueError('밸브 동작: 유효한 Tag와 개도 0~100% 필요')
            if protection and float(a.value) != 0:
                raise ValueError('보호/인터록 규칙은 밸브 CLOSE만 허용합니다.')
        elif a.action == 'compressor':
            if not isinstance(a.value, bool):
                raise ValueError('압축기 value는 JSON true / false')
            if protection and a.value:
                raise ValueError('보호/인터록 규칙은 압축기 STOP만 허용합니다.')
        elif a.action == 'bank':
            if str(a.value) not in {b.name for b in self.banks}:
                raise ValueError('규칙의 Bank 이름을 확인하세요.')
        elif a.action == 'route':
            if a.value not in ('BYPASS', 'RECOVERY'):
                raise ValueError('경로는 BYPASS 또는 RECOVERY')
            if a.value == 'RECOVERY' and not self.compressor_enabled:
                raise ValueError('압축기 미사용 구성에는 RECOVERY 경로가 없습니다.')
        elif a.action in ('next', 'stop', 'trip', 'release'):
            if a.action == 'release' and a.target not in (*self.valves, 'COMPRESSOR', '*'):
                raise ValueError('release 대상은 밸브 Tag / COMPRESSOR / *')
        else:
            raise ValueError(f'지원하지 않는 동작: {a.action}')
        if protection and a.action not in ('valve', 'compressor', 'stop', 'trip'):
            raise ValueError('보호/인터록 규칙에는 차단·정지 동작만 사용하세요.')

    def validate_rule(self, r):
        if not r.name.strip() or not r.conditions or not r.actions:
            raise ValueError('규칙 이름, 조건 및 동작이 필요합니다.')
        if r.combine not in ('ALL', 'ANY') or r.reset_combine not in ('ALL', 'ANY'):
            raise ValueError('조건 결합: ALL(AND) / ANY(OR)')
        if r.mode not in ('ALL', 'AUTO', 'MANUAL') or r.phase not in ('ANY', 'EQ', 'RECOVERY'):
            raise ValueError('규칙의 운전모드/적용단계 오류')
        if r.behavior not in ('WHILE', 'LATCH', 'ONCE') or r.priority not in ('NORMAL', 'INTERLOCK', 'PROTECTION'):
            raise ValueError('규칙 유지방식/우선순위 오류')
        if not all(finite(v) and 0 <= float(v) <= 86400 for v in (r.hold_s, r.reset_hold_s)):
            raise ValueError('규칙 유지시간 0~86400초')
        names = set(self.variable_names())
        for c in r.conditions + r.reset_conditions:
            if c.variable not in names or c.op not in ('>=', '<=', '>', '<', '==', '!='):
                raise ValueError(f'규칙 변수/연산자 오류: {c.variable}')
            if c.variable in ('mode', 'phase', 'route'):
                if c.op not in ('==', '!=') or not isinstance(c.value, str):
                    raise ValueError('상태 비교에는 == 또는 != 및 문자열 사용')
            elif not finite(c.value):
                raise ValueError('수치 조건에는 유한한 숫자를 입력하세요.')
        for a in r.actions:
            self.validate_action(a, r.priority != 'NORMAL')
        return r

    def validate(self):
        if self.eos not in ('Leachman', 'PR', 'Ideal', 'CoolProp'):
            raise ValueError('EOS: Leachman / PR / Ideal / CoolProp')
        if not all(finite(getattr(self, n)) for n in (
            'dt_s','sample_s','k','xt','fp','tt_stop_barg','bank_hh_barg','equalize_dp_bar',
            'equalize_hold_s','compressor_kg_h','suction_set_barg','suction_min_barg',
            'compressor_max_barg','compressor_ramp_s','compressor_reference_kw',
            'compressor_temperature_c','aftercooler_temperature_c','compressor_stop_s',
            'actuator_timeout_s','no_flow_delay_s','numerical_max_barg')):
            raise ValueError('모델 입력은 유한한 숫자여야 합니다.')
        if not .01 <= self.dt_s <= 2 or not self.dt_s <= self.sample_s <= 3600:
            raise ValueError('dt 0.01~2초 / 기록간격 dt~3600초')
        if not 1 <= len(self.banks) <= 32:
            raise ValueError('Bank 1~32개를 입력하세요.')
        names = [b.name for b in self.banks]
        if len(set(names)) != len(names) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,23}', n) or n == 'TT' for n in names):
            raise ValueError('Bank 이름: 영문 시작, 영문/숫자/_/- 24자 이내, 중복 및 TT 금지')
        if not 1 < self.k <= 1.67 or not .05 <= self.xt <= .95 or not .05 <= self.fp <= 1:
            raise ValueError('k >1~1.67, xT 0.05~0.95, Fp 0.05~1')
        if not 0 < self.numerical_max_barg <= 350:
            raise ValueError('현 물성/모델 검토범위: 압력상한 0~350 barg')
        for b in [self.tt] + self.banks:
            vals = (b.volume_m3, b.pressure_barg, b.temperature_c, b.target_barg)
            if not all(finite(x) for x in vals) or not .001 <= b.volume_m3 <= 10000 or not 0 <= b.pressure_barg < self.numerical_max_barg or not -50 <= b.temperature_c <= 150:
                raise ValueError(f'{b.name}: V 0.001~10000m³, P 0~상한 미만, T -50~150°C')
        for b in self.banks:
            hh = self.hh(b)
            if not finite(hh) or not 0 < b.target_barg < hh <= self.numerical_max_barg or not b.pressure_barg < hh:
                raise ValueError(f'{b.name}: 초기압력/목표압력 < HH ≤ 계산상한')
            if self.compressor_enabled and b.target_barg >= self.compressor_max_barg:
                raise ValueError(f'{b.name}: 목표압력 < 압축기 토출상한')
        if not 0 <= self.tt_stop_barg < self.tt.pressure_barg:
            raise ValueError('TT 종료압력은 0 이상, TT 초기압력 미만')
        if not 0 <= self.suction_min_barg < self.suction_set_barg < self.compressor_max_barg <= self.numerical_max_barg:
            raise ValueError('흡입최소 < 흡입SP < 토출상한 ≤ 계산상한')
        if not 0 < self.equalize_dp_bar <= 20 or not 0 <= self.equalize_hold_s <= 3600:
            raise ValueError('종료차압 0~20 bar, 유지시간 0~3600초')
        if not 0 < self.compressor_kg_h <= 10000 or self.compressor_reference_kw < 0:
            raise ValueError('압축기 정격유량/참조동력 오류')
        if not all(0 <= x <= 600 for x in (self.compressor_ramp_s, self.compressor_stop_s)):
            raise ValueError('압축기 기동/정지시간 0~600초')
        if not all(0 < x <= 3600 for x in (self.actuator_timeout_s, self.no_flow_delay_s)):
            raise ValueError('Timeout 및 무유량 지연은 0~3600초')
        if self.shutdown_mode not in ('IDEAL', 'DELAYED') or self.manual_target_policy not in ('AUTO_ONLY', 'ALL_MODES'):
            raise ValueError('종료모드/수동 목표정책 오류')
        if self.sequence_mode not in ('EQ_RECOVERY', 'EQUALIZE_ONLY', 'COMPRESS_ONLY'):
            raise ValueError('자동 시퀀스 형식 오류')
        if not self.compressor_enabled and self.sequence_mode != 'EQUALIZE_ONLY':
            raise ValueError('압축기 미사용 시 EQUALIZE_ONLY 선택')
        for order in (self.equalize_order, self.recovery_order):
            if len(set(order)) != len(order) or any(n not in names for n in order):
                raise ValueError('충전순서는 존재하는 Bank 이름, 중복 없이 입력')
        if self.sequence_mode != 'COMPRESS_ONLY' and not self.equalize_order:
            raise ValueError('균압 순서가 비어 있습니다.')
        if self.compressor_enabled and self.sequence_mode != 'EQUALIZE_ONLY' and not self.recovery_order:
            raise ValueError('압축회수 순서가 비어 있습니다.')
        expected = set(CORE_VALVES) | {'BANK-' + n for n in names}
        if set(self.valves) != expected:
            raise ValueError('밸브 목록: 공통 4개 + Bank별 BANK-이름 1개씩 필요')
        for tag, v in self.valves.items():
            if not all(finite(x) for x in (v.cv,v.stroke_s,v.rangeability)) or not 0 < v.cv <= 50 or not 0 <= v.stroke_s <= 600 or not 1 < v.rangeability <= 1000:
                raise ValueError(f'{tag}: Cv 0~50, 행정시간 0~600, R>1')
            if v.close_s is not None and (not finite(v.close_s) or not 0 <= v.close_s <= 600):
                raise ValueError(f'{tag}: 폐쇄시간 0~600초')
            if v.characteristic not in ('Linear', 'Equal percentage'):
                raise ValueError('밸브 특성 오류')
        if len({r.name for r in self.rules}) != len(self.rules):
            raise ValueError('규칙 이름은 중복될 수 없습니다.')
        for r in self.rules:
            self.validate_rule(r)
        for e in self.events:
            if not finite(e.get('time_s')) or float(e['time_s']) < 0:
                raise ValueError('이벤트 시간은 0 이상')
            if e.get('authority', 'operator') not in ('operator', 'automatic'):
                raise ValueError('이벤트 authority: operator / automatic')
            if e.get('action') in ('valve','bank','route','compressor','release'):
                self.validate_action(Action(e['action'], e.get('target',''), e.get('value',0)))
            elif e.get('action') not in ('estop','auto','manual','stop'):
                raise ValueError('이벤트 action 오류')
        return self

    @classmethod
    def from_dict(cls, data):
        d = copy.deepcopy(data)
        legacy = str(d.get('version', '0.1')).startswith('0.1')
        d['tt'] = VesselSpec(**d['tt'])
        d['banks'] = [VesselSpec(**b) for b in d['banks']]
        d['valves'] = {k: ValveSpec(**v) for k,v in d['valves'].items()}
        d['rules'] = [RuleSpec.from_dict(r) for r in d.get('rules', [])]
        if legacy:
            d.setdefault('shutdown_mode', 'IDEAL')
            d.setdefault('compressor_stop_s', 0.0)
        return cls(**d).normalize().validate()

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def save(self, path):
        self.validate()
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding='utf-8')


def finite(x):
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
