#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H2 TT Dynamic Lab HMI v0.2.2 (engine v0.2.0) — OFFLINE ONLY, Python >=3.10 + Tkinter.
Generated from the accompanying h2sim modules; edit modular source first.
See README_KO.md and MODEL_NOTES.md for assumptions and limitations.
"""
from __future__ import annotations

# Hydrogen EOS numerical data
# --------------------------
# The residual Helmholtz coefficients and reducing constants used by the built-in
# Leachman pressure/density implementation were transcribed from the public
# CoolProp Hydrogen.json data file and reference Leachman et al. (2009),
# J. Phys. Chem. Ref. Data 38(3), 721-748, doi:10.1063/1.3160306.
# 
# Data source:
# https://raw.githubusercontent.com/CoolProp/CoolProp/master/dev/fluids/Hydrogen.json
# 
# CoolProp license notice (for the referenced numerical-data source):
# 
# MIT License
# 
# Copyright (c) 2012-2018 Ian H. Bell and other CoolProp developers
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# 
# This package does not redistribute the CoolProp compiled library, any font,
# or the Fisher/Emerson handbook. The Python implementation, UI, and simulation
# logic are newly authored for this request. No endorsement by a vendor is implied.


# ===== h2sim/physics.py =====
"""Pure-hydrogen EOS and quasi-steady compressible valve flow.

SI internally except valve pressures [bar abs] and flow [kg/h].
N8=94.8: Fisher/ISA mass-flow form with pressure in bar, temperature K,
M in kg/kmol. Do not substitute Kv for Cv.
"""
from dataclasses import dataclass
import math

R_UNIV = 8.31446261815324  # J/mol/K
MW = 2.016               # kg/kmol; project conversion basis
R_SPEC = R_UNIV * 1000.0 / MW
PATM_BAR = 1.01325
NORMAL_M3_KMOL = 22.414
N8 = 94.8


def normal_flow(mass_kg_h: float) -> float:
    return mass_kg_h / MW * NORMAL_M3_KMOL


def cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def largest_cubic_root(a: float, b: float, c: float) -> float:
    """Largest real root of z^3 + a z^2 + b z + c = 0."""
    p = b - a * a / 3.0
    q = 2.0 * a**3 / 27.0 - a * b / 3.0 + c
    discriminant = q * q / 4.0 + p**3 / 27.0
    if discriminant >= 0.0:
        s = math.sqrt(discriminant)
        return cbrt(-q / 2.0 + s) + cbrt(-q / 2.0 - s) - a / 3.0
    r = math.sqrt(-p / 3.0)
    arg = max(-1.0, min(1.0, -q / (2.0 * r**3)))
    angle = math.acos(arg) / 3.0
    return max(2.0 * r * math.cos(angle - i * 2.0 * math.pi / 3.0) - a / 3.0
               for i in range(3))


class HydrogenEOS:
    """PR requires no dependency; CoolProp is optional, explicitly selected.

    No silent property-backend substitution. All reservoirs are isothermal;
    this class does not implement a transient energy balance.
    """
    TC = 33.145
    PC = 1.2964e6
    OMEGA = -0.219

    def __init__(self, name: str = 'PR'):
        if name not in ('Leachman', 'PR', 'Ideal', 'CoolProp'):
            raise ValueError('EOS must be Leachman, PR, Ideal, or CoolProp')
        self.name = name
        self._cp = None
        self._state = None
        self.b = 0.07780 * R_UNIV * self.TC / self.PC
        self.kappa = 0.37464 + 1.54226*self.OMEGA - 0.26992*self.OMEGA**2
        self._a_cache: dict[float, float] = {}
        self._terms_cache = {}
        self._z_tables = {}
        if name == 'CoolProp':
            try:
                import CoolProp.CoolProp as CP
                self._cp = CP
                self._state = CP.AbstractState('HEOS', 'Hydrogen')
            except ImportError as exc:
                raise ValueError('CoolProp가 없습니다. pip install CoolProp 또는 PR을 선택하세요.') from exc


    # Leachman et al. 2009 normal hydrogen residual Helmholtz coefficients.
    # Numeric reference data: CoolProp dev/fluids/Hydrogen.json, MIT licensed.
    # Pressure/density only; no caloric properties are implemented here.
    EOS_R = 8.314472
    EOS_M = 0.00201588
    RHOC_MOLAR = 15508.0
    POWER = (
        (-6.93643,1,.6844,0), (.01,4,1.,0), (2.1101,1,.989,0),
        (4.52059,1,.489,0), (.732564,2,.803,0), (-1.34086,2,1.1444,0),
        (.130985,3,1.409,0), (-.777414,1,1.754,1), (.351944,3,1.311,1))
    GAUSS = (
        (-.0211716,2,4.187,1.685,.171,.7164,1.506),
        (.0226312,1,5.646,.489,.2245,1.3444,.156),
        (.032187,3,.791,.103,.1304,1.4517,1.736),
        (-.0231752,1,7.249,2.506,.2785,.7204,.67),
        (.0557346,1,2.986,1.607,.3967,1.5445,1.662))

    def _leachman_pd(self, rho, t):
        if t not in self._terms_cache:
            tau=self.TC/t
            powers=[(n*tau**tt,d,l) for n,d,tt,l in self.POWER]
            gauss=[(n*tau**tt*math.exp(-beta*(tau-gamma)**2),d,eta,eps)
                   for n,d,tt,eta,beta,gamma,eps in self.GAUSS]
            self._terms_cache[t]=(powers,gauss)
        powers,gauss=self._terms_cache[t]
        delta=rho/(self.EOS_M*self.RHOC_MOLAR)
        z=1.0; dzdelta=0.0
        for coeff,d,l in powers:
            dl=delta if l else 0.0
            f=coeff*delta**d*(math.exp(-dl) if l else 1.0)
            D=d-dl
            z+=f*D
            dzdelta+=f*(D*D-dl)
        for coeff,d,eta,eps in gauss:
            f=coeff*delta**d*math.exp(-eta*(delta-eps)**2)
            D=d-2*eta*delta*(delta-eps)
            z+=f*D
            dzdelta+=f*(D*D-2*eta*delta*(2*delta-eps))
        rt=self.EOS_R/self.EOS_M*t
        return rho*rt*z/1e5,rt*(z+dzdelta)/1e5,z

    def _leachman_density(self,p,t):
        rho=p*1e5*self.EOS_M/(self.EOS_R*t)
        for _ in range(15):
            pc,der,z=self._leachman_pd(rho,t)
            delta=(pc-p)/der
            new=max(1e-12,rho-delta)
            if abs(new-rho)<1e-11*max(1,rho):return new
            rho=new
        if abs(self._leachman_pd(rho,t)[0]-p)>1e-7:
            raise ValueError('Leachman density Newton solve did not converge')
        return rho

    def _leachman_z_fast(self,p,t):
        # Flow solver calls Z frequently. Table interpolation only for Z;
        # vessel P(rho,T) and initialization rho(P,T) use exact equations.
        increment=.25
        if t not in self._z_tables:
            values=[1.0]
            for i in range(1,2002):
                pp=i*increment
                rr=self._leachman_density(pp,t)
                values.append(self._leachman_pd(rr,t)[2])
            self._z_tables[t]=values
        index=p/increment;i=int(index)
        arr=self._z_tables[t]
        if i>=len(arr)-1:
            rho=self._leachman_density(p,t)
            return self._leachman_pd(rho,t)[2]
        f=index-i
        return arr[i]*(1-f)+arr[i+1]*f

    def _a(self, t: float) -> float:
        if t not in self._a_cache:
            alpha = (1.0 + self.kappa*(1.0-math.sqrt(t/self.TC)))**2
            self._a_cache[t] = 0.45724 * R_UNIV**2 * self.TC**2 / self.PC * alpha
        return self._a_cache[t]

    def z(self, p_bar_abs: float, t: float) -> float:
        if p_bar_abs <= 0 or t <= 0:
            raise ValueError('Absolute pressure and Kelvin temperature must be positive')
        if self.name == 'Leachman':
            return self._leachman_z_fast(p_bar_abs,t)
        if self.name == 'Ideal':
            return 1.0
        if self.name == 'CoolProp':
            self._state.update(self._cp.PT_INPUTS, p_bar_abs*1e5, t)
            return self._state.compressibility_factor()
        p = p_bar_abs*1e5
        A = self._a(t)*p / (R_UNIV*t)**2
        B = self.b*p / (R_UNIV*t)
        z = largest_cubic_root(B-1.0, A-3.0*B*B-2.0*B, -A*B+B*B+B**3)
        if not math.isfinite(z) or z <= B:
            raise ValueError('PR gas root is invalid; check state')
        return z

    def density(self, p_bar_abs: float, t: float) -> float:
        if self.name == 'Leachman':
            return self._leachman_density(p_bar_abs,t)
        if self.name == 'CoolProp':
            self._state.update(self._cp.PT_INPUTS, p_bar_abs*1e5, t)
            return self._state.rhomass()
        return p_bar_abs*1e5 / (self.z(p_bar_abs, t)*R_SPEC*t)

    def pressure(self, rho: float, t: float) -> float:
        if rho <= 0 or t <= 0:
            raise ValueError('Density and temperature must be positive')
        if self.name == 'Leachman':
            return self._leachman_pd(rho,t)[0]
        if self.name == 'CoolProp':
            self._state.update(self._cp.DmassT_INPUTS, rho, t)
            return self._state.p()/1e5
        if self.name == 'Ideal':
            return rho*R_SPEC*t/1e5
        vm = MW/1000.0/rho
        if vm <= self.b:
            raise ValueError('PR excluded-volume limit reached')
        return (R_UNIV*t/(vm-self.b)-self._a(t)/(vm*vm+2*self.b*vm-self.b*self.b))/1e5


@dataclass
class FlowResult:
    kg_h: float = 0.0
    choked: bool = False
    x: float = 0.0
    effective_cv: float = 0.0


def valve_flow(p1: float, p2: float, t: float, cv: float, z: float = 1.0,
               k: float = 1.4, xt: float = .72, fp: float = 1.0) -> FlowResult:
    """Forward-only valve; NRV/no-backflow assumption is explicit."""
    if p1 <= p2 or cv <= 0:
        return FlowResult(effective_cv=max(cv, 0.0))
    xc = min(.999999, (k/1.4)*xt)
    x = max(0.0, (p1-p2)/p1)
    xs = min(x, xc)
    Y = 1.0-xs/(3.0*xc)
    w = N8*fp*cv*p1*Y*math.sqrt(MW*xs/(z*t))
    return FlowResult(w, x >= xc, x, cv)


def outlet_pressure_for_flow(p1: float, t: float, cv: float, w: float,
                             eos: HydrogenEOS, k: float, xt: float, fp: float) -> float | None:
    """Maximum downstream pressure compatible with w.

    Inversion uses r = (3u-u^3)/2, u=2 sin(asin(r)/3), x=xc*u^2.
    None means demanded flow exceeds the choked capacity of this valve.
    """
    if cv <= 1e-12:
        return p1 if w <= 1e-12 else None
    xc = min(.999999, k/1.4*xt)
    cap = N8*fp*cv*p1*(2.0/3.0)*math.sqrt(MW*xc/(eos.z(p1,t)*t))
    if w > cap*(1+1e-10):
        return None
    ratio = min(1.0,max(0.0,w/max(cap,1e-300)))
    u = 2.0*math.sin(math.asin(ratio)/3.0)
    return p1*(1.0-xc*u*u)


def series_flow(p1: float, p2: float, t: float, cvs: list[float],
                eos: HydrogenEOS, k: float=1.4, xt: float=.72,
                fp: float=1.0) -> FlowResult:
    """Quasi-steady, isothermal, zero-holdup series valve network.

    The same flow crosses each valve. Internal pressures are eliminated by
    monotonic inversion and flow bisection. Shared k/xT/Fp are model inputs.
    This does not model pipe inertia or an acoustic wave.
    """
    if p1 <= p2 or any(c <= 1e-12 for c in cvs):
        return FlowResult()
    if len(cvs) == 1:
        return valve_flow(p1,p2,t,cvs[0],eos.z(p1,t),k,xt,fp)
    hi = valve_flow(p1,0.0,t,cvs[0],eos.z(p1,t),k,xt,fp).kg_h
    lo = 0.0
    for _ in range(27):
        w=(lo+hi)*.5
        p=p1
        possible=True
        for cv in cvs:
            pnext=outlet_pressure_for_flow(p,t,cv,w,eos,k,xt,fp)
            if pnext is None or pnext < p2:
                possible=False
                break
            p=pnext
        if possible:
            lo=w
        else:
            hi=w
    # Identify at least one valve reaching the choking limit on the limiting path.
    p=p1
    choking=False
    for cv in cvs:
        cap=valve_flow(p,0.0,t,cv,eos.z(p,t),k,xt,fp).kg_h
        if lo >= cap*(1-1e-5):
            choking=True
        pnext=outlet_pressure_for_flow(p,t,cv,lo,eos,k,xt,fp)
        if pnext is None:
            choking=True
            break
        p=pnext
    return FlowResult(lo,choking,(p1-p2)/p1,min(cvs))


# ===== h2sim/config.py =====
"""Versioned model inputs; live setpoints are intentionally a separate API."""
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


# ===== h2sim/controls.py =====
"""Safe data-only condition/action evaluation; no eval/exec of user expressions."""
from dataclasses import dataclass, field
from typing import Callable, Any

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


# ===== h2sim/engine.py =====
"""v0.2: finite-inventory receiving model with explicit command arbitration.

One TT, configurable parallel banks, one bypass and optional equivalent compressor.
All reservoirs isothermal. No PLC I/O, acoustic/thermal/PSV model.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import deque
import copy
import csv
import json
import math
import tempfile
import shutil

EPS = 1e-8

@dataclass
class Valve:
    spec: ValveSpec
    command: float = 0.0
    opening: float = 0.0
    source: str = 'DEFAULT CLOSED'

    def position_after(self, dt):
        stroke = self.spec.stroke_s if self.command >= self.opening or self.spec.close_s is None else self.spec.close_s
        if stroke <= 0: return self.command
        d = dt / stroke
        return min(1.0, max(0.0, self.opening + max(-d, min(d, self.command-self.opening))))

    def effective_cv(self, opening=None):
        u = self.opening if opening is None else opening
        if u <= 1e-9: return 0.0
        f = u if self.spec.characteristic == 'Linear' else (self.spec.rangeability**u-1)/(self.spec.rangeability-1)
        return self.spec.cv*f

@dataclass
class Vessel:
    spec: VesselSpec
    eos: HydrogenEOS
    mass_kg: float

    @classmethod
    def create(cls, spec, eos):
        return cls(spec, eos, eos.density(spec.pressure_barg+PATM_BAR,spec.temperature_c+273.15)*spec.volume_m3)

    @property
    def pabs(self): return self.eos.pressure(self.mass_kg/self.spec.volume_m3,self.spec.temperature_c+273.15)
    @property
    def pg(self): return self.pabs-PATM_BAR
    def pressure_at(self, mass): return self.eos.pressure(max(mass,1e-12)/self.spec.volume_m3,self.spec.temperature_c+273.15)
    def mass_at_pg(self, pg): return self.eos.density(pg+PATM_BAR,self.spec.temperature_c+273.15)*self.spec.volume_m3


class Simulator:
    def __init__(self, config: Config | None = None):
        self.cfg = copy.deepcopy(config or Config()).normalize().validate()
        self.initial_config = asdict(self.cfg)
        self.eos = HydrogenEOS(self.cfg.eos)
        self.tt = Vessel.create(self.cfg.tt,self.eos)
        self.banks = {b.name: Vessel.create(b,self.eos) for b in self.cfg.banks}
        self.valves = {k: Valve(copy.deepcopy(v)) for k,v in self.cfg.valves.items()}
        self.time_s = 0.0
        self.mode = 'MANUAL'; self.phase = 'READY'; self.route = 'BYPASS'
        self.selected_bank = (self.cfg.equalize_order or list(self.banks))[0]
        self.pending_bank = None
        self.compressor_command = False; self.compressor_speed = 0.0
        self.compressor_source = 'DEFAULT STOP'
        self._rated_suction_abs = self.cfg.suction_set_barg+PATM_BAR
        self.suction_barg = 0.0; self.flow_kg_h = 0.0; self.step_avg_kg_h = 0.0
        self.choked = False; self.bypass_kg = 0.0; self.compressed_kg = 0.0
        self.reference_kwh = 0.0; self.compressor_run_h = 0.0
        self.initial_mass = self.tt.mass_kg+sum(b.mass_kg for b in self.banks.values())
        self.initial_bank_mass = {k:b.mass_kg for k,b in self.banks.items()}
        self.peak_pressure = {k:b.pg for k,b in self.banks.items()}
        self.events_log = []
        self.history = deque(maxlen=30000)
        self._tempdir = tempfile.TemporaryDirectory(prefix='h2tt_')
        self._journal_path = Path(self._tempdir.name)/'trend.csv'
        self._journal = self._journal_path.open('w',newline='',encoding='utf-8-sig')
        self._writer = None; self._sample_count = 0
        self._last_sample = -math.inf
        self._base = {k:0.0 for k in self.valves}; self._base['COMPRESSOR'] = 0.0
        self._operator = {}; self._scheduled = {}
        self._due = sorted(copy.deepcopy(self.cfg.events),key=lambda e:float(e['time_s']))
        self._event_index = 0
        self.rule_states = {r.name: RuleState() for r in self.cfg.rules}
        self._queue = []; self._queue_index = 0
        self._eq_since = None; self._stage_start = 0.0
        self._stopping = None; self.stop_history = []
        self._starved_age = 0.0; self._mismatch_since = {}
        self.tripped = False; self.last_notice = 'READY'
        self._last_block = ''; self._busy_rule = False
        self.log('INIT',f'v0.2.0 / {len(self.banks)} banks / {self.eos.name} / {self.cfg.shutdown_mode}')
        self.record(force=True)

    def close(self):
        if getattr(self,'_journal',None) and not self._journal.closed:
            self._journal.close()
        if getattr(self,'_tempdir',None): self._tempdir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except (OSError, AttributeError):
            pass

    def log(self, code, message, **detail):
        self.last_notice = message
        self.events_log.append({'time_s':round(self.time_s,6),'code':code,'message':message,
                                'detail':json.dumps(detail,ensure_ascii=False) if detail else ''})

    def active_bank(self):
        opened = [n for n in self.banks if self.valves['BANK-'+n].opening > EPS]
        return opened[0] if len(opened) == 1 else None

    def _ensure_commandable(self):
        if self.tripped: raise ValueError('TRIP 래치: 원인 해소·밸브 폐쇄 후 보호 Reset 필요')
        if self._stopping: raise ValueError('차단/절체 동작 중입니다. 완료 후 조작하세요.')

    def manual(self):
        self._ensure_commandable()
        self.mode = 'MANUAL'; self.phase = 'MANUAL'; self._queue = []; self._eq_since = None
        self._base = {k:v.command for k,v in self.valves.items()}
        self._base['COMPRESSOR'] = float(self.compressor_command)
        self.log('MODE','MANUAL / 재고·시각 유지')

    def set_valve(self, tag, pct, override=True):
        self.cfg.validate_action(Action('valve',tag,pct))
        self._ensure_commandable()
        if override and (self.mode != 'MANUAL' or self.phase in ('READY','HOLD','DONE')): self.manual()
        if tag == 'BYPASS' and float(pct)>0 and self.route != 'BYPASS':
            raise ValueError('BYPASS 경로에서만 해당 밸브를 열 수 있습니다.')
        if tag in ('PRV-102','COMP-OUT') and float(pct)>0 and self.route != 'RECOVERY':
            raise ValueError('RECOVERY 경로에서만 압축경로 밸브를 열 수 있습니다.')
        if tag.startswith('BANK-') and float(pct)>0:
            self.request_bank(tag[5:],float(pct)/100)
        else:
            self._operator[tag] = float(pct)/100
        if float(pct)==0 and self.compressor_speed>EPS and self.route=='RECOVERY' and tag != 'BYPASS':
            self.request_stop('HOLD',f'운전자 {tag} CLOSE: 구동 정지 후 격리')
        self.log('OPERATOR',f'{tag} → {float(pct):g}%')
        self._resolve_commands()

    def request_bank(self, name, opening=1.0, authority='operator'):
        self._ensure_commandable()
        if name not in self.banks or not 0 <= float(opening) <= 1:
            raise ValueError('Bank 이름/개도 오류')
        if self.active_bank() not in (None,name) and self.compressor_speed>EPS:
            self.request_stop('BANK','압축 운전 중 Bank 절체',next_bank=name)
            return
        self.selected_bank = name
        for n in self.banks:
            self._base['BANK-'+n] = 0.0
            if authority=='operator':
                self._operator['BANK-'+n] = 0.0
                self._scheduled.pop('BANK-'+n,None)
        self._base['BANK-'+name] = float(opening)
        if authority=='operator':self._operator['BANK-'+name]=float(opening)
        self.pending_bank = name
        self.log('BANK_REQUEST',name)
        self._resolve_commands()

    def set_route(self, route):
        self._ensure_commandable()
        self.cfg.validate_action(Action('route',value=route))
        if route == self.route: return
        self.request_stop('ROUTE',f'경로 절체 {self.route} → {route}',next_route=route)
        self._resolve_commands()

    def set_compressor(self, run):
        self._ensure_commandable()
        if bool(run) and (not self.cfg.compressor_enabled or self.route != 'RECOVERY'):
            raise ValueError('압축기를 사용한 RECOVERY 경로에서만 기동 가능합니다.')
        self._operator['COMPRESSOR'] = float(bool(run))
        if not run and self.compressor_speed>EPS:
            self.log('COMP_REQUEST','STOP / 등가 감속 적용')
        else: self.log('COMP_REQUEST','RUN' if run else 'STOP')
        self._resolve_commands()

    def open_route(self):
        self._ensure_commandable()
        self.request_bank(self.selected_bank)
        for tag in ('MOV-101','BYPASS') if self.route=='BYPASS' else ('MOV-101','PRV-102','COMP-OUT'):
            self._operator[tag]=1.0
        self._resolve_commands()

    def release_overrides(self, target='*', include_operator=True):
        keys = list(self._operator)+list(self._scheduled) if target=='*' else [target]
        for k in keys:
            if include_operator:self._operator.pop(k,None)
            self._scheduled.pop(k,None)
        self.log('RELEASE',f'수동/예약 출력 해제: {target}')
        self._resolve_commands()

    def start_auto(self, compress_only=False):
        self._ensure_commandable()
        kind = 'COMPRESS_ONLY' if compress_only else self.cfg.sequence_mode
        if kind != 'EQUALIZE_ONLY' and not self.cfg.compressor_enabled:
            raise ValueError('압축기 미사용 구성입니다.')
        self.mode='AUTO'; self._operator.clear(); self._scheduled.clear()
        self._queue=[]
        if kind != 'COMPRESS_ONLY': self._queue += [('EQ',n) for n in self.cfg.equalize_order]
        if kind != 'EQUALIZE_ONLY': self._queue += [('REC',n) for n in self.cfg.recovery_order]
        self._queue_index=0
        self.request_stop('NEXT','자동시퀀스 시작 전 닫힘 확인')
        self.log('AUTO',f'{kind}: ' + ' → '.join(k+'_'+n for k,n in self._queue))
        self._resolve_commands()

    def next_stage(self):
        self._ensure_commandable()
        if self.mode!='AUTO' or not self._queue: raise ValueError('AUTO 충전 단계에서만 NEXT 가능')
        self._queue_index+=1
        self.request_stop('NEXT','규칙/운전자에 의한 다음 단계')

    def _enter_stage(self):
        if self.tt.pg <= self.cfg.tt_stop_barg+EPS:
            self.phase='DONE'; self.log('COMPLETE','TT 종료압력 도달'); return
        while self._queue_index < len(self._queue):
            kind,n=self._queue[self._queue_index]; b=self.banks[n]
            skip = b.pg >= b.spec.target_barg-EPS
            if kind=='EQ': skip |= self.tt.pg-b.pg <= self.cfg.equalize_dp_bar
            if skip:
                self.log('STAGE_SKIP',f'{kind}_{n}: 압력/목표 조건')
                self._queue_index+=1; continue
            self._operator.clear(); self._scheduled.clear()
            self._base={k:0.0 for k in self.valves};self._base['COMPRESSOR']=float(kind=='REC')
            self.route='BYPASS' if kind=='EQ' else 'RECOVERY';self.selected_bank=n
            self._base['MOV-101']=1.0; self._base['BANK-'+n]=1.0
            if kind=='EQ':self._base['BYPASS']=1.0
            else:self._base['PRV-102']=1.0;self._base['COMP-OUT']=1.0
            self.phase=kind+'_'+n;self._eq_since=None;self._stage_start=self.time_s
            self.log('SEQUENCE',self.phase,target_barg=b.spec.target_barg)
            return
        self.phase='DONE';self.log('COMPLETE','선택된 충전 단계 완료')

    def request_stop(self, kind='HOLD', reason='Operator stop', **extra):
        # Severity cannot be downgraded by a lower-priority command.
        rank={'NEXT':1,'ROUTE':1,'BANK':1,'HOLD':2,'DONE':3,'TRIP':4}
        if self._stopping and rank[kind] <= rank[self._stopping['kind']]: return
        if self.phase=='TRIP' and kind=='TRIP': return
        initial = {'time_s':self.time_s,'tt_kg':self.tt.mass_kg,
                   'pressures':{n:b.pg for n,b in self.banks.items()}}
        self._stopping={'kind':kind,'reason':reason,'start':initial, **extra}
        self._operator.clear();self._scheduled.clear()
        self._drain={k:v.command for k,v in self.valves.items()}
        self.phase='TRIPPING' if kind=='TRIP' else 'SWITCH' if kind in ('NEXT','ROUTE','BANK') else 'STOPPING'
        self.tripped |= kind=='TRIP'
        self.log('STOP_REQUEST',reason,kind=kind,shutdown_mode=self.cfg.shutdown_mode,**initial)
        self._base={k:0.0 for k in self.valves};self._base['COMPRESSOR']=0.0
        if self.cfg.shutdown_mode=='IDEAL':
            for v in self.valves.values(): v.command=0.0;v.opening=0.0;v.source='IDEAL STOP'
            self.compressor_speed=0.0;self.compressor_command=False
            self._finish_stop()
        else:
            self._finish_stop()  # already isolated: no artificial transition delay

    def _finish_stop(self):
        if not self._stopping: return
        if self.compressor_speed>EPS or any(v.opening>EPS for v in self.valves.values()): return
        st=self._stopping;self._stopping=None
        residual=st['start']['tt_kg']-self.tt.mass_kg
        row={'reason':st['reason'],'kind':st['kind'],'request_time_s':st['start']['time_s'],
             'complete_time_s':self.time_s,'residual_inflow_kg':residual,
             'pressure_at_request':st['start']['pressures'],
             'pressure_at_complete':{n:b.pg for n,b in self.banks.items()}}
        self.stop_history.append(row);self.log('STOP_COMPLETE',st['reason'],**row)
        self.flow_kg_h=0.0;self._eq_since=None
        if st['kind']=='NEXT': self._enter_stage()
        elif st['kind']=='BANK':
            self.selected_bank=st['next_bank'];self.phase='MANUAL';self.mode='MANUAL'
        elif st['kind']=='ROUTE':
            self.route=st['next_route'];self.phase='MANUAL';self.mode='MANUAL'
        else: self.phase={'TRIP':'TRIP','DONE':'DONE','HOLD':'HOLD'}[st['kind']]

    def estop(self, message='Operator SIM ESD'):
        self.request_stop('TRIP',message);self._resolve_commands()
        if self.cfg.shutdown_mode=='IDEAL':self.flow_kg_h=0.0

    def reset_trip(self):
        if self._stopping or self.compressor_speed>EPS or any(v.opening>EPS for v in self.valves.values()):
            raise ValueError('밸브 폐쇄 및 압축기 정지 완료 후 Reset 가능')
        if any(b.pg >= self.cfg.hh(b.spec) for b in self.banks.values()):
            raise ValueError('Bank HH 원인이 유지되고 있습니다.')
        vals=self.values()
        for r in self.cfg.rules:
            if r.enabled and r.priority=='PROTECTION' and expression(r.conditions,r.combine,vals):
                raise ValueError(f'보호 규칙 원인 유지: {r.name}')
        self.tripped=False; self.mode='MANUAL';self.phase='READY'
        self._operator.clear();self._scheduled.clear()
        self._base={k:0.0 for k in self.valves};self._base['COMPRESSOR']=0.0
        self.log('TRIP_RESET','재고·시각 유지 / 재기동 명령 필요')

    def apply_setpoints(self, changes: dict, source='OPERATOR'):
        """Atomic live update: mass/time/history are not reinitialized."""
        new=copy.deepcopy(self.cfg);old_values={}
        allowed={'tt_stop_barg','bank_hh_barg','equalize_dp_bar','equalize_hold_s',
                 'suction_set_barg','suction_min_barg','actuator_timeout_s','no_flow_delay_s',
                 'manual_target_policy'}
        for key,value in changes.items():
            if key.startswith('bank.'):
                parts=key.split('.')
                if len(parts)!=3 or parts[2] not in ('target_barg','hh_barg'):
                    raise ValueError('운전 중 Bank 변경: target_barg / hh_barg만 허용')
                b=next((x for x in new.banks if x.name==parts[1]),None)
                if b is None: raise ValueError('Bank 이름 오류')
                old_values[key]=getattr(b,parts[2]);setattr(b,parts[2],float(value))
            elif key in allowed:
                old_values[key]=getattr(new,key);setattr(new,key,value if key=='manual_target_policy' else float(value))
            else: raise ValueError(f'운전 중 변경 불가: {key}. 설비 구성에서 초기화하여 적용하세요.')
        new.validate()
        self.cfg=new
        self.tt.spec=new.tt
        for b in new.banks:self.banks[b.name].spec=b
        self.log('SP_CHANGE',f'{source}: {len(changes)}개 운전값 변경',old=old_values,new=changes)
        self._eq_since=None
        self._evaluate_protection()
        self._resolve_commands()
        self.record(force=True)

    def values(self):
        b=self.banks[self.selected_bank]
        v={'time_s':self.time_s,'tt.pressure':self.tt.pg,'tt.mass':self.tt.mass_kg,
           'flow_kg_h':self.flow_kg_h,'suction.pressure':self.suction_barg,
           'selected.pressure':b.pg,'selected.target':b.spec.target_barg,'selected.dp':self.tt.pg-b.pg,
           'compressor.speed':self.compressor_speed*100,'mode':self.mode,'phase':self.phase,
           'route':self.route,'tripped':self.tripped,
           'setpoint.tt_stop_barg':self.cfg.tt_stop_barg,'setpoint.equalize_dp_bar':self.cfg.equalize_dp_bar,
           'setpoint.suction_set_barg':self.cfg.suction_set_barg}
        for n,b in self.banks.items():
            v.update({f'bank.{n}.pressure':b.pg,f'bank.{n}.mass':b.mass_kg,
                      f'bank.{n}.target':b.spec.target_barg,f'bank.{n}.hh':self.cfg.hh(b.spec),
                      f'bank.{n}.dp':self.tt.pg-b.pg})
        for tag,b in self.valves.items():
            v[f'valve.{tag}.opening']=b.opening*100;v[f'valve.{tag}.command']=b.command*100
        return v

    def replace_rules(self, rules):
        new=copy.deepcopy(self.cfg)
        new.rules=[RuleSpec.from_dict(r) if isinstance(r,dict) else copy.deepcopy(r) for r in rules]
        new.validate()
        for r in self.cfg.rules:
            if r.priority!='NORMAL' and self.rule_states[r.name].active:
                replacement=next((x for x in new.rules if x.name==r.name),None)
                if replacement != r: raise ValueError(f'활성 보호/인터록 {r.name}: 원인 해소 및 규칙 Reset 후 수정하세요.')
        states={r.name: self.rule_states[r.name] if r in self.cfg.rules else RuleState() for r in new.rules}
        self.cfg.rules=new.rules;self.rule_states=states
        self.log('RULE_EDIT',f'{len(rules)}개 규칙 적용 / 재고·시각 유지')
        self._resolve_commands()

    def reset_rule(self, name):
        r=next(r for r in self.cfg.rules if r.name==name)
        if expression(r.conditions,r.combine,self.values()):
            raise ValueError('발동조건이 유지되는 규칙은 Reset할 수 없습니다.')
        self.rule_states[name]=RuleState();self.log('RULE_RESET',name);self._resolve_commands()

    def _evaluate_rules(self):
        if not self.cfg.rules:return
        vals=self.values()
        for r in sorted(self.cfg.rules,key=lambda r:-PRIORITIES[r.priority]):
            state=self.rule_states[r.name]
            rising,falling=state.evaluate(r,vals,self.time_s)
            if rising:
                self.log('RULE_FIRE',r.name,values=state.trigger_values,hold_s=r.hold_s,priority=r.priority)
                for a in r.actions:
                    if a.action in ('valve','compressor'): continue
                    if self._stopping and a.action not in ('trip','stop'): continue
                    try:self._side_action(a, r.name)
                    except (ValueError,KeyError) as exc:self.log('RULE_REJECTED',f'{r.name}: {exc}')
            if falling:self.log('RULE_RELEASE',r.name)

    def _side_action(self,a,source):
        if a.action in ('bank','route','next') and self.mode=='MANUAL':
            raise ValueError('MANUAL 경로/Bank 선택은 운전자 우선; 해당 규칙은 AUTO에서 적용하세요.')
        if a.action=='trip':self.estop(f'{source}: TRIP')
        elif a.action=='stop':self.request_stop('HOLD',f'{source}: STOP')
        elif a.action=='next':self.next_stage()
        elif a.action=='bank':self.request_bank(str(a.value),authority='automatic')
        elif a.action=='route':self.set_route(str(a.value))
        elif a.action=='release':self.release_overrides(a.target,include_operator=False)

    def _resolve_commands(self):
        req={k:[Request(self._base.get(k,0),20,'SEQUENCE' if self.mode=='AUTO' else 'MANUAL BASE')] for k in (*self.valves,'COMPRESSOR')}
        for outputs,pri,src in ((self._scheduled,40,'SCHEDULE'),(self._operator,60,'OPERATOR')):
            for k,v in outputs.items():req[k].append(Request(v,pri,src))
        for r in self.cfg.rules:
            if not r.enabled or not self.rule_states[r.name].active: continue
            for a in r.actions:
                if a.action in ('valve','compressor'):
                    key=a.target if a.action=='valve' else 'COMPRESSOR'
                    value=float(a.value)/100 if a.action=='valve' else float(a.value)
                    req[key].append(Request(value,PRIORITIES[r.priority],'RULE:'+r.name))
        winners={k:arbitrate(v) for k,v in req.items()}
        # Normal shutdown: keep current recovery path while equivalent drive ramps down.
        # Trip: command all closed immediately but honor finite travel in DELAYED mode.
        if self._stopping:
            drain=(self._stopping['kind']!='TRIP' and self.route=='RECOVERY' and self.compressor_speed>EPS)
            for k in self.valves:
                val=self._drain.get(k,0.0) if drain else 0.0
                if drain:
                    # Never override an active protective close request.
                    protective=[x for x in req[k] if x.priority>=70 and x.value<=EPS]
                    if protective:val=0.0
                winners[k]=Request(val,100,'STOP:'+self._stopping['reason'])
            winners['COMPRESSOR']=Request(0,100,'STOP:'+self._stopping['reason'])
        elif self.phase in ('DONE','HOLD','TRIP') or self.tripped:
            for k in winners:winners[k]=Request(0,100,'TERMINAL:'+self.phase)
        else:
            # Break-before-make bank selection; no parallel Bank flows in this template.
            open_requests=[n for n in self.banks if winners['BANK-'+n].value>EPS]
            if open_requests:
                best=max(winners['BANK-'+n].priority for n in open_requests)
                candidates=[n for n in open_requests if winners['BANK-'+n].priority==best]
                if len(candidates)>1:
                    for n in self.banks:winners['BANK-'+n]=Request(0,90,'INTERLOCK: conflicting Bank requests')
                else:
                    desired=candidates[0];others=[n for n in self.banks if n!=desired]
                    for n in others:winners['BANK-'+n]=Request(0,90,'INTERLOCK: one Bank')
                    if any(self.valves['BANK-'+n].opening>EPS for n in others):
                        winners['BANK-'+desired]=Request(0,90,'INTERLOCK: wait other Bank CLOSED')
                        self.pending_bank=desired
                    else:
                        self.selected_bank=desired;self.pending_bank=None
            if self.route=='BYPASS':
                for k in ('PRV-102','COMP-OUT','COMPRESSOR'):winners[k]=Request(0,90,'INTERLOCK: BYPASS route')
                if any(self.valves[k].opening>EPS for k in ('PRV-102','COMP-OUT')):
                    winners['BYPASS']=Request(0,90,'INTERLOCK: recovery path closing')
            else:
                winners['BYPASS']=Request(0,90,'INTERLOCK: RECOVERY route')
                if self.valves['BYPASS'].opening>EPS:
                    for k in ('PRV-102','COMP-OUT','COMPRESSOR'):winners[k]=Request(0,90,'INTERLOCK: bypass closing')
            if winners['COMPRESSOR'].value>EPS and self.compressor_speed<=EPS:
                required=['MOV-101','PRV-102','COMP-OUT','BANK-'+self.selected_bank]
                if self.route!='RECOVERY' or any(self.valves[k].opening<.95 or winners[k].value<=EPS for k in required):
                    winners['COMPRESSOR']=Request(0,90,'INTERLOCK: start path not open')
        for k,v in self.valves.items():
            w=winners[k]
            if abs(v.command-w.value)>EPS or v.source!=w.source:
                if abs(v.command-w.value)>EPS:self.log('VALVE_CMD',f'{k}: {w.value*100:g}% / {w.source}',tag=k,source=w.source)
                v.command=w.value;v.source=w.source
        w=winners['COMPRESSOR'];self.compressor_command=bool(w.value>EPS);self.compressor_source=w.source

    def _evaluate_protection(self):
        for n,b in self.banks.items():
            if b.pg>=self.cfg.hh(b.spec)-EPS:
                self.request_stop('TRIP',f'Bank {n} HH: {b.pg:.4f} ≥ {self.cfg.hh(b.spec):g} barg')
        if self._stopping or self.phase in ('DONE','TRIP','HOLD'): return
        if self.tt.pg<=self.cfg.tt_stop_barg+EPS:
            self.request_stop('DONE',f'TT {self.tt.pg:.4f} ≤ SP {self.cfg.tt_stop_barg:g} barg');return
        b=self.banks[self.selected_bank]
        if self.mode=='AUTO' and self.phase.startswith(('EQ_','REC_')):
            target=b.pg>=b.spec.target_barg-EPS
            eq=self.phase.startswith('EQ_') and self.tt.pg-b.pg<=self.cfg.equalize_dp_bar
            self._eq_since=(self.time_s if self._eq_since is None else self._eq_since) if eq else None
            eq_done=self._eq_since is not None and self.time_s-self._eq_since>=self.cfg.equalize_hold_s-EPS
            if target or eq_done:
                reason=(f'{self.phase}: P={b.pg:.4f} / SP={b.spec.target_barg:g}' if target else
                        f'{self.phase}: ΔP={self.tt.pg-b.pg:.4f} ≤ {self.cfg.equalize_dp_bar:g} / {self.cfg.equalize_hold_s:g}s')
                self._queue_index+=1;self.request_stop('NEXT',reason)
        elif self.mode=='MANUAL' and self.cfg.manual_target_policy=='ALL_MODES' and self.active_bank() and b.pg>=b.spec.target_barg-EPS:
            self.request_stop('HOLD',f'MANUAL target: {self.selected_bank} {b.spec.target_barg:g} barg')

    def schedule(self,event):
        temp=copy.deepcopy(self.cfg);temp.events=[event];temp.validate()
        if float(event['time_s'])<self.time_s-EPS:raise ValueError('현재 모사시각 이후 이벤트만 추가 가능')
        self.cfg.events.append(copy.deepcopy(event))
        self._due=self._due[self._event_index:]+[copy.deepcopy(event)]
        self._due.sort(key=lambda e:float(e['time_s']));self._event_index=0
        self.log('EVENT_ADD',str(event))

    def _run_events(self):
        while self._event_index<len(self._due) and float(self._due[self._event_index]['time_s'])<=self.time_s+EPS:
            e=self._due[self._event_index];self._event_index+=1
            a=e['action'];value=e.get('value');operator=e.get('authority','operator')=='operator'
            try:
                if a=='estop':self.estop('Scheduled SIM ESD')
                elif a=='stop':self.request_stop('HOLD','Scheduled STOP')
                elif a=='auto':self.start_auto(bool(value))
                elif a=='manual':self.manual()
                elif a=='release':self.release_overrides(e.get('target','*'),include_operator=operator)
                else:
                    self._ensure_commandable()
                    if operator and self.mode!='MANUAL':self.manual()
                    if a=='valve':
                        if operator:self.set_valve(e['target'],float(value))
                        else:self._scheduled[e['target']]=float(value)/100
                    elif a=='compressor':
                        if operator:self.set_compressor(bool(value))
                        else:self._scheduled['COMPRESSOR']=float(bool(value))
                    elif a=='bank':self.request_bank(str(value),authority='operator' if operator else 'automatic')
                    elif a=='route':self.set_route(str(value))
                self.log('EVENT',str(e))
            except (ValueError,KeyError,TypeError) as exc:self.log('EVENT_REJECTED',f'{e}: {exc}')
    def _flow(self,positions=None,tt_mass=None,bank_mass=None):
        c=self.cfg
        name=self.active_bank() if positions is None else self.selected_bank
        if name is None:
            return 0.0,False,0.0
        bank=self.banks[name]
        p1=self.tt.pabs if tt_mass is None else self.tt.pressure_at(tt_mass)
        p2=bank.pabs if bank_mass is None else bank.pressure_at(bank_mass)
        t=self.tt.spec.temperature_c+273.15
        def pos(tag):
            return self.valves[tag].opening if positions is None else positions[tag]
        def cv(tag):
            return self.valves[tag].effective_cv(None if positions is None else positions[tag])
        if self.route=='BYPASS':
            if pos('PRV-102')>1e-7 or pos('COMP-OUT')>1e-7:
                return 0.0,False,0.0
            f=series_flow(p1,p2,t,[cv('MOV-101'),cv('BYPASS'),cv('BANK-'+name)],self.eos,c.k,c.xt,c.fp)
            return f.kg_h,f.choked,0.0
        if pos('BYPASS')>1e-7 or self.compressor_speed<=1e-8:
            return 0.0,False,0.0
        if min(cv('MOV-101'),cv('PRV-102'),cv('COMP-OUT'),cv('BANK-'+name))<=1e-12:
            return 0.0,False,0.0
        pref=c.suction_set_barg+PATM_BAR
        tcomp=c.compressor_temperature_c+273.15
        rhoref=self.eos.density(self._rated_suction_abs,tcomp)
        psmax=min(pref,p1)
        def disp(ps):
            return c.compressor_kg_h*self.compressor_speed*self.eos.density(ps,tcomp)/rhoref
        def available(ps):
            return series_flow(p1,ps,t,[cv('MOV-101'),cv('PRV-102')],self.eos,c.k,c.xt,c.fp)
        supply=available(psmax)
        if supply.kg_h>=disp(psmax):
            ps=psmax; w=disp(ps)
        else:
            lo=PATM_BAR*.05; hi=psmax
            for _ in range(17):
                mid=(lo+hi)*.5
                if available(mid).kg_h>=disp(mid): lo=mid
                else: hi=mid
            ps=lo; w=disp(ps)
            supply=available(ps)
        # Finite discharge valve capacity at the compressor maximum-pressure boundary.
        fout=series_flow(c.compressor_max_barg+PATM_BAR,p2,c.aftercooler_temperature_c+273.15,
                         [cv('COMP-OUT'),cv('BANK-'+name)],self.eos,c.k,c.xt,c.fp)
        if ps-PATM_BAR<c.suction_min_barg:
            return 0.0,supply.choked,ps-PATM_BAR
        return min(w,fout.kg_h),supply.choked or (fout.choked and fout.kg_h<=w*(1+1e-6)),ps-PATM_BAR

    def _equilibrium_transfer(self,bank,upper):
        lo=0.0; hi=upper
        for _ in range(38):
            dm=(lo+hi)*.5
            if self.tt.pressure_at(self.tt.mass_kg-dm)>bank.pressure_at(bank.mass_kg+dm):lo=dm
            else:hi=dm
        return lo

    def step(self, dt=None):
        dt=self.cfg.dt_s if dt is None else float(dt)
        if not finite(dt) or not 0<dt<=2:raise ValueError('Step: 0 초과 2초 이하')
        self._run_events()
        if self._event_index<len(self._due):
            until=float(self._due[self._event_index]['time_s'])-self.time_s
            if EPS<until<dt:dt=until
        self._finish_stop()
        self._evaluate_protection()
        self._evaluate_rules()
        self._resolve_commands()
        positions={k:v.position_after(dt/2) for k,v in self.valves.items()}
        opened=[n for n in self.banks if positions['BANK-'+n]>EPS]
        if len(opened)>1:
            self.estop('Model integrity: simultaneous Bank opening')
            raise ArithmeticError('단일 Bank 인터록 위반: 실제 개도 상태를 확인하세요.')
        if opened:self.selected_bank=opened[0]
        start_speed=self.compressor_speed
        ramp_s=self.cfg.compressor_ramp_s if self.compressor_command else self.cfg.compressor_stop_s
        end_speed=(1.0 if self.compressor_command else 0.0) if ramp_s<=0 else min(1.0,start_speed+dt/ramp_s) if self.compressor_command else max(0.0,start_speed-dt/ramp_s)
        self.compressor_speed=(start_speed+end_speed)/2
        w0,ch,ps=self._flow(positions)
        dm=0.0
        if opened and w0>0:
            b=self.banks[opened[0]]
            floor=self.cfg.tt_stop_barg if self.cfg.shutdown_mode=='IDEAL' else 0.0
            remaining=max(0.0,self.tt.mass_kg-self.tt.mass_at_pg(floor))
            upper=min(w0*dt/3600,remaining)
            w1,_,_=self._flow(positions,self.tt.mass_kg-upper,b.mass_kg+upper)
            dm=min(.5*(w0+w1)*dt/3600,remaining)
            if self.route=='BYPASS' and self.tt.pressure_at(self.tt.mass_kg-dm)<b.pressure_at(b.mass_kg+dm):
                dm=self._equilibrium_transfer(b,dm)
            if self.cfg.shutdown_mode=='IDEAL':
                enforce=self.mode=='AUTO' or self.cfg.manual_target_policy=='ALL_MODES'
                ceiling=b.spec.target_barg if enforce else self.cfg.hh(b.spec)
                dm=min(dm,max(0.0,b.mass_at_pg(ceiling)-b.mass_kg))
            self.tt.mass_kg-=dm;b.mass_kg+=dm
            if self.route=='BYPASS':self.bypass_kg+=dm
            else:self.compressed_kg+=dm
        for tag,v in self.valves.items():
            was=v.opening;v.opening=v.position_after(dt)
            if abs(was-v.command)>EPS and abs(v.opening-v.command)<=EPS:
                self.log('VALVE_REACHED',f'{tag}: {v.opening*100:g}% / {v.source}',
                         completion_time_s=self.time_s+dt)
            if abs(v.opening-v.command)>EPS:
                self._mismatch_since.setdefault(tag,self.time_s)
            else:self._mismatch_since.pop(tag,None)
        self.compressor_speed=end_speed
        self.step_avg_kg_h=dm/dt*3600
        self.choked=ch;self.suction_barg=ps
        if start_speed>EPS or end_speed>EPS:
            self.compressor_run_h+=dt/3600
            self.reference_kwh+=self.cfg.compressor_reference_kw*(start_speed+end_speed)/2*dt/3600
        if self.compressor_command and end_speed>=.999 and w0<1e-8 and not self._stopping:
            self._starved_age+=dt
            if self._starved_age>=self.cfg.no_flow_delay_s:
                self.request_stop('HOLD','Compressor no-flow timeout')
        else:self._starved_age=0.0
        self.time_s+=dt
        for n,b in self.banks.items():
            self.peak_pressure[n]=max(self.peak_pressure[n],b.pg)
            if b.pg>self.cfg.numerical_max_barg+1e-6:
                self.estop(f'계산 유효범위 초과: {n}')
                raise ArithmeticError(f'{n} 압력 {b.pg:.3f} barg: 모델 상한 초과')
        for tag,since in list(self._mismatch_since.items()):
            if self.time_s-since>self.cfg.actuator_timeout_s:
                self.request_stop('TRIP',f'Actuator timeout: {tag}')
        if abs(self.mass_error_kg)>1e-6:raise ArithmeticError(f'질량수지 오차 {self.mass_error_kg:g} kg')
        self.flow_kg_h,self.choked,self.suction_barg=self._flow()
        self._evaluate_protection()
        self._evaluate_rules()
        self._resolve_commands()
        self._finish_stop()
        self.flow_kg_h,self.choked,self.suction_barg=self._flow()
        self.record()
        return dt

    @property
    def mass_error_kg(self):
        return self.tt.mass_kg+sum(b.mass_kg for b in self.banks.values())-self.initial_mass

    @property
    def settled(self):
        return self.phase in ('DONE','TRIP','HOLD') and self._stopping is None

    def snapshot(self):
        row={'time_s':self.time_s,'mode':self.mode,'phase':self.phase,'route':self.route,
             'eos':self.eos.name,'shutdown_mode':self.cfg.shutdown_mode,'tripped':int(self.tripped),
             'tt_barg':self.tt.pg,'tt_kg':self.tt.mass_kg,'tt_stop_barg':self.cfg.tt_stop_barg,
             'flow_kg_h':self.flow_kg_h,'step_avg_kg_h':self.step_avg_kg_h,
             'flow_Nm3_h':normal_flow(self.flow_kg_h),
             'flow_at_TT_m3_h':self.flow_kg_h/(self.tt.mass_kg/self.tt.spec.volume_m3),
             'bypass_kg':self.bypass_kg,'compressed_kg':self.compressed_kg,
             'total_transferred_kg':self.bypass_kg+self.compressed_kg,
             'suction_barg':self.suction_barg,'compressor_speed_pct':self.compressor_speed*100,
             'compressor_command':int(self.compressor_command),'compressor_source':self.compressor_source,
             'compressor_run_h':self.compressor_run_h,'reference_kWh':self.reference_kwh,
             'mass_error_kg':self.mass_error_kg,'choked':int(self.choked),'selected_bank':self.selected_bank}
        for n,b in self.banks.items():
            row[f'bank_{n}_barg']=b.pg;row[f'bank_{n}_kg']=b.mass_kg
            row[f'bank_{n}_in_kg_h']=self.flow_kg_h if n==self.active_bank() else 0.0
            row[f'bank_{n}_received_kg']=b.mass_kg-self.initial_bank_mass[n]
            row[f'bank_{n}_target_barg']=b.spec.target_barg
            row[f'bank_{n}_hh_barg']=self.cfg.hh(b.spec)
            row[f'bank_{n}_peak_barg']=self.peak_pressure[n]
        for k,v in self.valves.items():
            row[k+'_command_pct']=v.command*100;row[k+'_actual_pct']=v.opening*100
            row[k+'_source']=v.source
        row['active_rules']=';'.join(n for n,state in self.rule_states.items() if state.active)
        row['rule_status_json']=json.dumps({n:{'active':s.active,'fires':s.fired_count} for n,s in self.rule_states.items()},ensure_ascii=False)
        return row

    def record(self, force=False):
        if force or self.time_s-self._last_sample>=self.cfg.sample_s-EPS:
            row=self.snapshot();self.history.append(row);self._last_sample=self.time_s
            if self._writer is None:
                self._writer=csv.DictWriter(self._journal,fieldnames=list(row));self._writer.writeheader()
            self._writer.writerow(row);self._sample_count+=1

    def export(self, folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        self.record(force=True);self._journal.flush()
        shutil.copyfile(self._journal_path,folder/'trend.csv')
        with (folder/'events.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['time_s','code','message','detail']);w.writeheader();w.writerows(self.events_log)
        self.cfg.save(folder/'config.json')
        (folder/'initial_config.json').write_text(json.dumps(self.initial_config,ensure_ascii=False,indent=2),encoding='utf-8')
        summary=self.snapshot();summary['stop_history']=self.stop_history;summary['samples_saved']=self._sample_count
        summary['notes']='등온/단일Bank/준정상Cv. 정지 지연 포함; 배관재고·실제PID·온도·PSV·실제동력 제외.'
        (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        return folder


# ===== h2sim/editors.py =====
"""Tk table editors for model structure, live SPs and data-only condition rules."""
import copy
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, messagebox


def table(parent, columns, height=6):
    frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,pady=5)
    tree=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',height=height)
    for name,label,width in columns:
        tree.heading(name,text=label);tree.column(name,width=width,anchor='center',minwidth=55)
    sy=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
    sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
    tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
    frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
    return tree


def fields(parent, definitions):
    """definitions=(key,label,value,choices_or_None)"""
    result={}
    for row,(key,label,value,choices) in enumerate(definitions):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=8,pady=5)
        v=tk.StringVar(value='' if value is None else str(value));result[key]=v
        widget=ttk.Combobox(parent,textvariable=v,values=choices,state='readonly',width=25) if choices else ttk.Entry(parent,textvariable=v,width=28)
        widget.grid(row=row,column=1,sticky='ew',padx=8,pady=5)
    return result


class ItemEditor(tk.Toplevel):
    def __init__(self,parent,title,definitions,callback):
        super().__init__(parent);self.title(title);self.transient(parent)
        f=ttk.Frame(self,padding=15);f.pack(fill='both',expand=True)
        self.vars=fields(f,definitions)
        def save():
            try:callback({k:v.get() for k,v in self.vars.items()});self.destroy()
            except (ValueError,TypeError,KeyError) as exc:messagebox.showerror('입력 확인',str(exc),parent=self)
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Button(row,text='적용',command=save).pack(side='right');ttk.Button(row,text='취소',command=self.destroy).pack(side='right',padx=6)
        self.grab_set()


class SetpointEditor(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.title('운전 SP 변경 — 재고·시간·이력 유지');self.geometry('830x650');self.transient(app)
        self.app=app;self.vars={}
        ttk.Label(self,text='운전 중 변경 가능 | 용적·초기압력·Cv는 설비 구성에서 수정',font=(app.face,12,'bold')).pack(anchor='w',padx=15,pady=12)
        nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=12)
        f=ttk.Frame(nb,padding=10);nb.add(f,text='공통 운전값')
        definitions=[]
        for key,label in [('tt_stop_barg','TT 종료압력 [barg]'),('bank_hh_barg','기본 Bank HH [barg]'),
                          ('equalize_dp_bar','균압 종료차압 [bar]'),('equalize_hold_s','균압 확인시간 [s]'),
                          ('suction_set_barg','이상 Regulator 흡입 SP [barg]'),('suction_min_barg','최소흡입압력 [barg]'),
                          ('actuator_timeout_s','밸브 동작 Timeout [s]'),('no_flow_delay_s','압축기 무유량 지연 [s]')]:
            definitions.append((key,label,getattr(app.sim.cfg,key),None))
        definitions.append(('manual_target_policy','수동운전 목표압력 정책',app.sim.cfg.manual_target_policy,['AUTO_ONLY','ALL_MODES']))
        self.vars.update(fields(f,definitions))
        b=ttk.Frame(nb,padding=8);nb.add(b,text='Bank 목표 / HH')
        canvas=tk.Canvas(b,highlightthickness=0);scroll=ttk.Scrollbar(b,command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        inner=ttk.Frame(canvas);canvas.create_window(0,0,window=inner,anchor='nw')
        inner.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        for j,text in enumerate(['Bank','현재압력 barg','목표압력 barg','HH barg (개별값)']):ttk.Label(inner,text=text).grid(row=0,column=j,padx=10,pady=8)
        for i,(name,vessel) in enumerate(app.sim.banks.items(),1):
            ttk.Label(inner,text=name).grid(row=i,column=0,padx=10)
            ttk.Label(inner,text=f'{vessel.pg:.3f}').grid(row=i,column=1,padx=10)
            for j,attr,value in [(2,'target_barg',vessel.spec.target_barg),(3,'hh_barg',app.sim.cfg.hh(vessel.spec))]:
                v=tk.StringVar(value=str(value));self.vars[f'bank.{name}.{attr}']=v
                ttk.Entry(inner,textvariable=v,width=17).grid(row=i,column=j,padx=8,pady=6)
        ttk.Label(self,text='목표압력보다 현재압력이 높으면 해당 운전정책에 따라 종료 절차가 시작됩니다.\n보호 HH는 제조사 설정값이 아닌 모의실험 입력입니다.',foreground='#617789').pack(anchor='w',padx=15,pady=10)
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Button(row,text='상태 유지하고 적용',command=self.apply).pack(side='right');ttk.Button(row,text='닫기',command=self.destroy).pack(side='right',padx=6)

    def apply(self):
        try:
            changes={k:(v.get() if k=='manual_target_policy' else float(v.get())) for k,v in self.vars.items()}
            self.app.sim.apply_setpoints(changes);self.app.refresh();self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('적용 실패 — 기존 상태 유지',str(exc),parent=self)


class ConfigEditor(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.cfg=copy.deepcopy(app.sim.cfg)
        self.title('설비 구성 / 계산조건 — 적용 시 새 실험');self.geometry('1050x750');self.transient(app)
        self.vars={};nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=10,pady=10)
        bank=ttk.Frame(nb,padding=10);nb.add(bank,text='TT / Bank 구성')
        self.ttvars=fields(bank,[('volume_m3','TT 내용적 [m³]',self.cfg.tt.volume_m3,None),('pressure_barg','TT 초기압력 [barg]',self.cfg.tt.pressure_barg,None),('temperature_c','TT 등온온도 [°C]',self.cfg.tt.temperature_c,None)])
        # fields use grid, so put them in a separate nested frame.
        bar=ttk.Frame(bank);bar.grid(row=3,column=0,columnspan=2,sticky='ew',pady=8)
        for text,fn in [('Bank 추가',lambda:self.bank_edit()),('선택 Bank 수정',lambda:self.bank_edit(True)),('선택 Bank 삭제',self.bank_delete)]:
            ttk.Button(bar,text=text,command=fn).pack(side='left',padx=4)
        area=ttk.Frame(bank);area.grid(row=4,column=0,columnspan=2,sticky='nsew');bank.rowconfigure(4,weight=1);bank.columnconfigure(1,weight=1)
        self.banktree=table(area,[('name','이름',90),('v','내용적 m³',110),('p','초기 barg',110),('t','온도 °C',100),('target','목표 barg',110),('hh','HH barg',100)])
        self.banktree.bind('<Double-1>',lambda e:self.bank_edit(True))
        valves=ttk.Frame(nb,padding=10);nb.add(valves,text='밸브 Cv / 응답')
        ttk.Button(valves,text='선택 밸브 수정',command=self.valve_edit).pack(anchor='w')
        self.valvetree=table(valves,[('tag','Tag',150),('cv','정격 Cv',95),('open','개방 전행정 s',120),('close','폐쇄 전행정 s',120),('char','특성',160),('R','Rangeability',100)],10)
        self.valvetree.bind('<Double-1>',lambda e:self.valve_edit())
        ttk.Label(valves,text='Close가 공란이면 개방시간과 동일. Equal-%는 0%에서 Cv=0으로 정규화한 등가특성.\nPRV 개도는 이상 Regulator의 가용 Cv 상한이며 PID 출력이 아닙니다.',foreground='#617789').pack(anchor='w',pady=10)
        for tabname,defs in [
            ('압축기 / 물성',[
                ('eos','물성모델',self.cfg.eos,['Leachman','PR','Ideal','CoolProp']),
                ('compressor_enabled','압축기 사용',str(self.cfg.compressor_enabled),['True','False']),
                ('compressor_kg_h','정격 처리유량 kg/h',self.cfg.compressor_kg_h,None),
                ('compressor_max_barg','토출 상한 barg',self.cfg.compressor_max_barg,None),
                ('compressor_ramp_s','기동 ramp s',self.cfg.compressor_ramp_s,None),
                ('compressor_stop_s','정지 ramp s',self.cfg.compressor_stop_s,None),
                ('compressor_reference_kw','참조 전력 kW',self.cfg.compressor_reference_kw,None),
                ('compressor_temperature_c','흡입 온도 °C',self.cfg.compressor_temperature_c,None),
                ('aftercooler_temperature_c','냉각 후 온도 °C',self.cfg.aftercooler_temperature_c,None),
                ('k','비열비 k',self.cfg.k,None),('xt','공통 xT',self.cfg.xt,None),('fp','공통 Fp',self.cfg.fp,None)]),
            ('시퀀스 / 차단',[
                ('sequence_mode','자동 시퀀스',self.cfg.sequence_mode,['EQ_RECOVERY','EQUALIZE_ONLY','COMPRESS_ONLY']),
                ('equalize_order','균압순서 (쉼표 구분)',','.join(self.cfg.equalize_order),None),
                ('recovery_order','압축순서 (쉼표 구분)',','.join(self.cfg.recovery_order),None),
                ('shutdown_mode','종료 응답모드',self.cfg.shutdown_mode,['DELAYED','IDEAL']),
                ('dt_s','적분간격 s',self.cfg.dt_s,None),('sample_s','기록간격 s',self.cfg.sample_s,None),
                ('tt_stop_barg','TT 종료압력 barg',self.cfg.tt_stop_barg,None),
                ('bank_hh_barg','기본 Bank HH barg',self.cfg.bank_hh_barg,None),
                ('equalize_dp_bar','균압 종료차압 bar',self.cfg.equalize_dp_bar,None),
                ('equalize_hold_s','균압 확인시간 s',self.cfg.equalize_hold_s,None),
                ('suction_set_barg','흡입 SP barg',self.cfg.suction_set_barg,None),
                ('suction_min_barg','최소흡입 barg',self.cfg.suction_min_barg,None)])]:
            f=ttk.Frame(nb,padding=10);nb.add(f,text=tabname);self.vars.update(fields(f,defs))
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Label(row,text='1 TT + 1~32 Bank / 고정 2경로 템플릿. 구성 변경은 초기화, 운전 SP 변경은 별도 메뉴.').pack(side='left')
        ttk.Button(row,text='새 실험으로 적용',command=self.apply).pack(side='right')
        self.refresh_tables()

    def refresh_tables(self):
        for tree in (self.banktree,self.valvetree):tree.delete(*tree.get_children())
        for b in self.cfg.banks:self.banktree.insert('','end',iid=b.name,values=(b.name,b.volume_m3,b.pressure_barg,b.temperature_c,b.target_barg,self.cfg.hh(b)))
        for tag,v in self.cfg.valves.items():self.valvetree.insert('','end',iid=tag,values=(tag,v.cv,v.stroke_s,v.close_s if v.close_s is not None else '동일',v.characteristic,v.rangeability))

    def bank_edit(self,edit=False):
        if edit and not self.banktree.selection():return
        old=next((b for b in self.cfg.banks if edit and b.name==self.banktree.selection()[0]),None)
        b=copy.deepcopy(old or VesselSpec(f'B{len(self.cfg.banks)+1}',5.0,20.0))
        defs=[('name','Bank 이름 (영문 시작)',b.name,None),('volume_m3','전체 내용적 m³',b.volume_m3,None),('pressure_barg','초기압력 barg',b.pressure_barg,None),('temperature_c','등온온도 °C',b.temperature_c,None),('target_barg','충전 목표압력 barg',b.target_barg,None),('hh_barg','HH barg',self.cfg.hh(b),None)]
        def save(d):
            if old and d['name']!=old.name:raise ValueError('기존 Bank 이름은 유지하세요. 이름 변경은 삭제 후 추가하세요.')
            if not old and any(x.name==d['name'] for x in self.cfg.banks):raise ValueError('Bank 이름 중복')
            new=VesselSpec(d['name'],*[float(d[k]) for k in ('volume_m3','pressure_barg','temperature_c','target_barg','hh_barg')])
            test=copy.deepcopy(self.cfg)
            if old:test.banks=[new if x.name==old.name else x for x in test.banks]
            else:
                test.banks.append(new);test.equalize_order.append(new.name);test.recovery_order.append(new.name)
            test.normalize().validate();self.cfg=test
            self.vars['equalize_order'].set(','.join(test.equalize_order));self.vars['recovery_order'].set(','.join(test.recovery_order))
            self.refresh_tables()
        ItemEditor(self,'Bank 사양',defs,save)

    def bank_delete(self):
        if not self.banktree.selection():return
        name=self.banktree.selection()[0]
        if len(self.cfg.banks)<=1:return messagebox.showerror('Bank','최소 1개 Bank 필요',parent=self)
        if not messagebox.askyesno('Bank 삭제',name+' 및 해당 충전밸브를 삭제합니까?',parent=self):return
        test=copy.deepcopy(self.cfg);test.banks=[b for b in test.banks if b.name!=name]
        test.valves.pop('BANK-'+name);test.equalize_order=[n for n in test.equalize_order if n!=name];test.recovery_order=[n for n in test.recovery_order if n!=name]
        if not test.recovery_order:test.recovery_order=[test.banks[0].name]
        try:test.validate()
        except ValueError as exc:return messagebox.showerror('참조자료 확인',str(exc)+'\n관련 조건규칙/예약이벤트부터 정리하세요.',parent=self)
        self.cfg=test;self.vars['equalize_order'].set(','.join(test.equalize_order));self.vars['recovery_order'].set(','.join(test.recovery_order));self.refresh_tables()

    def valve_edit(self):
        if not self.valvetree.selection():return
        tag=self.valvetree.selection()[0];v=self.cfg.valves[tag]
        defs=[('cv','정격 Cv',v.cv,None),('stroke_s','개방 전행정 s',v.stroke_s,None),('close_s','폐쇄 전행정 s (빈칸=동일)',v.close_s,None),('characteristic','특성',v.characteristic,['Linear','Equal percentage']),('rangeability','Rangeability',v.rangeability,None)]
        def save(d):
            nv=ValveSpec(float(d['cv']),float(d['stroke_s']),d['characteristic'],float(d['rangeability']),float(d['close_s']) if d['close_s'].strip() else None)
            test=copy.deepcopy(self.cfg);test.valves[tag]=nv;test.validate();self.cfg=test;self.refresh_tables()
        ItemEditor(self,tag,defs,save)

    def apply(self):
        try:
            for k,v in self.ttvars.items():setattr(self.cfg.tt,k,float(v.get()))
            for key,v in self.vars.items():
                value=v.get()
                if key in ('equalize_order','recovery_order'):value=[x.strip() for x in value.split(',') if x.strip()]
                elif key=='compressor_enabled':value=value=='True'
                elif key not in ('eos','sequence_mode','shutdown_mode'):value=float(value)
                setattr(self.cfg,key,value)
            self.cfg.validate()
            if self.app.sim.time_s>0 and not messagebox.askyesno('새 실험','현재 재고·시간·이력을 초기화합니다. 결과 저장을 완료했습니까?',parent=self):return
            self.app.install_config(self.cfg);self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('입력 확인',str(exc),parent=self)


class RuleEditor(tk.Toplevel):
    def __init__(self,app,rule=None):
        super().__init__(app);self.app=app;self.original=copy.deepcopy(rule)
        self.rule=copy.deepcopy(rule or RuleSpec('Rule_'+str(len(app.sim.cfg.rules)+1),
            [Condition('selected.pressure','>=',190)],[Action('stop')]))
        self.title('조건–동작 로직 편집');self.geometry('1130x790');self.transient(app)
        top=ttk.Frame(self,padding=10);top.pack(fill='x')
        self.vars={}
        defs=[('name','규칙 이름',self.rule.name,None),('mode','운전모드',self.rule.mode,['ALL','AUTO','MANUAL']),
              ('phase','적용 단계',self.rule.phase,['ANY','EQ','RECOVERY']),('priority','우선순위',self.rule.priority,['NORMAL','INTERLOCK','PROTECTION']),
              ('behavior','유지방식',self.rule.behavior,['WHILE','LATCH','ONCE']),('hold_s','발동 유지시간 s',self.rule.hold_s,None),
              ('reset_hold_s','복귀 유지시간 s',self.rule.reset_hold_s,None),('enabled','사용',str(self.rule.enabled),['True','False'])]
        for i,(k,label,value,choices) in enumerate(defs):
            row,col=divmod(i,4);f=ttk.Frame(top);f.grid(row=row,column=col,sticky='ew',padx=6,pady=4)
            ttk.Label(f,text=label).pack(anchor='w');v=tk.StringVar(value=str(value));self.vars[k]=v
            wid=ttk.Combobox(f,textvariable=v,values=choices,width=20,state='readonly') if choices else ttk.Entry(f,textvariable=v,width=22)
            wid.pack(fill='x')
        nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=12)
        self.condlists={'conditions':copy.deepcopy(self.rule.conditions),'reset_conditions':copy.deepcopy(self.rule.reset_conditions)}
        self.trees={};self.combines={}
        for key,title in [('conditions','발동조건'),('reset_conditions','복귀조건 / 히스테리시스')]:
            f=ttk.Frame(nb,padding=10);nb.add(f,text=title)
            row=ttk.Frame(f);row.pack(fill='x')
            cv=tk.StringVar(value=self.rule.combine if key=='conditions' else self.rule.reset_combine);self.combines[key]=cv
            ttk.Label(row,text='조건 결합').pack(side='left');ttk.Combobox(row,textvariable=cv,values=['ALL','ANY'],state='readonly',width=8).pack(side='left',padx=6)
            ttk.Label(row,text='ALL = AND / ANY = OR · 숫자압력은 barg, 차압은 bar, 개도는 %').pack(side='left',padx=10)
            self.trees[key]=table(f,[('var','감시변수',370),('op','비교',100),('value','설정값',170)],8)
            control=ttk.Frame(f);control.pack(fill='x',pady=5)
            for text,fn in [('조건 추가',lambda k=key:self.condition_edit(k)),('선택 수정',lambda k=key:self.condition_edit(k,True)),('선택 삭제',lambda k=key:self.condition_delete(k))]:ttk.Button(control,text=text,command=fn).pack(side='left',padx=4)
            self.trees[key].bind('<Double-1>',lambda e,k=key:self.condition_edit(k,True))
            ttk.Label(f,text='복귀조건이 있으면 발동 후 그 조건이 성립할 때까지 출력 유지.\n예: 120 barg 이상 CLOSE, 115 barg 이하 복귀. LATCH는 복귀조건 없이 수동 Reset도 가능.',foreground='#617789').pack(anchor='w',pady=10)
        a=ttk.Frame(nb,padding=10);nb.add(a,text='실행 동작')
        self.actions=copy.deepcopy(self.rule.actions)
        self.actiontree=table(a,[('action','동작',180),('target','대상',240),('value','값',190)],8)
        bar=ttk.Frame(a);bar.pack(fill='x')
        for text,fn in [('동작 추가',lambda:self.action_edit()),('선택 수정',lambda:self.action_edit(True)),('선택 삭제',self.action_delete)]:ttk.Button(bar,text=text,command=fn).pack(side='left',padx=4)
        ttk.Label(a,text='valve: 개도 0~100 / compressor: true 또는 false / bank: Bank 이름 / route: 경로\nnext: AUTO 다음 단계 / stop: 전체 정상정지 / trip: 모의 보호정지 / release: 수동·예약 출력 해제\n보호·인터록 우선순위는 CLOSE/STOP만 허용. 경로·Bank·NEXT 동작은 발동 시 1회 실행.',foreground='#617789').pack(anchor='w',pady=12)
        bot=ttk.Frame(self,padding=12);bot.pack(fill='x')
        ttk.Label(bot,text='WHILE: 조건 유지 · LATCH: 복귀/Reset까지 · ONCE: 1회 발동 후 Reset 전 재발동 금지',foreground='#617789').pack(side='left')
        ttk.Button(bot,text='상태 유지하고 적용',command=self.save).pack(side='right')
        self.refresh()

    def refresh(self):
        for key,tree in self.trees.items():
            tree.delete(*tree.get_children())
            for i,c in enumerate(self.condlists[key]):tree.insert('','end',iid=str(i),values=(c.variable,c.op,c.value))
        self.actiontree.delete(*self.actiontree.get_children())
        for i,a in enumerate(self.actions):self.actiontree.insert('','end',iid=str(i),values=(a.action,a.target,a.value))

    def condition_edit(self,key,edit=False):
        selected=self.trees[key].selection()
        if edit and not selected:return
        index=int(selected[0]) if edit else None
        c=self.condlists[key][index] if edit else Condition('selected.pressure','>=',190)
        def save(d):
            value=d['value'] if d['variable'] in ('mode','phase','route') else float(d['value'])
            nc=Condition(d['variable'],d['op'],value)
            if edit:self.condlists[key][index]=nc
            else:self.condlists[key].append(nc)
            self.refresh()
        ItemEditor(self,'조건 입력',[('variable','감시변수',c.variable,self.app.sim.cfg.variable_names()),('op','연산자',c.op,['>=','<=','>','<','==','!=']),('value','설정값',c.value,None)],save)

    def condition_delete(self,key):
        if self.trees[key].selection():del self.condlists[key][int(self.trees[key].selection()[0])];self.refresh()

    def action_edit(self,edit=False):
        selected=self.actiontree.selection()
        if edit and not selected:return
        index=int(selected[0]) if edit else None
        a=self.actions[index] if edit else Action('valve','BYPASS',0)
        def save(d):
            action=d['action'];v=d['value']
            if action=='valve':v=float(v)
            elif action=='compressor':
                if v.lower() not in ('true','false','1','0'):raise ValueError('true 또는 false 입력')
                v=v.lower() in ('true','1')
            na=Action(action,d['target'],v)
            if edit:self.actions[index]=na
            else:self.actions.append(na)
            self.refresh()
        ItemEditor(self,'동작 입력',[('action','동작 종류',a.action,['valve','compressor','bank','route','next','stop','trip','release']),('target','대상 (valve/release)',a.target,['',*self.app.sim.valves,'COMPRESSOR','*']),('value','값 (Bank/경로도 여기에 입력)',a.value,None)],save)

    def action_delete(self):
        if self.actiontree.selection():del self.actions[int(self.actiontree.selection()[0])];self.refresh()

    def save(self):
        try:
            d={k:v.get() for k,v in self.vars.items()};d['hold_s']=float(d['hold_s']);d['reset_hold_s']=float(d['reset_hold_s']);d['enabled']=d['enabled']=='True'
            d.update(conditions=self.condlists['conditions'],reset_conditions=self.condlists['reset_conditions'],actions=self.actions,combine=self.combines['conditions'].get(),reset_combine=self.combines['reset_conditions'].get())
            r=RuleSpec(**d)
            rules=[r if self.original and x.name==self.original.name else x for x in self.app.sim.cfg.rules]
            if not self.original:rules.append(r)
            self.app.sim.replace_rules(rules);self.app.refresh_rules();self.app.refresh();self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('규칙 확인',str(exc),parent=self)


# ===== h2sim/hmi.py =====
"""Read-only industrial HMI renderer. No model or control logic is implemented here.

Every indicated process value is read from Simulator/Config. The artwork is a
functional mimic, not additional physical equipment. Uses Tk only.
"""
import math
import time
import tkinter as tk

P = dict(bg='#E8EDF1', paper='#F8FAFC', panel='#F1F5F7', white='#FFFFFF',
         navy='#142B3D', ink='#203747', muted='#657B8C', border='#B7C7D2',
         steel='#91A5B4', pipe='#9AADB9', live='#168D78', green='#16815C',
         red='#B34848', amber='#BD7C24', blue='#2475B1', cyan='#31A9BD',
         display='#122D3B', digit='#96E6CA')


def blend(a, b, t):
    ar = [int(a[i:i+2], 16) for i in (1,3,5)]
    br = [int(b[i:i+2], 16) for i in (1,3,5)]
    return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x,y in zip(ar,br))


class Mimic(tk.Canvas):
    """Vector HMI: 1 TT / 1 compressor package / three banks per display page."""
    BASE_W=1540
    BASE_H=540

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, bg=P['paper'], highlightthickness=0, **kwargs)
        self.app=app
        self.page=0
        self.follow=True
        self._last_selected=None
        self._last_size=None
        self._hit_targets={}
        self.bind('<Configure>', lambda e:self.redraw())
        self.bind('<Leave>', lambda e:self.configure(cursor=''))
        self.bind('<Button-4>', lambda e:self.change_page(-1))
        self.bind('<Button-5>', lambda e:self.change_page(1))
        self.bind('<MouseWheel>',self._wheel)

    def _wheel(self,e):
        if len(self.app.sim.banks)>3:
            self.change_page(-1 if e.delta>0 else 1)

    def change_page(self, direction):
        count=max(1,math.ceil(len(self.app.sim.banks)/3))
        self.page=(self.page+direction)%count
        self.follow=False
        self.redraw()

    def follow_active(self):
        self.follow=True
        self.redraw()

    def coords_(self, values):
        return [self.ox+v*self.scale if i%2==0 else self.oy+v*self.scale
                for i,v in enumerate(values)]

    def rect(self,x,y,w,h,fill=None,outline=None,width=1,tag=None):
        return self.create_rectangle(*self.coords_((x,y,x+w,y+h)),fill=fill or '',
                outline=outline or '',width=width,tags=tag or ())

    def line(self,pts,color=None,width=1,dash=None,arrow=None,tag=None):
        return self.create_line(*self.coords_([v for xy in pts for v in xy]),
              fill=color or P['ink'],width=width*self.scale,dash=dash,
              arrow=arrow or 'none',arrowshape=(9*self.scale,11*self.scale,4*self.scale),
              capstyle='round',joinstyle='round',tags=tag or ())

    def text(self,x,y,value,size=10,color=None,bold=False,anchor='center',mono=False,width=None,tag=None):
        # Negative sizes are pixel fonts: stable across Windows display scaling.
        face=self.app.mono if mono else self.app.face
        return self.create_text(*self.coords_((x,y)),text=value,fill=color or P['ink'],
            anchor=anchor,font=(face,-max(9,round(size*self.scale)),'bold' if bold else 'normal'),
            width=0 if width is None else width*self.scale,tags=tag or ())

    def oval(self,x,y,w,h,fill=None,outline=None,width=1,tag=None):
        return self.create_oval(*self.coords_((x,y,x+w,y+h)),fill=fill or '',
            outline=outline or '',width=width*self.scale,tags=tag or ())

    def polygon(self,pts,fill,outline=None,width=1,tag=None):
        return self.create_polygon(*self.coords_([v for xy in pts for v in xy]),fill=fill,
                outline=outline or '',width=width*self.scale,tags=tag or ())

    def rounded(self,x,y,w,h,r=9,fill=None,outline=None,width=1,tag=None):
        pts=[(x+r,y),(x+w-r,y),(x+w,y),(x+w,y+r),(x+w,y+h-r),(x+w,y+h),
             (x+w-r,y+h),(x+r,y+h),(x,y+h),(x,y+h-r),(x,y+r),(x,y)]
        return self.create_polygon(*self.coords_([v for xy in pts for v in xy]),
            smooth=True,splinesteps=16,fill=fill or '',outline=outline or '',
            width=width*self.scale,tags=tag or ())

    def hit(self,tag,x,y,w,h,callback,hint):
        self.rect(x,y,w,h,tag=tag)
        self.tag_bind(tag,'<Button-1>',lambda e:callback())
        self.tag_bind(tag,'<Enter>',lambda e:self._hover(hint))
        self.tag_bind(tag,'<Leave>',lambda e:self._hover(''))
        self._hit_targets[tag]=(x+w/2,y+h/2)

    def _hover(self, text):
        self.configure(cursor='hand2' if text else '')
        self.app.hover_var.set(text or '밸브·용기·압축기 심벌 클릭: 운전 상태 및 조작 Faceplate')

    def pipe(self,pts,active=False):
        # Neutral pipe has visible edging; flowing pipe has a teal core.
        self.line(pts,P['steel'],7)
        self.line(pts,'#D5DFE5',4.6)
        if active:
            self.line(pts,P['live'],3.3)
        for (x1,y1),(x2,y2) in zip(pts[:-1],pts[1:]):
            length=math.hypot(x2-x1,y2-y1)
            if length<58:continue
            dx=(x2-x1)/length;dy=(y2-y1)/length
            phase=(time.monotonic()*.34)%1 if self.app.running and active else .55
            for dist in [length*(.25+.5*phase)]:
                x=x1+dx*dist;y=y1+dy*dist
                self.polygon([(x+dx*7,y+dy*7),(x-dx*4-dy*4,y-dy*4+dx*4),
                              (x-dx*4+dy*4,y-dy*4-dx*4)],
                             P['live'] if active else P['muted'])

    def section(self,x,y,w,h,num,title,sub=''):
        self.rounded(x,y,w,h,8,P['white'],P['border'])
        self.rect(x+1,y+1,w-2,38,P['panel'])
        self.text(x+16,y+18,num,11,P['blue'],True,anchor='w',mono=True)
        self.text(x+45,y+18,title,13,P['ink'],True,anchor='w')
        if sub:self.text(x+w-12,y+18,sub,10,P['muted'],anchor='e')
        self.line([(x,y+39),(x+w,y+39)],P['border'],.8)

    def display(self,x,y,w,label,value,unit='',color=None):
        self.text(x,y-12,label,10,P['muted'],True,anchor='w')
        self.rounded(x,y,w,38,4,P['display'])
        self.text(x+w-10,y+19,value,23,color or P['digit'],True,anchor='e',mono=True)
        if unit:self.text(x+w,y+48,unit,9,P['muted'],anchor='e')

    def pill(self,x,y,w,label,fill=None,color=None):
        self.rounded(x,y,w,22,5,fill or P['panel'])
        self.text(x+w/2,y+11,label,10,color or P['muted'],True)

    def vessel(self,x,y,w,h,active=False):
        self.rounded(x+2,y+3,w,h,h/2,'#CCD7DF')
        self.rounded(x,y,w,h,h/2,'#DCE5EB',P['steel'],1.3)
        for i in range(1,8):
            t=i/8
            self.rounded(x+5,y+3+i*2,w-10,h-6-i*4,max(3,h/2-i*2),
                         blend('#A4B6C3','#F9FCFE',t),None)
        self.line([(x+22,y+6),(x+w-22,y+6)],'#FFFFFF',1.2)
        for xx in (x+40,x+w-40):
            self.rect(xx-8,y+h-1,16,8,'#A5B6C3',P['steel'])
        if active:self.rounded(x,y,w,h,h/2,None,P['live'],1.8)

    def valve(self,x,y,tag,compact=False):
        v=self.app.sim.valves[tag]
        moving=abs(v.opening-v.command)>1e-6
        color=P['amber'] if moving else P['green'] if v.opening>1e-6 else P['red']
        face=blend(color,P['white'],.82)
        self.line([(x,y-4),(x,y-23)],P['ink'],1.2)
        self.rounded(x-11,y-34,22,12,2,face,color,1.2)
        self.polygon([(x-14,y-10),(x-14,y+10),(x,y)],face,color,1.5)
        self.polygon([(x+14,y-10),(x+14,y+10),(x,y)],face,color,1.5)
        self.oval(x-3,y-3,6,6,color)
        display_tag=tag if len(tag)<=13 else tag[:10]+'…'
        self.text(x,y-45,display_tag,11,P['ink'],True)
        status=f'{v.opening*100:.0f} → {v.command*100:.0f}%' if moving else (
            'OPEN' if v.opening>.999 else 'CLOSED' if v.opening<1e-6 else f'{v.opening*100:.1f}%')
        self.pill(x-39,y+17,78,status,blend(color,P['white'],.9),color)
        if not compact:self.text(x,y+52,f'Cv {v.spec.cv:g}',10,P['muted'],mono=True)
        self.hit('valve:'+tag,x-42,y-52,84,113,lambda:self.app.valve_dialog(tag),
                 f'{tag} | 실제 {v.opening*100:.1f}% · 명령 {v.command*100:.1f}% · {v.source}')

    def cooler(self,x,y,tag):
        self.oval(x-19,y-19,38,38,'#E8F1F6',P['steel'],1.6)
        self.line([(x-15,y+5),(x-8,y-6),(x,y+6),(x+8,y-6),(x+15,y+5)],P['blue'],1.5)
        self.text(x,y+29,tag,10,P['muted'])

    def compressor(self,x,y,active):
        # Two diaphragm heads, one drive/package; no invented stage readings.
        c=P['cyan'] if active else '#97ACB9'
        self.rounded(x-55,y+50,175,13,3,'#94A8B5')
        self.rounded(x-36,y-2,133,51,5,blend(c,P['white'],.72),P['steel'],1.4)
        self.oval(x+3,y+6,43,35,blend(c,P['white'],.2),P['ink'],1.1)
        self.oval(x+15,y+15,18,18,'#DCECF1',P['ink'],1)
        self.rect(x-57,y-32,28,63,blend(c,P['white'],.25),P['ink'],1.3)
        self.rect(x+87,y-32,28,63,blend(c,P['white'],.25),P['ink'],1.3)
        for xx in (x-53,x+91):
            for yy in (y-25,y+21):self.oval(xx,yy,4,4,P['ink'])
        self.text(x-43,y-2,'1',13,P['white'],True)
        self.text(x+101,y-2,'2',13,P['white'],True)
        self.line([(x-29,y+12),(x-15,y+12)],P['steel'],6)
        self.line([(x+70,y+12),(x+87,y+12)],P['steel'],6)
        self.text(x+29,y+39,'DIAPHRAGM',8,P['ink'],True)
        self.text(x+29,y+81,'C-103  /  1기 · 2단',12,P['ink'],True)
        self.hit('compressor',x-70,y-42,202,139,self.app.compressor_dialog,
                 'C-103 | 등가 압축기 상태·기동/정지 · 단별 온도/압력은 계산하지 않음')

    def redraw(self):
        if not self.winfo_exists() or self.winfo_width()<10:return
        s=self.app.sim
        names=list(s.cfg.equalize_order)+[n for n in s.banks if n not in s.cfg.equalize_order]
        if self.follow and s.selected_bank in names:self.page=names.index(s.selected_bank)//3
        self.page=min(self.page,max(0,(len(names)-1)//3))
        self._last_selected=s.selected_bank
        w,h=self.winfo_width(),self.winfo_height()
        self.scale=min(w/self.BASE_W,h/self.BASE_H)
        self.ox=(w-self.BASE_W*self.scale)/2;self.oy=(h-self.BASE_H*self.scale)/2
        self.delete('all');self._hit_targets={}
        live=s.flow_kg_h>1e-7
        by=live and s.route=='BYPASS';cp=live and s.route=='RECOVERY'
        # Area frames
        self.section(12,12,282,468,'01','TT 수취 / INLET','T-101')
        self.section(309,12,669,158,'02','직접 균압 / BYPASS')
        self.section(309,186,669,294,'03','압축 회수 / COMPRESSION',
                     'RUN' if s.compressor_speed>1e-6 else 'STANDBY')
        self.section(995,12,533,468,'04','BANK 충전 / PRIORITY')
        count=max(1,math.ceil(len(names)/3))
        self.pill(1320,20,82,f'{self.page+1:02} / {count:02}')
        self.text(1426,30,'‹',23,P['blue'],True)
        self.text(1461,30,'›',23,P['blue'],True)
        self.hit('bankprev',1410,16,30,28,lambda:self.change_page(-1),'이전 Bank 화면')
        self.hit('banknext',1446,16,30,28,lambda:self.change_page(1),'다음 Bank 화면')
        self.text(1506,30,'◎',19,P['live'] if self.follow else P['muted'])
        self.hit('bankfollow',1489,16,30,28,self.follow_active,'현재 선택 Bank 따라가기')
        # TT vessel illustration
        self.text(151,75,'HYDROGEN TUBE TRAILER',12,P['muted'],True)
        for yy in (103,128,153,178):self.vessel(43,yy,209,20)
        self.line([(36,214),(263,214)],P['ink'],3)
        self.rect(48,214,202,8,'#AFBECA')
        for xx in (80,218):
            self.oval(xx-13,222,26,26,P['ink'])
            self.oval(xx-7,228,14,14,'#C5D3DD')
        self.hit('tt',33,86,237,164,lambda:self.app.vessel_dialog('TT'),'T-101 | TT 재고·압력 및 종료압력 조회')
        self.display(35,276,156,'TT PRESSURE',f'{s.tt.pg:.2f}','barg')
        self.text(209,278,'종료 SP',10,P['muted'])
        self.text(210,303,f'{s.cfg.tt_stop_barg:g}',17,P['ink'],True,mono=True)
        self.text(210,321,'barg',9,P['muted'])
        self.line([(35,344),(270,344)],P['border'])
        for yy,label,value in [(366,'남은 수소',f'{s.tt.mass_kg:,.3f} kg'),
                               (393,'용기 내용적',f'{s.tt.spec.volume_m3:g} m³'),
                               (420,'등온 설정',f'{s.tt.spec.temperature_c:g} °C'),
                               (447,'총 이송량',f'{s.bypass_kg+s.compressed_kg:,.3f} kg')]:
            self.text(36,yy,label,11,P['muted'],anchor='w')
            self.text(268,yy,value,13,P['ink'],True,anchor='e',mono=True)
        # Main flow: drawing leaves geometry gaps for equipment, not fake topology.
        self.pipe([(252,156),(278,156),(278,110),(376,110)],live)
        self.valve(342,110,'MOV-101',True)
        self.pipe([(376,110),(470,110),(970,110),(1018,110)],by)
        self.valve(602,110,'BYPASS',True)
        self.text(760,63,'DIRECT TRANSFER',10,P['muted'],True)
        self.text(765,85,f'{s.bypass_kg:,.3f} kg',19,P['live'] if by else P['muted'],True,mono=True)
        self.text(874,143,f'종료 ΔP {s.cfg.equalize_dp_bar:g} bar / {s.cfg.equalize_hold_s:g} s',10,P['muted'],anchor='e')
        # Branch tee to compressor route
        self.oval(373,107,6,6,P['live'] if live else P['steel'])
        self.pipe([(376,110),(376,305),(418,305)],cp)
        self.pipe([(418,305),(496,305)],cp)
        self.valve(436,305,'PRV-102')
        self.compressor(553,305,s.compressor_speed>1e-6)
        self.pipe([(668,305),(713,305)],cp)
        self.cooler(734,305,'IC / AC')
        self.pipe([(755,305),(970,305),(1018,305)],cp)
        self.valve(884,305,'COMP-OUT')
        # Regulator setpoint and package values are not measured stage pressures.
        self.text(626,249,f'흡입 SP  {s.cfg.suction_set_barg:.2f} barg',13,P['ink'],True)
        # Header and faceplate identify the package equivalent; no duplicate label here.
        self.text(734,355,f'냉각 설정 {s.cfg.aftercooler_temperature_c:g} °C',10,P['muted'])
        for xx,ww,label,value,unit in [(329,139,'흡입 계산압력',f'{s.suction_barg:.2f}' if s.compressor_speed>1e-6 and s.route=='RECOVERY' else '—','barg'),
                  (489,137,'등가 구동',f'{s.compressor_speed*100:.1f}','%'),
                  (647,137,'정격 처리량',f'{s.cfg.compressor_kg_h:.2f}','kg/h'),
                  (806,147,'압축 이송량',f'{s.compressed_kg:.3f}','kg')]:
            self.text(xx,403,label,10,P['muted'],True,anchor='w')
            self.rounded(xx,415,ww,32,4,P['display'])
            self.text(xx+ww-9,431,value,18,P['digit'] if cp else '#BECED8',True,anchor='e',mono=True)
            self.text(xx+ww,461,unit,10,P['muted'],anchor='e')
        # Header and 3 visible banks
        self.pipe([(1018,110),(1018,428)],False)
        visible=names[self.page*3:self.page*3+3]
        active_name=s.active_bank()
        if live and active_name in visible:
            y_active=125+137*visible.index(active_name)
            self.pipe([(1018,110 if by else 305),(1018,y_active)],True)
        self.text(1018,69,'HEADER',9,P['muted'],True)
        for i,name in enumerate(names[self.page*3:self.page*3+3]):
            y=125+i*137
            b=s.banks[name];v=s.valves['BANK-'+name]
            active=live and s.active_bank()==name
            selected=name==s.selected_bank
            self.rounded(1050,y-60,459,124,7,'#EFF8F6' if selected else '#F7F9FB',
                         '#74B4A7' if selected else '#D1DCE4',1.5 if selected else .8)
            self.pipe([(1018,y),(1152,y)],active)
            self.valve(1090,y,'BANK-'+name,True)
            self.vessel(1155,y-13,333,51,active)
            shown=name if len(name)<=17 else name[:14]+'…'
            self.text(1171,y-39,f'BANK {shown}',14,P['ink'],True,anchor='w')
            self.text(1490,y-39,f'{b.spec.volume_m3:g} m³',12,P['muted'],anchor='e',mono=True)
            self.rounded(1184,y-6,151,32,4,P['display'])
            self.text(1324,y+10,f'{b.pg:.2f}',21,'#FFDC96',True,anchor='e',mono=True)
            self.text(1346,y+10,'barg',11,P['muted'],anchor='w')
            self.text(1468,y+8,f'{b.mass_kg:,.2f} kg',13,P['ink'],True,anchor='e',mono=True)
            self.text(1161,y+56,f'SP {b.spec.target_barg:g}  /  HH {s.cfg.hh(b.spec):g} barg',10,P['muted'],anchor='w')
            ratio=min(1,max(0,b.pg/max(1,b.spec.target_barg)))
            self.rect(1380,y+51,105,6,'#DCE6ED')
            self.rect(1380,y+51,105*ratio,6,P['live'] if selected else P['steel'])
            self.text(1373,y+54,'P/SP',8,P['muted'],anchor='e')
            self.hit('bank:'+name,1154,y-55,344,114,lambda n=name:self.app.vessel_dialog(n),
                     f'Bank {name} | P {b.pg:.3f} barg · 재고 {b.mass_kg:.3f} kg · SP {b.spec.target_barg:g} barg')
        # Legend / model fence, so no unmodelled gauges are implied.
        self.rounded(12,493,1516,35,5,P['panel'])
        legend=[(30,P['green'],'OPEN'),(128,P['red'],'CLOSED'),(234,P['amber'],'MOVING')]
        for x,col,lab in legend:
            self.oval(x,505,10,10,col)
            self.text(x+18,510,lab,10,P['muted'],anchor='w')
        self.text(365,510,'H₂ 유동 배관',10,P['muted'],anchor='w')
        self.line([(453,510),(498,510)],P['live'],3)
        self.text(525,510,'P/SP = 압력/목표압력 (액위 아님)',10,P['muted'],anchor='w')
        self.text(1508,510,'등온 · 1 Bank 선택 충전 · 실제 PLC I/O 없음',10,P['muted'],anchor='e')


class Trend(tk.Canvas):
    """Read-only trend with numeric axes and decimated draw data."""
    def __init__(self,parent,app,kind='pressure',mini=False,**kw):
        super().__init__(parent,bg=P['white'],highlightthickness=0,**kw)
        self.app=app;self.kind=kind;self.mini=mini
        self.bind('<Configure>',lambda e:self.redraw())
        self.bind('<Motion>',self._motion)
        self.bind('<Leave>',lambda e:self.delete('crosshair'))
        self._domain=None
    def _motion(self,e):
        self.delete('crosshair')
        if not self._domain or self.mini:return
        left,right,top,bottom,x0,x1,ymax=self._domain
        if not left<=e.x<=right or not top<=e.y<=bottom:return
        self.create_line(e.x,top,e.x,bottom,fill='#93A9B8',dash=(3,3),tags='crosshair')
        t=x0+(x1-x0)*(e.x-left)/(right-left)
        self.create_text(min(right-75,e.x+8),top+10,text=f'{t:.2f} min',anchor='w',
                         fill=P['ink'],font=(self.app.mono,-11),tags='crosshair')
    def redraw(self):
        if not self.winfo_ismapped() or self.winfo_width()<30:return
        s=self.app.sim;rows=list(s.history)
        r=s.snapshot()
        if not rows or r['time_s']>rows[-1]['time_s']:rows.append(r)
        # Limit draw points only. Model journal/sample interval are untouched.
        stride=max(1,len(rows)//650)
        if rows:rows=rows[::stride]+([rows[-1]] if len(rows)>1 else [])
        colors=['#BB7525','#21856F','#7963B2','#B34E7A','#348D9C','#879034','#A25938','#55738C']
        names=list(s.banks)[:8]
        defs={
            'pressure':('압력','barg',[('tt_barg','TT',P['blue'])]+[(f'bank_{n}_barg',n,colors[i]) for i,n in enumerate(names)]),
            'flow':('순간 이송 유량','kg/h',[('flow_kg_h','TOTAL',P['live'])]+([] if self.mini else [(f'bank_{n}_in_kg_h',n,colors[i]) for i,n in enumerate(names)])),
            'opening':('밸브 실개도','%',[(tag+'_actual_pct',tag,col) for tag,col in
                 [('MOV-101',P['blue']),('BYPASS',P['live']),('PRV-102',colors[2]),('BANK-'+s.selected_bank,colors[0])]]),
            'inventory':('수소 재고','kg',[('tt_kg','TT',P['blue'])]+[(f'bank_{n}_kg',n,colors[i]) for i,n in enumerate(names)])}
        title,unit,series=defs[self.kind]
        self.delete('all');w=self.winfo_width();h=self.winfo_height()
        left=54;right=w-25;top=49 if self.mini else 74;bottom=h-31 if self.mini else h-42
        if right<=left or bottom<=top:return
        self.create_text(14,17,text=title+'  /  '+unit,anchor='w',fill=P['ink'],font=(self.app.face,-11 if self.mini else -16,'bold'))
        max_val=max([float(row.get(k,0)) for row in rows for k,_,_ in series] or [1.0])
        ymax=105 if self.kind=='opening' else max(1,max_val*1.12)
        if self.kind!='opening':
            order=10**math.floor(math.log10(ymax));ymax=math.ceil(ymax/order*2)/2*order
        x0=rows[0]['time_s']/60 if rows else 0;x1=max(x0+1,rows[-1]['time_s']/60 if rows else 1)
        self._domain=(left,right,top,bottom,x0,x1,ymax)
        for i in range(5):
            y=bottom-(bottom-top)*i/4
            self.create_line(left,y,right,y,fill='#E5ECF1')
            val=ymax*i/4
            self.create_text(left-8,y,text=f'{val:.0f}' if ymax>10 else f'{val:.1f}',anchor='e',
                             fill=P['muted'],font=(self.app.mono,-9 if self.mini else -11))
        for i in range(6):
            x=left+(right-left)*i/5
            self.create_text(x,bottom+15,text=f'{x0+(x1-x0)*i/5:.1f}',fill=P['muted'],font=(self.app.mono,-9 if self.mini else -11))
        self.create_text(right,bottom+29,text='min',anchor='e',fill=P['muted'],font=(self.app.mono,-9))
        lx=155 if self.mini else 20;ly=17 if self.mini else 45
        for j,(key,label,col) in enumerate(series):
            full_label=label
            if self.mini and len(label)>10:label=label[:8]+'…'
            if lx+65>right:lx=155 if self.mini else 20;ly+=16
            self.create_line(lx,ly,lx+14,ly,fill=col,width=2)
            self.create_text(lx+19,ly,text=label,anchor='w',fill=P['muted'],font=(self.app.face,-9 if self.mini else -11))
            lx+=max(50,len(label)*7+35)
            pts=[]
            for row in rows:
                x=left+(row['time_s']/60-x0)/(x1-x0)*(right-left)
                y=bottom-float(row.get(key,0))/ymax*(bottom-top)
                pts.extend((x,y))
            if len(pts)>=4:self.create_line(*pts,fill=col,width=1.7 if self.mini else 2.2)
            if pts:self.create_oval(pts[-2]-2,pts[-1]-2,pts[-2]+2,pts[-1]+2,fill=col,outline='')
        if self.kind=='flow' and self.mini:
            self.create_text(right,17,text=f'{s.flow_kg_h:.2f} kg/h',anchor='e',fill=P['live'],font=(self.app.mono,-12,'bold'))


# ===== h2sim/trend_data.py =====
"""Read-only trend snapshots. Never changes simulator time, state or journal samples."""
from dataclasses import dataclass, asdict
import copy
import csv
import math

TREND_GROUPS = {
    'pressure': ('압력', 'barg'),
    'flow': ('질량유량', 'kg/h'),
    'normal_flow': ('표준체적유량', 'Nm³/h'),
    'opening': ('밸브 개도', '%'),
    'inventory': ('수소 재고', 'kg'),
    'transferred': ('누적 이송량', 'kg'),
}

@dataclass(frozen=True)
class TrendSignal:
    key: str
    label: str
    unit: str

@dataclass
class TrendSnapshot:
    rows: list[dict]
    events: list[dict]
    initial_config: dict
    current_config: dict
    current_state: dict
    stop_history: list[dict]
    sampled_count: int
    snapshot_time_s: float
    source: str = 'Full simulation journal + current snapshot'


def signals_for(sim, group: str) -> list[TrendSignal]:
    if group not in TREND_GROUPS:
        raise ValueError('지원하지 않는 추세 종류입니다.')
    unit = TREND_GROUPS[group][1]
    pairs = {
        'pressure': [('tt_barg', 'TT'), ('suction_barg', '압축기 흡입'), ('tt_stop_barg', 'TT 종료 SP')]
            + [(f'bank_{n}_barg', f'Bank {n}') for n in sim.banks]
            + [(f'bank_{n}_target_barg', f'Bank {n} 목표 SP') for n in sim.banks]
            + [(f'bank_{n}_hh_barg', f'Bank {n} HH') for n in sim.banks],
        'flow': [('flow_kg_h', '순간 총유량'), ('step_avg_kg_h', '직전 적분 평균유량')]
            + [(f'bank_{n}_in_kg_h', f'Bank {n} 유입') for n in sim.banks],
        'normal_flow': [('flow_Nm3_h', '순간 총유량 / 0°C·1 atm')],
        'opening': [(f'{tag}_actual_pct', f'{tag} 실제') for tag in sim.valves]
            + [(f'{tag}_command_pct', f'{tag} 명령') for tag in sim.valves],
        'inventory': [('tt_kg', 'TT 재고')] + [(f'bank_{n}_kg', f'Bank {n} 재고') for n in sim.banks],
        'transferred': [('total_transferred_kg', '총 이송량'), ('bypass_kg', '직접 균압'), ('compressed_kg', '압축 회수')]
            + [(f'bank_{n}_received_kg', f'Bank {n} 누적 수취') for n in sim.banks],
    }
    return [TrendSignal(k, label, unit) for k, label in pairs[group]]


def default_signal_keys(sim, group: str) -> list[str]:
    if group == 'pressure': return ['tt_barg'] + [f'bank_{n}_barg' for n in list(sim.banks)[:8]]
    if group == 'flow': return ['flow_kg_h'] + [f'bank_{n}_in_kg_h' for n in list(sim.banks)[:8]]
    if group == 'opening':
        return [f'{tag}_actual_pct' for tag in ['MOV-101','BYPASS','PRV-102','COMP-OUT','BANK-'+sim.selected_bank] if tag in sim.valves]
    if group == 'inventory': return ['tt_kg'] + [f'bank_{n}_kg' for n in list(sim.banks)[:8]]
    if group == 'transferred': return ['total_transferred_kg','bypass_kg','compressed_kg']
    return ['flow_Nm3_h']


def _read_numeric(value: str, example):
    if isinstance(example, (float, int)):
        number = float(value)
        if not math.isfinite(number): raise ValueError('기록에 NaN/Inf가 포함되어 있습니다.')
        return int(number) if isinstance(example,int) else number
    return value


def read_journal(sim) -> list[dict]:
    """Read the complete disk journal, NOT the 30,000-row display buffer."""
    sim._journal.flush()
    current = sim.snapshot()
    rows = []
    with sim._journal_path.open(newline='',encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            rows.append({k: _read_numeric(v,current.get(k)) for k,v in row.items()})
    # Preserve same-time SP changes. Identical final samples need not be repeated.
    if not rows or current != rows[-1]: rows.append(copy.deepcopy(current))
    return rows


def capture_trend_snapshot(sim) -> TrendSnapshot:
    return TrendSnapshot(
        read_journal(sim), copy.deepcopy(sim.events_log), copy.deepcopy(sim.initial_config),
        asdict(sim.cfg), copy.deepcopy(sim.snapshot()), copy.deepcopy(sim.stop_history),
        sim._sample_count, sim.time_s)


def select_rows(rows: list[dict], start_s=None, end_s=None) -> list[dict]:
    if start_s is not None and (not math.isfinite(start_s) or start_s < 0):
        raise ValueError('시작시각은 0 이상의 유한한 숫자여야 합니다.')
    if end_s is not None and (not math.isfinite(end_s) or end_s < 0):
        raise ValueError('종료시각은 0 이상의 유한한 숫자여야 합니다.')
    if start_s is not None and end_s is not None and end_s < start_s:
        raise ValueError('종료시각이 시작시각보다 작습니다.')
    return [r for r in rows if (start_s is None or r['time_s'] >= start_s-1e-9)
            and (end_s is None or r['time_s'] <= end_s+1e-9)]


def thin_for_plot(rows: list[dict], keys: list[str], maximum: int=1800) -> list[dict]:
    """Display-only min/max envelope: keep signal extrema, endpoints and order."""
    if len(rows)<=maximum: return rows
    keys = keys or ['time_s']
    width = max(1, math.ceil(len(rows) / max(1, maximum // (2*len(keys)+2))))
    keep = {0,len(rows)-1}
    for a in range(0,len(rows),width):
        b=min(len(rows),a+width);keep.update((a,b-1))
        for key in keys:
            finite=[i for i in range(a,b) if isinstance(rows[i].get(key),(float,int)) and math.isfinite(rows[i][key])]
            if finite:
                keep.add(min(finite,key=lambda i:rows[i][key]));keep.add(max(finite,key=lambda i:rows[i][key]))
    return [rows[i] for i in sorted(keep)]


def flatten_inputs(value, prefix=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from flatten_inputs(item,f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from flatten_inputs(item,f'{prefix}[{i}]')
    else:yield prefix,value


def field_unit(key: str) -> str:
    if key == 'time_min': return 'min'
    if key == 'time_s': return 's'
    if key.endswith('barg'): return 'barg'
    if key.endswith('Nm3_h'): return 'Nm³/h @ 0°C, 1.01325 bara'
    if key.endswith('m3_h'): return 'm³/h (actual TT state)'
    if key.endswith('kg_h'): return 'kg/h'
    if key.endswith('_kg'): return 'kg'
    if key.endswith('_pct'): return '%'
    if key == 'compressor_run_h': return 'h'
    if key == 'reference_kWh': return 'kWh (reference load, not predicted power)'
    return '—'


# ===== h2sim/xlsx_export.py =====
"""Portable native XLSX export, using only Python's standard library.

Writes an OOXML workbook and an editable XY chart. No Excel/COM, macros,
network service or mandatory third-party dependency. This module is only an
output serializer; it never evaluates the process model or changes its state.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile
from xml.etree import ElementTree as ET

_X='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_P='http://schemas.openxmlformats.org/package/2006/relationships'
_C='http://schemas.openxmlformats.org/drawingml/2006/chart'
_A='http://schemas.openxmlformats.org/drawingml/2006/main'
_D='http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
_CT='http://schemas.openxmlformats.org/package/2006/content-types'

@dataclass(frozen=True)
class _XFormula:
    expression: str
    cached: float


def _xe(parent,tag,attributes=None,text=None,ns=_X):
    element=ET.SubElement(parent,'{'+ns+'}'+tag,{str(k):str(v) for k,v in (attributes or {}).items()})
    if text is not None: element.text=str(text)
    return element


def _xmlbytes(root):
    data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    # OPC readers commonly expect the document namespace as the default namespace.
    # Local serialization avoids process-global namespace registry changes in workers.
    if root.tag.startswith(('{'+_X+'}', '{'+_P+'}', '{'+_CT+'}')):
        data=data.replace(b'xmlns:ns0=',b'xmlns=').replace(b'<ns0:',b'<').replace(b'</ns0:',b'</')
    return data


def _col(index):
    result=''
    while index:
        index,r=divmod(index-1,26);result=chr(65+r)+result
    return result


def _clean(value):
    # Text fields are inline strings, so =, +, @, - never become formulas.
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]','',str(value))[:32767]


def _styles():
    root=ET.Element('{'+_X+'}styleSheet')
    nf=_xe(root,'numFmts',{'count':3})
    for num,fmt in [(164,'0.000'),(165,'0.000000'),(166,'0.000E+00')]:_xe(nf,'numFmt',{'numFmtId':num,'formatCode':fmt})
    fonts=_xe(root,'fonts',{'count':4})
    for size,bold,color in [(10,False,'243746'),(17,True,'FFFFFF'),(10,True,'FFFFFF'),(10,False,'5B6E7D')]:
        f=_xe(fonts,'font');_xe(f,'sz',{'val':size});_xe(f,'name',{'val':'Malgun Gothic'});_xe(f,'color',{'rgb':'FF'+color})
        if bold:_xe(f,'b')
    fills=_xe(root,'fills',{'count':5})
    for pattern,color in [('none',None),('gray125',None),('solid','17324D'),('solid','EAF0F5'),('solid','F5F8FB')]:
        p=_xe(_xe(fills,'fill'),'patternFill',{'patternType':pattern})
        if color:_xe(p,'fgColor',{'rgb':'FF'+color});_xe(p,'bgColor',{'indexed':64})
    borders=_xe(root,'borders',{'count':1});b=_xe(borders,'border')
    for side in ('left','right','top','bottom','diagonal'):_xe(b,side)
    sx=_xe(root,'cellStyleXfs',{'count':1});_xe(sx,'xf',{'numFmtId':0,'fontId':0,'fillId':0,'borderId':0})
    xfs=_xe(root,'cellXfs',{'count':9})
    # 0=text, 1=number, 2=title, 3=header, 4=subtitle, 5=band-text,
    # 6=scientific, 7=high-precision, 8=band-number.
    definitions=[(0,0,0,False),(164,0,0,False),(0,1,2,False),(0,2,2,True),(0,3,0,True),
                 (0,0,4,False),(166,0,0,False),(165,0,0,False),(164,0,4,False)]
    for fmt,fontid,fill,wrap in definitions:
        xf=_xe(xfs,'xf',{'numFmtId':fmt,'fontId':fontid,'fillId':fill,'borderId':0,'xfId':0,'applyNumberFormat':1,'applyAlignment':1})
        _xe(xf,'alignment',{'vertical':'center','wrapText':int(wrap)})
    cs=_xe(root,'cellStyles',{'count':1});_xe(cs,'cellStyle',{'name':'Normal','xfId':0,'builtinId':0})
    return _xmlbytes(root)


def _cell(row,index,value,style=None):
    if value is None:return
    attrs={'r':f'{_col(index)}{row.attrib["r"]}'}
    is_number=isinstance(value,(float,int)) and not isinstance(value,bool)
    attrs['s']=str(style if style is not None else (1 if is_number else 0))
    if isinstance(value,_XFormula):
        if not math.isfinite(value.cached):raise ValueError('비유한 수식 결과')
        c=_xe(row,'c',attrs);_xe(c,'f',text=value.expression.lstrip('='));_xe(c,'v',text=repr(value.cached))
    elif is_number:
        if not math.isfinite(value):raise ValueError('Excel에 NaN/Inf를 저장할 수 없습니다.')
        _xe(_xe(row,'c',attrs),'v',text=repr(value))
    elif isinstance(value,bool):
        attrs['t']='b';_xe(_xe(row,'c',attrs),'v',text=int(value))
    else:
        attrs['t']='inlineStr';c=_xe(row,'c',attrs);t=_xe(_xe(c,'is'),'t',text=_clean(value))
        t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')


def _sheet(title,subtitle,headers,rows,header_row=4,chart=False,widths=None):
    root=ET.Element('{'+_X+'}worksheet')
    ncol=max(len(headers),1);last=header_row+len(rows)
    title_cols=12 if chart else min(ncol,12)
    _xe(root,'dimension',{'ref':f'A1:{_col(max(ncol,title_cols))}{max(last,header_row+1)}'})
    views=_xe(root,'sheetViews');view=_xe(views,'sheetView',{'workbookViewId':0,'showGridLines':0})
    _xe(view,'pane',{'xSplit':1,'ySplit':header_row,'topLeftCell':f'B{header_row+1}','activePane':'bottomRight','state':'frozen'})
    _xe(root,'sheetFormatPr',{'defaultRowHeight':18})
    cols=_xe(root,'cols')
    for i,h in enumerate(headers,1):
        width=(widths or {}).get(i,min(32,max(15,len(str(h))*.8+3)))
        _xe(cols,'col',{'min':i,'max':i,'width':width,'customWidth':1})
    data=_xe(root,'sheetData')
    r=_xe(data,'row',{'r':1,'ht':32,'customHeight':1});_cell(r,1,title,2)
    for i in range(2,title_cols+1):_cell(r,i,'',2)
    r=_xe(data,'row',{'r':2,'ht':34,'customHeight':1});_cell(r,1,subtitle,4)
    r=_xe(data,'row',{'r':header_row,'ht':36,'customHeight':1})
    for i,h in enumerate(headers,1):_cell(r,i,h,3)
    for ri,values in enumerate(rows,header_row+1):
        r=_xe(data,'row',{'r':ri,'ht':21,'customHeight':1})
        for ci,v in enumerate(values,1):
            sty=None
            if isinstance(v,_XFormula):sty=1
            elif ri%2==0:sty=8 if isinstance(v,(float,int)) and not isinstance(v,bool) else 5
            if ci<=len(headers) and headers[ci-1]=='mass_error_kg':sty=6
            if ci==2 and values and values[0]=='최대 절대 질량수지 오차':sty=6
            if ci==2 and values and values[0] in ('저장 표본 수','선택 변수 수','차트 표시 변수 수'):sty=0
            _cell(r,ci,v,sty)
    if rows:_xe(root,'autoFilter',{'ref':f'A{header_row}:{_col(ncol)}{last}'})
    merges=_xe(root,'mergeCells',{'count':2})
    _xe(merges,'mergeCell',{'ref':f'A1:{_col(title_cols)}1'})
    _xe(merges,'mergeCell',{'ref':f'A2:{_col(title_cols)}2'})
    _xe(root,'pageMargins',{'left':'.3','right':'.3','top':'.4','bottom':'.4','header':'.2','footer':'.2'})
    if chart:_xe(root,'drawing',{'{'+_R+'}id':'rId1'})
    return _xmlbytes(root)


def _chart_title(parent,title):
    elem=_xe(parent,'title',ns=_C);tx=_xe(elem,'tx',ns=_C);rich=_xe(tx,'rich',ns=_C)
    _xe(rich,'bodyPr',ns=_A);_xe(rich,'lstStyle',ns=_A);p=_xe(rich,'p',ns=_A)
    r=_xe(p,'r',ns=_A);_xe(r,'rPr',{'lang':'ko-KR','sz':1250},ns=_A);_xe(r,'t',text=title,ns=_A)
    _xe(elem,'overlay',{'val':0},ns=_C)


def _chart_xml(title,signals,rows):
    root=ET.Element('{'+_C+'}chartSpace')
    _xe(root,'lang',{'val':'ko-KR'},ns=_C)
    chart=_xe(root,'chart',ns=_C);_chart_title(chart,title)
    plot=_xe(chart,'plotArea',ns=_C);_xe(plot,'layout',ns=_C)
    scatter=_xe(plot,'scatterChart',ns=_C);_xe(scatter,'scatterStyle',{'val':'line'},ns=_C)
    start=29;end=start+len(rows)-1
    # A numeric X axis preserves unequal recording intervals and event timing.
    for idx,signal in enumerate(signals):
        ser=_xe(scatter,'ser',ns=_C);_xe(ser,'idx',{'val':idx},ns=_C);_xe(ser,'order',{'val':idx},ns=_C)
        tx=_xe(ser,'tx',ns=_C);_xe(tx,'v',text=signal.label,ns=_C)
        marker=_xe(ser,'marker',ns=_C);_xe(marker,'symbol',{'val':'none'},ns=_C)
        for name,col,vals in [('xVal','B',[r['time_s']/60 for r in rows]),('yVal',_col(idx+3),[r.get(signal.key,0) for r in rows])]:
            ref=_xe(_xe(ser,name,ns=_C),'numRef',ns=_C)
            _xe(ref,'f',text=f"'선택추세'!${col}${start}:${col}${end}",ns=_C)
            # Large runs still reference every data cell. Cache is optional.
            if len(rows)<=10000:
                cache=_xe(ref,'numCache',ns=_C);_xe(cache,'formatCode',text='General',ns=_C)
                _xe(cache,'ptCount',{'val':len(vals)},ns=_C)
                for n,v in enumerate(vals):_xe(_xe(cache,'pt',{'idx':n},ns=_C),'v',text=repr(float(v)),ns=_C)
        _xe(ser,'smooth',{'val':0},ns=_C)
    _xe(scatter,'axId',{'val':101},ns=_C);_xe(scatter,'axId',{'val':102},ns=_C)
    for aid,pos,cross,label in [(101,'b',102,'모사시간 [min]'),(102,'l',101,signals[0].unit)]:
        axis=_xe(plot,'valAx',ns=_C);_xe(axis,'axId',{'val':aid},ns=_C)
        scale=_xe(axis,'scaling',ns=_C);_xe(scale,'orientation',{'val':'minMax'},ns=_C)
        _xe(axis,'delete',{'val':0},ns=_C);_xe(axis,'axPos',{'val':pos},ns=_C)
        if aid==102:_xe(axis,'majorGridlines',ns=_C)
        _chart_title(axis,label);_xe(axis,'numFmt',{'formatCode':'0.00','sourceLinked':0},ns=_C)
        _xe(axis,'majorTickMark',{'val':'out'},ns=_C);_xe(axis,'minorTickMark',{'val':'none'},ns=_C)
        _xe(axis,'tickLblPos',{'val':'nextTo'},ns=_C);_xe(axis,'crossAx',{'val':cross},ns=_C)
        _xe(axis,'crosses',{'val':'autoZero'},ns=_C);_xe(axis,'crossBetween',{'val':'midCat'},ns=_C)
    leg=_xe(chart,'legend',ns=_C);_xe(leg,'legendPos',{'val':'b'},ns=_C);_xe(leg,'overlay',{'val':0},ns=_C)
    _xe(chart,'plotVisOnly',{'val':1},ns=_C);_xe(chart,'dispBlanksAs',{'val':'gap'},ns=_C)
    return _xmlbytes(root)


def _drawing():
    root=ET.Element('{'+_D+'}wsDr');anchor=_xe(root,'twoCellAnchor',ns=_D)
    for name,col,row in [('from',0,3),('to',12,26)]:
        mark=_xe(anchor,name,ns=_D)
        for n,v in [('col',col),('colOff',0),('row',row),('rowOff',0)]:_xe(mark,n,text=v,ns=_D)
    frame=_xe(anchor,'graphicFrame',{'macro':''},ns=_D);nv=_xe(frame,'nvGraphicFramePr',ns=_D)
    _xe(nv,'cNvPr',{'id':2,'name':'Editable trend chart'},ns=_D);_xe(nv,'cNvGraphicFramePr',ns=_D)
    xf=_xe(frame,'xfrm',ns=_D);_xe(xf,'off',{'x':0,'y':0},ns=_A);_xe(xf,'ext',{'cx':0,'cy':0},ns=_A)
    gd=_xe(_xe(frame,'graphic',ns=_A),'graphicData',{'uri':_C},ns=_A)
    _xe(gd,'chart',{'{'+_R+'}id':'rId1'},ns=_C);_xe(anchor,'clientData',ns=_D)
    return _xmlbytes(root)


def _rels(items):
    root=ET.Element('{'+_P+'}Relationships')
    for rid,kind,target in items:_xe(root,'Relationship',{'Id':rid,'Type':_R+'/'+kind,'Target':target},ns=_P)
    return _xmlbytes(root)


def write_trend_xlsx(path, snapshot: TrendSnapshot, group: str, signals: list[TrendSignal],
                     start_s=None, end_s=None) -> dict:
    """Atomically write full-precision numeric records and an editable chart.

    Selection bounds are inclusive and filter recorded samples only. No
    interpolation and no display downsampling is applied to exported values.
    """
    path=Path(path)
    if path.suffix.lower()!='.xlsx':raise ValueError('저장 확장자는 .xlsx여야 합니다.')
    if not signals:raise ValueError('내보낼 변수를 하나 이상 선택하세요.')
    if group not in TREND_GROUPS:raise ValueError('잘못된 추세 항목')
    if len({s.unit for s in signals})!=1:raise ValueError('선택추세에는 동일 단위의 변수만 저장하세요.')
    rows=select_rows(snapshot.rows,start_s,end_s)
    if not rows:raise ValueError('지정 구간에 저장된 표본이 없습니다.')
    if len(rows)>1048500:raise ValueError('Excel 단일 시트 행 한도를 초과합니다. 시간구간을 나눠 저장하세요.')
    fields=list(rows[0])
    if any(s.key not in fields for s in signals):raise ValueError('선택 변수와 실험 구성이 다릅니다.')
    raw_fields=['time_s','time_min']+[k for k in fields if k!='time_s']
    raw_rows=[];trend_rows=[]
    for i,r in enumerate(rows,5):
        raw_rows.append([r['time_s'],_XFormula(f'A{i}/60',r['time_s']/60)]+[r.get(k) for k in fields if k!='time_s'])
    for i,r in enumerate(rows,29):
        trend_rows.append([r['time_s'],_XFormula(f'A{i}/60',r['time_s']/60)]+[r.get(s.key) for s in signals])
    events=[e for e in snapshot.events if (start_s is None or e['time_s']>=start_s-1e-9)
            and (end_s is None or e['time_s']<=end_s+1e-9)]
    lo=rows[0]['time_s'];hi=rows[-1]['time_s'];nlast=4+len(rows)
    cmap={k:_col(i) for i,k in enumerate(raw_fields,1)}
    maxflow=max(float(r['flow_kg_h']) for r in rows)
    masserr=max(abs(float(r['mass_error_kg'])) for r in rows)
    summary=[
        ['프로그램','H₂ TT Dynamic Lab / HMI 0.2.2, Engine 0.2.0','물리·제어 엔진 유지'],
        ['EOS',snapshot.current_config['eos'],'선택한 EOS로 모든 재고·압력 계산'],
        ['모델 범위','순수 수소 / 등온 / 유한재고 / Cv 준정상 유동','실제 PID·열수지·압력파 제외'],
        ['내보내기 시각',datetime.now(timezone.utc).isoformat(timespec='seconds'),'UTC'],
        ['Snapshot 모사시각',snapshot.snapshot_time_s,'s'],
        ['구간 시작',_XFormula("'전체기록'!A5",lo),'s'],
        ['구간 종료',_XFormula(f"'전체기록'!A{nlast}",hi),'s'],
        ['구간 길이',_XFormula(f"'전체기록'!A{nlast}-'전체기록'!A5",hi-lo),'s'],
        ['저장 표본 수',_XFormula(f"ROWS('전체기록'!A5:A{nlast})",len(rows)),'표시 간소화 없이 저장'],
        ['최대 순간유량',_XFormula(f"MAX('전체기록'!{cmap['flow_kg_h']}5:{cmap['flow_kg_h']}{nlast})",maxflow),'kg/h'],
        ['구간 이송량',_XFormula(f"'전체기록'!{cmap['total_transferred_kg']}{nlast}-'전체기록'!{cmap['total_transferred_kg']}5",rows[-1]['total_transferred_kg']-rows[0]['total_transferred_kg']),'kg / 누적값의 차'],
        ['최대 절대 질량수지 오차',masserr,'kg'],
        ['추세 종류',TREND_GROUPS[group][0],TREND_GROUPS[group][1]],
        ['선택 변수 수',len(signals),'모든 선택 변수는 숫자 데이터로 저장'],
        ['차트 표시 변수 수',min(12,len(signals)),'가독성을 위해 최초 12개 / 표의 변수는 생략 없음'],
        ['기록간격',snapshot.current_config['sample_s'],'s / 적분간격과 구분'],
        ['표준체적 기준','0°C / 1.01325 bara','MW=2.016 kg/kmol; Vm=22.414 Nm³/kmol'],
        ['자료 출처',snapshot.source,'현재 실험의 디스크 저널을 포함'],
        ['반올림','표시 형식만 적용','내부 저장값은 계산 정밀도 유지'],
        ['전력량','reference_kWh는 참조부하 환산값','실제 압축기 입력전력 예측값과 구분'],
        ['선택구간','기록 표본에 대해 시작·끝 경계를 포함하여 필터','구간 경계의 보간은 수행하지 않음'],
        ['현재 실험','EOS/설비 구성 변경은 새 실험으로 초기화','서로 다른 EOS 실행결과를 한 시계열에 혼합하지 않음'],
    ]
    inputs=[]
    for prefix,cfg in [('초기',snapshot.initial_config),('현재',snapshot.current_config)]:
        inputs.extend([[prefix,k,v] for k,v in flatten_inputs(cfg)])
    for i,st in enumerate(snapshot.stop_history):inputs.extend([[f'정지이력[{i}]',k,v] for k,v in flatten_inputs(st)])
    key_labels={s.key:s.label for s in signals}
    dictionary=[[k,field_unit(k),key_labels.get(k,k)] for k in raw_fields]
    source_note=f"{lo:.3f}–{hi:.3f} s | EOS: {snapshot.current_config['eos']} | 표본 {len(rows):,}개 | 전체 저장 정밀도 유지"
    chart_signals=signals[:12]
    sheets=[
        ('요약',_sheet('TT 수취 시뮬레이션 · 결과 요약',source_note,['항목','값','단위 / 설명'],summary,widths={1:29,2:48,3:62})),
        ('선택추세',_sheet(TREND_GROUPS[group][0]+' · 편집 가능한 Excel 추세',source_note,
            ['time_s','time_min']+[f'{s.label} [{s.unit}]' for s in signals],trend_rows,header_row=28,chart=True)),
        ('전체기록',_sheet('원시 시간 이력 · 전체 변수',source_note,raw_fields,raw_rows)),
        ('이벤트',_sheet('운전·밸브·설정값 이벤트',source_note,['time_s','code','message','detail'],
            [[e.get(k,'') for k in ('time_s','code','message','detail')] for e in events],widths={1:16,2:27,3:75,4:80})),
        ('입력조건',_sheet('초기·현재 입력값 및 정지 상세이력',source_note,['구분','입력 경로','값'],inputs,widths={1:16,2:62,3:70})),
        ('변수설명',_sheet('시간 이력의 변수·단위',source_note,['변수 키','단위','표시명'],dictionary,widths={1:50,2:40,3:55})),
    ]
    workbook=ET.Element('{'+_X+'}workbook');views=_xe(workbook,'bookViews');_xe(views,'workbookView',{'activeTab':0})
    sheetlist=_xe(workbook,'sheets')
    for i,(name,_) in enumerate(sheets,1):_xe(sheetlist,'sheet',{'name':name,'sheetId':i,'{'+_R+'}id':f'rId{i}'})
    _xe(workbook,'calcPr',{'calcId':191029,'fullCalcOnLoad':1})
    ct=ET.Element('{'+_CT+'}Types')
    for ext,kind in [('rels','application/vnd.openxmlformats-package.relationships+xml'),('xml','application/xml')]:
        _xe(ct,'Default',{'Extension':ext,'ContentType':kind},ns=_CT)
    overrides=[('/xl/workbook.xml','spreadsheetml.sheet.main'),('/xl/styles.xml','spreadsheetml.styles'),
               ('/xl/drawings/drawing1.xml','drawing'),('/xl/charts/chart1.xml','drawingml.chart')]
    overrides += [(f'/xl/worksheets/sheet{i}.xml','spreadsheetml.worksheet') for i in range(1,len(sheets)+1)]
    for part,kind in overrides:
        _xe(ct,'Override',{'PartName':part,'ContentType':'application/vnd.openxmlformats-officedocument.'+kind+'+xml'},ns=_CT)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tempname=tempfile.mkstemp(prefix='h2_export_',suffix='.xlsx',dir=path.parent);os.close(fd)
    try:
        with zipfile.ZipFile(tempname,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.writestr('[Content_Types].xml',_xmlbytes(ct))
            z.writestr('_rels/.rels',_rels([('rId1','officeDocument','xl/workbook.xml')]))
            z.writestr('xl/workbook.xml',_xmlbytes(workbook))
            rels=[(f'rId{i}','worksheet',f'worksheets/sheet{i}.xml') for i in range(1,len(sheets)+1)]
            rels.append((f'rId{len(sheets)+1}','styles','styles.xml'))
            z.writestr('xl/_rels/workbook.xml.rels',_rels(rels));z.writestr('xl/styles.xml',_styles())
            for i,(_,payload) in enumerate(sheets,1):z.writestr(f'xl/worksheets/sheet{i}.xml',payload)
            z.writestr('xl/worksheets/_rels/sheet2.xml.rels',_rels([('rId1','drawing','../drawings/drawing1.xml')]))
            z.writestr('xl/drawings/drawing1.xml',_drawing())
            z.writestr('xl/drawings/_rels/drawing1.xml.rels',_rels([('rId1','chart','../charts/chart1.xml')]))
            z.writestr('xl/charts/chart1.xml',_chart_xml(TREND_GROUPS[group][0]+' / '+snapshot.current_config['eos'],chart_signals,rows))
        os.chmod(tempname, 0o644)
        os.replace(tempname,path)
    except BaseException:
        try:os.unlink(tempname)
        except OSError:pass
        raise
    return {'path':str(path),'rows':len(rows),'events':len(events),'signals':len(signals),
            'chart_signals':len(chart_signals),'eos':snapshot.current_config['eos'],
            'start_s':lo,'end_s':hi,'sheets':[name for name,_ in sheets]}


# ===== h2sim/trend_popup.py =====
"""Non-modal process trends; viewing and Excel export are read-only operations."""
import bisect
import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


class TrendPopup(tk.Toplevel):
    def __init__(self,app,group='pressure'):
        super().__init__(app)
        self.app=app;self.sim_identity=id(app.sim);self.group=group
        self.title('TREND MONITOR · 독립 추세창')
        self.geometry('1180x730');self.minsize(880,570);self.configure(bg=P['bg'])
        # Deliberately non-modal: no grab_set(), no pause(), no transient-only lock.
        self.live=tk.BooleanVar(value=True);self.events_var=tk.BooleanVar(value=True)
        self.group_var=tk.StringVar(value=TREND_GROUPS[group][0])
        self.window_var=tk.StringVar(value='전체')
        self.start_var=tk.StringVar(value='0');self.end_var=tk.StringVar(value='10')
        self._cache_count=-1;self._base_rows=[];self._frozen=None
        self._shown_rows=[];self._domain=None;self._last_signature=None
        self._build();self.bind('<Escape>',lambda e:self.destroy())
        self.bind('<Control-e>',lambda e:self.export_xlsx(False))
        self.bind('<F5>',lambda e:self.refresh_snapshot())
        self.set_group(group);self.update_view(force=True)

    def _build(self):
        head=tk.Frame(self,bg=P['navy']);head.pack(fill='x')
        tk.Label(head,text='TREND MONITOR',font=(self.app.face,-20,'bold'),fg='white',bg=P['navy']).pack(side='left',padx=18,pady=12)
        self.state_label=tk.Label(head,text='',font=(self.app.mono,-11),fg='#BFD4E1',bg=P['navy'])
        self.state_label.pack(side='right',padx=18)
        row=ttk.Frame(self,padding=(12,9));row.pack(fill='x')
        ttk.Label(row,text='항목').pack(side='left',padx=4)
        c=ttk.Combobox(row,textvariable=self.group_var,values=[x[0] for x in TREND_GROUPS.values()],width=14,state='readonly');c.pack(side='left',padx=4)
        c.bind('<<ComboboxSelected>>',lambda e:self.set_group(next(k for k,v in TREND_GROUPS.items() if v[0]==self.group_var.get())))
        ttk.Checkbutton(row,text='LIVE',variable=self.live,command=self.toggle_live).pack(side='left',padx=12)
        ttk.Button(row,text='새 Snapshot',command=self.refresh_snapshot).pack(side='left',padx=4)
        ttk.Checkbutton(row,text='이벤트 표시',variable=self.events_var,command=lambda:self.update_view(True)).pack(side='left',padx=9)
        ttk.Button(row,text='전체 Excel',command=lambda:self.export_xlsx(True)).pack(side='right',padx=4)
        ttk.Button(row,text='표시구간 Excel',style='Primary.TButton',command=lambda:self.export_xlsx(False)).pack(side='right',padx=4)
        row=ttk.Frame(self,padding=(12,0,12,9));row.pack(fill='x')
        ttk.Label(row,text='시간범위').pack(side='left',padx=4)
        self.window_combo=ttk.Combobox(row,textvariable=self.window_var,values=['전체','최근 10분','최근 30분','최근 60분','직접 지정'],width=12,state='readonly')
        self.window_combo.pack(side='left',padx=4);self.window_combo.bind('<<ComboboxSelected>>',lambda e:self.update_view(True))
        ttk.Label(row,text='시작 / 끝 [min]').pack(side='left',padx=(16,4))
        ttk.Entry(row,textvariable=self.start_var,width=8).pack(side='left',padx=3)
        ttk.Entry(row,textvariable=self.end_var,width=8).pack(side='left',padx=3)
        ttk.Button(row,text='구간 적용',command=self.apply_range).pack(side='left',padx=5)
        self.stats=ttk.Label(row,text='',foreground=P['muted']);self.stats.pack(side='right',padx=4)
        body=ttk.Frame(self);body.pack(fill='both',expand=True,padx=12,pady=(0,8))
        left=ttk.Frame(body,width=255);left.pack(side='left',fill='y',padx=(0,9));left.pack_propagate(False)
        ttk.Label(left,text='SIGNALS / 다중 선택',font=(self.app.face,-12,'bold')).pack(anchor='w',padx=4,pady=5)
        area=ttk.Frame(left);area.pack(fill='both',expand=True)
        self.listbox=tk.Listbox(area,selectmode='extended',exportselection=False,
            font=(self.app.face,-12),bg='white',fg=P['ink'],selectbackground='#D6ECE8',selectforeground=P['ink'],borderwidth=0,highlightthickness=1,highlightbackground=P['border'])
        self.listbox.pack(side='left',fill='both',expand=True)
        sb=ttk.Scrollbar(area,orient='vertical',command=self.listbox.yview);sb.pack(side='right',fill='y');self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>',lambda e:self.update_view(True))
        br=ttk.Frame(left);br.pack(fill='x',pady=7)
        ttk.Button(br,text='모두 선택',command=self.select_all).pack(side='left',expand=True,fill='x',padx=2)
        ttk.Button(br,text='기본 선택',command=self.select_defaults).pack(side='left',expand=True,fill='x',padx=2)
        ttk.Label(left,text='Ctrl / Shift로 다중 선택\n명령·실개도 및 SP도 선택 가능\n차트 표시: 최초 12개 변수\nExcel 데이터: 선택 변수 모두',foreground=P['muted'],justify='left').pack(anchor='w',padx=5,pady=(0,4))
        right=ttk.Frame(body,style='Card.TFrame');right.pack(side='left',fill='both',expand=True)
        self.canvas=tk.Canvas(right,bg='white',highlightthickness=1,highlightbackground=P['border'])
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.update_view(True))
        self.canvas.bind('<Motion>',self._hover);self.canvas.bind('<Leave>',lambda e:self.canvas.delete('cursor'))
        self.readout=ttk.Label(right,text='마우스로 기록 시각과 값을 조회합니다.',foreground=P['muted'],padding=8,wraplength=800)
        self.readout.pack(fill='x')
        foot=ttk.Label(self,text='LIVE 해제는 화면만 고정합니다. 시뮬레이션 RUN/PAUSE와 독립적입니다.  |  ESC 닫기 · Ctrl+E 표시구간 Excel',foreground=P['muted'],padding=(15,6))
        foot.pack(fill='x')

    def set_group(self,group):
        self.group=group;self.group_var.set(TREND_GROUPS[group][0]);self.signals=signals_for(self.app.sim,group)
        self.listbox.delete(0,'end')
        for s in self.signals:self.listbox.insert('end',s.label+' ['+s.unit+']')
        self.select_defaults()

    def select_defaults(self):
        default=set(default_signal_keys(self.app.sim,self.group));self.listbox.selection_clear(0,'end')
        for i,s in enumerate(self.signals):
            if s.key in default:self.listbox.selection_set(i)
        self.update_view(True)

    def select_all(self):
        self.listbox.selection_set(0,'end');self.update_view(True)

    def selected_signals(self):return [self.signals[i] for i in self.listbox.curselection()]

    def _rows(self):
        if self._frozen is not None and not self.live.get():return self._frozen.rows
        sim=self.app.sim
        if self._cache_count!=sim._sample_count:
            self._base_rows=read_journal(sim);self._cache_count=sim._sample_count
        rows=list(self._base_rows);current=sim.snapshot()
        # The last cached unsampled state can be refreshed without recording it.
        if rows and rows[-1]['time_s']>sim._last_sample+1e-9:rows.pop()
        if not rows or rows[-1]!=current:rows.append(current)
        return rows

    def range_bounds(self,rows=None):
        rows=self._rows() if rows is None else rows
        maximum=rows[-1]['time_s'] if rows else 0.0
        choice=self.window_var.get()
        if choice=='전체':return None,None
        if choice=='직접 지정':
            a=float(self.start_var.get())*60;b=float(self.end_var.get())*60
            select_rows([],a,b);return a,b
        mins=int(choice.split()[1].replace('분',''))
        return max(0,maximum-mins*60),maximum

    def apply_range(self):
        self.window_var.set('직접 지정')
        try:self.range_bounds();self.update_view(True)
        except ValueError as exc:messagebox.showerror('시간범위',str(exc),parent=self)

    def toggle_live(self):
        if not self.live.get():self._frozen=capture_trend_snapshot(self.app.sim)
        else:self._frozen=None;self._cache_count=-1
        self.update_view(True)

    def refresh_snapshot(self):
        self._cache_count=-1
        if not self.live.get():self._frozen=capture_trend_snapshot(self.app.sim)
        self.update_view(True)

    def on_new_simulation(self):
        self.sim_identity=id(self.app.sim);self._frozen=None;self.live.set(True);self._cache_count=-1
        self.window_var.set('전체');self.set_group(self.group)

    def update_view(self,force=False):
        if not self.winfo_exists():return
        if id(self.app.sim)!=self.sim_identity:self.on_new_simulation();return
        rows=self._rows()
        try:lo,hi=self.range_bounds(rows);selected=select_rows(rows,lo,hi)
        except (ValueError,TypeError):return
        signals=self.selected_signals();drawsignals=signals[:12]
        events=self._frozen.events if self._frozen is not None and not self.live.get() else self.app.sim.events_log
        self._shown_rows=selected;self._shown_signals=drawsignals
        self._shown_events=events
        currenttime=rows[-1]['time_s'] if rows else 0
        self.state_label.configure(text=f"{'LIVE' if self.live.get() else 'FROZEN'}  |  EOS {self.app.sim.cfg.eos}  |  {currenttime:.1f} s")
        self.stats.configure(text=f'{len(selected):,} samples · {len(signals)} signals')
        self._draw()

    def _draw(self):
        c=self.canvas;c.delete('all');w=c.winfo_width();h=c.winfo_height()
        if w<150 or h<120:return
        rows=self._shown_rows;signals=self._shown_signals
        c.create_text(18,22,text=f'{TREND_GROUPS[self.group][0]}  /  {TREND_GROUPS[self.group][1]}',anchor='w',fill=P['ink'],font=(self.app.face,-17,'bold'))
        if not rows or not signals:
            c.create_text(w/2,h/2,text='표시할 변수와 기록구간을 선택하세요.',fill=P['muted'],font=(self.app.face,-14));self._domain=None;return
        palette=['#23699C','#B47722','#168773','#7B5DB7','#B1476E','#148C9C','#7C8638','#AA5C38','#485F95','#807375','#B14343','#447564']
        x=18;y=51
        for i,s in enumerate(signals):
            text=s.label
            needed=max(92,min(220,len(text)*8+35))
            if x+needed>w-20:x=18;y+=21
            c.create_line(x,y,x+16,y,fill=palette[i],width=2.4)
            c.create_text(x+22,y,text=text,anchor='w',fill=P['muted'],font=(self.app.face,-11));x+=needed
        left=70;right=w-25;top=y+25;bottom=h-45
        if bottom-top<35:return
        vals=[float(r.get(s.key,0)) for r in rows for s in signals]
        low=min(0,min(vals));high=max(1,max(vals))
        if self.group=='opening':low=0;high=105
        else:
            span=max(1e-6,high-low);low-=span*.035 if low<0 else 0;high+=span*.08
        t0=rows[0]['time_s'];t1=max(t0+.01,rows[-1]['time_s'])
        self._domain=(left,right,top,bottom,t0,t1,low,high)
        for i in range(6):
            yy=bottom-(bottom-top)*i/5;v=low+(high-low)*i/5
            c.create_line(left,yy,right,yy,fill='#E1E9EF')
            c.create_text(left-8,yy,text=f'{v:,.1f}' if abs(high)<1000 else f'{v:,.0f}',anchor='e',fill=P['muted'],font=(self.app.mono,-10))
            xx=left+(right-left)*i/5
            c.create_text(xx,bottom+17,text=f'{(t0+(t1-t0)*i/5)/60:.2f}',fill=P['muted'],font=(self.app.mono,-10))
        c.create_text(right,bottom+32,text='min',anchor='e',fill=P['muted'],font=(self.app.mono,-10))
        if self.events_var.get():
            marks=[e for e in self._shown_events if t0<=e['time_s']<=t1 and any(a in e['code'] for a in ('RULE','SETPOINT','STOP','SEQUENCE','VALVE','ESD'))]
            for e in marks[-35:]:
                xx=left+(right-left)*(e['time_s']-t0)/(t1-t0)
                c.create_line(xx,top,xx,bottom,fill='#D5B8AA',dash=(2,5))
        plotted=thin_for_plot(rows,[s.key for s in signals])
        for i,s in enumerate(signals):
            points=[]
            for r in plotted:
                points.extend((left+(right-left)*(r['time_s']-t0)/(t1-t0),bottom-(bottom-top)*(float(r.get(s.key,0))-low)/(high-low)))
            if len(points)>=4:c.create_line(*points,fill=palette[i],width=2.0)
            if points:c.create_oval(points[-2]-2,points[-1]-2,points[-2]+2,points[-1]+2,fill=palette[i],outline='')
        self.readout.configure(text=f"{len(rows):,}개 기록 / 화면 {len(plotted):,}개 점 · Excel은 화면 간소화 없이 저장 · {t0:.3f}–{rows[-1]['time_s']:.3f} s")

    def _hover(self,event):
        self.canvas.delete('cursor')
        if not self._domain or not self._shown_rows:return
        left,right,top,bottom,t0,t1,low,high=self._domain
        if not left<=event.x<=right or not top<=event.y<=bottom:return
        target=t0+(t1-t0)*(event.x-left)/(right-left);times=[r['time_s'] for r in self._shown_rows]
        j=bisect.bisect_left(times,target);candidates=[i for i in (j-1,j) if 0<=i<len(times)]
        row=self._shown_rows[min(candidates,key=lambda i:abs(times[i]-target))]
        x=left+(right-left)*(row['time_s']-t0)/(t1-t0)
        self.canvas.create_line(x,top,x,bottom,fill='#61798A',dash=(3,3),tags='cursor')
        text=f"t={row['time_s']:.3f} s | "+'  ·  '.join(f'{s.label}: {float(row.get(s.key,0)):.3f}' for s in self._shown_signals[:6])
        self.readout.configure(text=text)

    def export_xlsx(self,all_rows=False):
        try:
            signals=self.selected_signals()
            if not signals:raise ValueError('변수를 하나 이상 선택하세요.')
            snap=self._frozen if self._frozen is not None and not self.live.get() else capture_trend_snapshot(self.app.sim)
            lo,hi=(None,None) if all_rows else self.range_bounds(snap.rows)
            if not select_rows(snap.rows,lo,hi):raise ValueError('지정 구간에 저장된 표본이 없습니다.')
            path=filedialog.asksaveasfilename(parent=self,defaultextension='.xlsx',filetypes=[('Excel workbook','*.xlsx')],
                initialfile=f'H2_TT_{self.group}_{snap.current_config["eos"]}_{int(snap.snapshot_time_s)}s.xlsx')
            if path:self.app.start_excel_export(path,snap,self.group,signals,lo,hi,parent=self)
        except (ValueError,TypeError,OSError) as exc:messagebox.showerror('Excel 저장',str(exc),parent=self)


# ===== h2sim/gui.py =====
"""v0.2.2 PLC-style HMI with independent trends and native Excel export."""
import copy
import json
import math
from pathlib import Path
import time
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font

NAVY=P['navy']; BG=P['bg']; WHITE=P['white']; INK=P['ink']; MUTED=P['muted']
BLUE=P['blue']; GREEN=P['green']; RED=P['red']; ORANGE=P['amber']; BORDER=P['border']
UI_VERSION='0.2.2'


class App(tk.Tk):
    def __init__(self,config=None):
        super().__init__()
        self.title('H₂ TT Receiving | PLC HMI v0.2.2 — Offline Simulator')
        sw,sh=self.winfo_screenwidth(),self.winfo_screenheight()
        self.geometry(f'{min(1740,sw-30)}x{min(1040,sh-65)}+10+10')
        self.minsize(1180,720);self.configure(bg=BG)
        families=font.families(self)
        self.face=next((x for x in ('Malgun Gothic','NanumGothic','Noto Sans CJK KR','Arial') if x in families),'TkDefaultFont')
        self.mono=next((x for x in ('Consolas','DejaVu Sans Mono','Liberation Mono','Courier New') if x in families),'TkFixedFont')
        self.option_add('*Font',(self.face,-12))
        self.option_add('*Toplevel.background',BG)
        self._style()
        self.sim=Simulator(config)
        self.running=False;self.speed=tk.StringVar(value='60')
        self.route_var=tk.StringVar(value='BYPASS');self.selected_var=tk.StringVar(value=self.sim.selected_bank)
        self._pending_sim=0.0;self._last_wall=time.perf_counter()
        self._last_rate=(time.perf_counter(),self.sim.time_s);self.actual_rate=0.0;self._last_log_count=-1
        self._last_hmi_refresh=0.0;self._last_plot_refresh=0.0
        self._fullscreen=False;self._faceplates={};self._windows=[]
        self.trend_windows=[];self._export_jobs=[]
        self.columnconfigure(0,weight=1);self.rowconfigure(3,weight=1)
        self.hover_var=tk.StringVar(value='밸브·용기·압축기 심벌 클릭: 운전 상태 및 조작 Faceplate')
        self.mini_var=tk.BooleanVar(value=False)
        self.trend_kind=tk.StringVar(value='pressure')
        self._build()
        self.protocol('WM_DELETE_WINDOW',self.on_close)
        self.bind('<F11>',self.toggle_fullscreen)
        self.bind('<Escape>',self.exit_fullscreen)
        self.bind('<F5>',lambda e:self.pause() if self.running else self.run())
        self.bind('<Control-o>',lambda e:self.load_config())
        self.bind('<Control-s>',lambda e:self.save_config())
        self.after(100,self._tick);self.after(150,self.refresh)

    def _style(self):
        st=ttk.Style(self);st.theme_use('clam')
        st.configure('.',background=BG,foreground=INK,font=(self.face,-12))
        st.configure('TFrame',background=BG)
        st.configure('Card.TFrame',background=WHITE)
        st.configure('TLabel',background=BG,foreground=INK)
        st.configure('Card.TLabel',background=WHITE)
        st.configure('Muted.TLabel',foreground=MUTED)
        st.configure('TButton',font=(self.face,-12),padding=(11,7),background='#F5F8FA',
                     foreground=INK,bordercolor=BORDER,lightcolor='#FFFFFF',darkcolor=BORDER,focuscolor='#75AABF',relief='flat')
        st.map('TButton',background=[('pressed','#CCDCE6'),('active','#E2EDF3')])
        for name,col in [('Primary',BLUE),('Run',GREEN),('Stop','#BA742A'),('Danger',RED)]:
            st.configure(name+'.TButton',background=col,foreground=WHITE,bordercolor=col,
                         lightcolor=col,darkcolor=col,font=(self.face,-12,'bold'))
            st.map(name+'.TButton',background=[('pressed',blend(col,'#000000',.17)),('active',blend(col,'#FFFFFF',.10))],
                   foreground=[('disabled','#B8C7D0')])
        st.configure('TEntry',padding=5,fieldbackground=WHITE,bordercolor=BORDER)
        st.configure('TCombobox',padding=4,fieldbackground=WHITE,background=WHITE,arrowcolor=INK,bordercolor=BORDER)
        st.map('TCombobox',fieldbackground=[('readonly',WHITE)])
        st.configure('TNotebook',background=BG,borderwidth=0,tabmargins=(0,0,0,0))
        st.configure('TNotebook.Tab',font=(self.face,-12,'bold'),padding=(22,10),background='#DEE7ED',foreground=MUTED,
                     bordercolor=BG,lightcolor=BG)
        st.map('TNotebook.Tab',background=[('selected',WHITE),('active','#EDF4F8')],
               foreground=[('selected',BLUE),('active',INK)])
        st.configure('Treeview',font=(self.face,-12),rowheight=31,background=WHITE,fieldbackground=WHITE,
                     foreground=INK,borderwidth=0)
        st.configure('Treeview.Heading',font=(self.face,-12,'bold'),background='#DCE7EE',foreground=INK,
                     relief='flat',padding=8)
        st.map('Treeview',background=[('selected','#D6ECE8')],foreground=[('selected',INK)])
        st.configure('TLabelframe',background=BG,bordercolor=BORDER)
        st.configure('TLabelframe.Label',background=BG,font=(self.face,-12,'bold'))
        st.configure('Vertical.TScrollbar',background='#BBCBD6',troughcolor='#EDF2F6',borderwidth=0,arrowsize=12)
        st.configure('TScale',background=BG,troughcolor='#C5D6DF')

    def _btn(self,parent,label,command,style=None,side='left',**kw):
        b=ttk.Button(parent,text=label,command=command,style=style or 'TButton',**kw)
        b.pack(side=side,padx=3);return b

    def _build(self):
        # Header: industrial identity and a clearly separated offline safety action.
        header=tk.Frame(self,bg=NAVY,height=63);header.grid(row=0,column=0,sticky='ew');header.pack_propagate(False)
        title=tk.Frame(header,bg=NAVY);title.pack(side='left',padx=18,pady=9)
        tk.Label(title,text='HYDROGEN RECEIVING',fg=WHITE,bg=NAVY,font=(self.face,-21,'bold')).pack(anchor='w')
        tk.Label(title,text='TT 수취 · 순차 충전 운전감시  /  CONTROL ROOM',fg='#ACC6D6',bg=NAVY,font=(self.face,-10)).pack(anchor='w')
        esd=tk.Button(header,text='SIM ESD',command=self.estop,bg=RED,fg=WHITE,activebackground='#9F3639',
                      activeforeground=WHITE,relief='flat',bd=0,font=(self.face,-14,'bold'),padx=23,pady=11,cursor='hand2')
        esd.pack(side='right',padx=(12,18),pady=11)
        meta=tk.Frame(header,bg=NAVY);meta.pack(side='right',padx=8)
        tk.Label(meta,text='OFFLINE SIMULATOR',bg=NAVY,fg='#A5E3CE',font=(self.face,-12,'bold')).pack(anchor='e')
        tk.Label(meta,text='HMI 0.2.2  /  ENGINE 0.2.0  ·  F11 전체화면',bg=NAVY,fg='#AEC4D2',font=(self.face,-10)).pack(anchor='e',pady=(4,0))
        # The simulation toolbar is not confused with plant start/stop controls.
        tool=ttk.Frame(self,padding=(12,7));tool.grid(row=1,column=0,sticky='ew')
        ttk.Label(tool,text='SIM',font=(self.mono,-10,'bold'),foreground=MUTED).pack(side='left',padx=(2,8))
        self.run_button=self._btn(tool,'▶ RUN',self.run,'Run.TButton')
        self._btn(tool,'Ⅱ PAUSE',self.pause)
        self._btn(tool,'1 STEP',self.single_step)
        ttk.Label(tool,text='배속',foreground=MUTED).pack(side='left',padx=(15,4))
        ttk.Combobox(tool,textvariable=self.speed,values=['1','10','60','300','1200','6000'],state='readonly',width=6).pack(side='left')
        self.rate_label=ttk.Label(tool,text='실행 0×',foreground=MUTED);self.rate_label.pack(side='left',padx=9)
        self._btn(tool,'설비 구성',self.settings,side='right')
        self._btn(tool,'운전 SP',self.open_setpoints,'Primary.TButton',side='right')
        export_menu=ttk.Menubutton(tool,text='EXPORT ▾');export_menu.pack(side='right',padx=3)
        menu=tk.Menu(export_menu,tearoff=False)
        menu.add_command(label='Excel (.xlsx) 전체 기록',command=self.export_excel)
        menu.add_command(label='CSV + JSON 원자료',command=self.export)
        export_menu.configure(menu=menu)
        self._btn(tool,'설정 저장',self.save_config,side='right')
        self._btn(tool,'설정 열기',self.load_config,side='right')
        self._btn(tool,'초기화',self.reset,side='right')
        # Compact process overview strip.
        strip=tk.Frame(self,bg=BG);strip.grid(row=2,column=0,sticky='ew',padx=14,pady=(0,9))
        self.kpi={};self.kpi_units={}
        definitions=[('time','SIM TIME','hh:mm:ss'),('tt','TT PRESSURE','barg'),
                     ('flow','TRANSFER RATE','kg/h'),('mass','RECEIVED','kg'),
                     ('phase','SEQUENCE','mode / stage'),('balance','MASS BALANCE','kg')]
        for i,(key,label,unit) in enumerate(definitions):
            strip.columnconfigure(i,weight=1,uniform='kpi')
            card=tk.Frame(strip,bg=WHITE,highlightbackground=BORDER,highlightthickness=1)
            card.grid(row=0,column=i,sticky='ew',padx=(0,7 if i<5 else 0))
            tk.Label(card,text=label,bg=WHITE,fg=MUTED,font=(self.face,-10,'bold')).pack(anchor='w',padx=12,pady=(7,1))
            row=tk.Frame(card,bg=WHITE);row.pack(fill='x',padx=12,pady=(0,7))
            lab=tk.Label(row,text='—',bg=WHITE,fg=INK,font=(self.mono,-21,'bold'));lab.pack(side='left')
            u=tk.Label(row,text=unit,bg=WHITE,fg=MUTED,font=(self.face,-9),justify='right');u.pack(side='right',anchor='s',pady=3);self.kpi_units[key]=u
            self.kpi[key]=lab
        self.notebook=ttk.Notebook(self);self.notebook.grid(row=3,column=0,sticky='nsew',padx=14)
        self.pages={}
        self._build_process()
        self._build_trends()
        self._build_logic()
        self._build_events()
        self._build_equipment()
        self.notebook.bind('<<NotebookTabChanged>>',lambda e:self.refresh(force=True))
        # Acknowledgement-free event journal; no fake PLC alarm ACK semantics.
        logs=tk.Frame(self,bg=WHITE,highlightbackground=BORDER,highlightthickness=1)
        logs.grid(row=4,column=0,sticky='ew',padx=14,pady=(5,0))
        top=tk.Frame(logs,bg='#DEE8EE',height=22);top.pack(fill='x');top.pack_propagate(False)
        tk.Label(top,text='EVENT JOURNAL',bg='#DEE8EE',fg=INK,font=(self.face,-10,'bold')).pack(side='left',padx=10)
        self.event_count=tk.Label(top,text='0 events',bg='#DEE8EE',fg=MUTED,font=(self.mono,-10));self.event_count.pack(side='right',padx=10)
        self.log_widget=tk.Text(logs,height=2,bg=WHITE,fg=INK,font=(self.mono,-10),relief='flat',bd=0,
                                padx=10,pady=3,state='disabled',wrap='none')
        self.log_widget.pack(fill='x')
        for tag,col in [('info',MUTED),('warning',ORANGE),('trip',RED),('success',GREEN)]:self.log_widget.tag_configure(tag,foreground=col)
        bar=tk.Frame(self,bg=NAVY,height=26);bar.grid(row=5,column=0,sticky='ew',pady=(5,0));bar.pack_propagate(False)
        self.statusbar=tk.Label(bar,textvariable=self.hover_var,bg=NAVY,fg='#BED0DC',font=(self.face,-10),anchor='w')
        self.statusbar.pack(side='left',padx=12,fill='x',expand=True)
        tk.Label(bar,text='ISOTHERMAL  |  H₂  |  NO PLC CONNECTION',bg=NAVY,fg='#8BA6B9',font=(self.mono,-9)).pack(side='right',padx=12)

    def _page(self,key,title):
        f=ttk.Frame(self.notebook,style='Card.TFrame');self.notebook.add(f,text=title);self.pages[key]=f;return f

    def _build_process(self):
        page=self._page('process','01  PROCESS / 계통')
        page.columnconfigure(0,weight=1);page.rowconfigure(1,weight=1)
        dock=tk.Frame(page,bg=WHITE);dock.grid(row=0,column=0,sticky='ew',padx=9,pady=7)
        self._btn(dock,'AUTO 충전 시작',lambda:self.auto(False),'Primary.TButton')
        self._btn(dock,'전량 압축',lambda:self.auto(True))
        self._btn(dock,'MANUAL',lambda:self._guard(self.sim.manual))
        self._btn(dock,'정상 STOP',self.stop,'Stop.TButton')
        self._btn(dock,'TRIP RESET',lambda:self._guard(self.sim.reset_trip))
        self._btn(dock,'수동 조작 패널',self.operator_dialog)
        self._btn(dock,'추세 팝업 ↗',self.open_trend,'Primary.TButton',side='right')
        self.state_badge=tk.Label(dock,text='READY',bg='#E8F1F5',fg=INK,font=(self.face,-11,'bold'),padx=14,pady=7)
        self.state_badge.pack(side='right',padx=8)
        self.mimic=Mimic(page,self,height=540);self.mimic.grid(row=1,column=0,sticky='nsew',padx=5)
        self.canvas=self.mimic
        self.mini_frame=tk.Frame(page,bg=WHITE)
        self.mini_pressure=Trend(self.mini_frame,self,'pressure',mini=True,height=140)
        self.mini_flow=Trend(self.mini_frame,self,'flow',mini=True,height=140)
        self.mini_pressure.pack(side='left',fill='both',expand=True,padx=(0,7))
        self.mini_flow.pack(side='left',fill='both',expand=True)
        self.toggle_mini()
        # Controls used by preserved callbacks live in the operator faceplate.
        self.bank_combo=ttk.Combobox(page,textvariable=self.selected_var,values=list(self.sim.banks),state='readonly')

    def _build_trends(self):
        page=self._page('trends','02  TRENDS / 추세')
        bar=ttk.Frame(page,padding=24);bar.pack(fill='x')
        ttk.Label(bar,text='독립 추세 모니터',font=(self.face,-24,'bold')).pack(anchor='w')
        ttk.Label(bar,text='계통도를 유지한 채 여러 추세창을 동시에 열어 비교합니다.',foreground=MUTED).pack(anchor='w',pady=(7,18))
        row=ttk.Frame(bar);row.pack(fill='x')
        for group,label in [('pressure','압력'),('flow','유량'),('opening','밸브 개도'),('inventory','수소 재고')]:
            self._btn(row,label+' ↗',lambda g=group:self.open_trend(g),'Primary.TButton')
        box=ttk.LabelFrame(bar,text='Excel / 원자료 내보내기',padding=18);box.pack(fill='x',pady=24)
        ttk.Label(box,text='· 팝업: 표시구간 또는 전체 이력을 .xlsx로 저장\n'
                  '· 저장항목: 요약, 편집형 추세차트, 전체 기록, 이벤트, 입력조건, 변수설명\n'
                  '· 그래프 표시 간소화와 별개로 전체 표본과 계산 정밀도를 보존\n'
                  '· LIVE 해제는 그래프만 고정하며 시간적분을 멈추지 않습니다.',justify='left').pack(anchor='w',pady=(0,15))
        row=ttk.Frame(box);row.pack(fill='x')
        self._btn(row,'전체 기록 Excel',self.export_excel,'Primary.TButton')
        self._btn(row,'CSV / JSON',self.export)
        self._btn(row,'물성모델 안내',self.eos_help)
        self.plot_canvases={'pressure':self.mini_pressure,'flow':self.mini_flow}

    def _build_logic(self):
        page=self._page('logic','03  LOGIC / 조건로직')
        bar=ttk.Frame(page,padding=12);bar.pack(fill='x')
        ttk.Label(bar,text='조건 · 동작 · 출력 권한',font=(self.face,-17,'bold')).pack(side='left')
        for text,fn in [('규칙 추가',lambda:self.open_rule()),('수정',self.edit_rule),('삭제',self.delete_rule),('선택 RESET',self.reset_rule)]:
            self._btn(bar,text,fn,side='right')
        ttk.Label(page,text='STOP/TRIP → 계통 인터록 → 보호규칙 → 인터록 규칙 → 수동 → 일반규칙 → 예약 → 시퀀스',
                  foreground=MUTED,padding=12).pack(fill='x')
        holder=ttk.Frame(page,padding=(12,0,12,10));holder.pack(fill='both',expand=True)
        self.rule_tree=table(holder,[('name','규칙 이름',235),('priority','우선순위',115),('mode','모드 / 단계',135),
                       ('behavior','유지방식',110),('state','상태',110),('fires','발동횟수',90)],12)
        self.rule_tree.bind('<Double-1>',lambda e:self.edit_rule())
        ttk.Label(page,text='규칙 저장·실행 동작은 v0.2.0과 동일합니다. UI는 규칙을 자동 추가하거나 변경하지 않습니다.',
                  foreground=MUTED,padding=12).pack(fill='x')

    def _build_events(self):
        page=self._page('events','04  EVENTS / 예약')
        bar=ttk.Frame(page,padding=14);bar.pack(fill='x')
        ttk.Label(bar,text='시간 예약 / Valve command',font=(self.face,-17,'bold')).pack(anchor='w',pady=(0,14))
        row=ttk.Frame(bar);row.pack(fill='x')
        self.ev_authority=tk.StringVar(value='automatic')
        self.ev_time=tk.StringVar(value='60');self.ev_target=tk.StringVar(value='BYPASS');self.ev_pct=tk.StringVar(value='0')
        for label in ['모사시각 [s]']:ttk.Label(row,text=label).pack(side='left')
        ttk.Entry(row,textvariable=self.ev_time,width=10).pack(side='left',padx=(6,18))
        ttk.Label(row,text='밸브').pack(side='left')
        self.ev_combo=ttk.Combobox(row,textvariable=self.ev_target,values=list(self.sim.valves),width=18,state='readonly')
        self.ev_combo.pack(side='left',padx=(6,18))
        ttk.Label(row,text='개도 [%]').pack(side='left')
        ttk.Entry(row,textvariable=self.ev_pct,width=8).pack(side='left',padx=(6,18))
        ttk.Combobox(row,textvariable=self.ev_authority,values=['automatic','operator'],width=15,state='readonly').pack(side='left',padx=6)
        self._btn(row,'예약 추가',self.add_event,'Primary.TButton')
        ttk.Label(page,text='automatic: AUTO 유지  /  operator: MANUAL 전환  ·  모사시각 기준으로 실행',foreground=MUTED,padding=14).pack(fill='x')
        area=ttk.Frame(page,padding=12);area.pack(fill='both',expand=True)
        self.event_tree=table(area,[('time','시각 [s]',140),('action','동작',180),('target','대상',230),('value','값',150)],12)

    def _build_equipment(self):
        page=self._page('equipment','05  EQUIPMENT / 상태')
        bar=ttk.Frame(page,padding=14);bar.pack(fill='x')
        ttk.Label(bar,text='저장 Bank / Valve output owner',font=(self.face,-17,'bold')).pack(side='left')
        self._btn(bar,'운전 SP',self.open_setpoints,'Primary.TButton',side='right')
        self._btn(bar,'설비 구성',self.settings,side='right')
        area=ttk.Frame(page,padding=12);area.pack(fill='both',expand=True)
        self.bank_tree=table(area,[('name','Bank',120),('p','압력 [barg]',125),('sp','목표 [barg]',125),('hh','HH [barg]',115),
                            ('mass','재고 [kg]',120),('op','실개도 [%]',115),('src','출력 소유자',320)],13)
        self.bank_tree.bind('<Double-1>',lambda e:self.vessel_dialog(self.bank_tree.selection()[0]) if self.bank_tree.selection() else None)
        ttk.Label(page,text='계통도는 3개 Bank씩 표시하며, 이 표와 CSV는 전체 Bank를 표시합니다. P/SP 표시줄은 액위가 아닙니다.',
                  foreground=MUTED,padding=12).pack(fill='x')

    def refresh(self,force=False):
        if not hasattr(self,'kpi') or not self.winfo_exists():return
        s=self.sim;now=time.perf_counter()
        # Refresh all main-screen readouts in the same frame: avoid stale mimic values
        # beside a newer KPI strip at high playback speeds. Engine dt is unchanged.
        if not force and now-self._last_hmi_refresh<.18:return
        sec=int(s.time_s)
        values={'time':f'{sec//3600:02}:{sec%3600//60:02}:{sec%60:02}',
                'tt':f'{s.tt.pg:.2f}','flow':f'{s.flow_kg_h:,.2f}','mass':f'{s.bypass_kg+s.compressed_kg:,.3f}',
                'phase':s.phase,'balance':f'{s.mass_error_kg:+.1e}'}
        for key,value in values.items():
            # A long Bank id cannot stretch the KPI columns.
            txt=value if key!='phase' or len(value)<=15 else value[:12]+'…'
            self.kpi[key].configure(text=txt,font=(self.mono,-16 if key=='phase' else -21,'bold'))
        self.kpi['phase'].configure(fg=RED if s.tripped else GREEN if s.flow_kg_h>0 else INK)
        self.rate_label.configure(text=f'실행 {self.actual_rate:.0f}×')
        self.kpi_units['flow'].configure(text=f'kg/h\n{normal_flow(s.flow_kg_h):,.1f} Nm³/h')
        display_state='TRIP' if s.tripped else 'RUNNING' if self.running else 'PAUSED' if s.time_s>0 else 'READY'
        self.state_badge.configure(text=f'{display_state}  |  {s.mode}',
                    bg='#F8E7E7' if s.tripped else '#E4F3ED' if self.running else '#E8F0F5',
                    fg=RED if s.tripped else GREEN if self.running else MUTED)
        self.run_button.configure(text='▶ RUNNING' if self.running else '▶ RUN')
        if self._last_log_count!=len(s.events_log):
            self.log_widget.configure(state='normal');self.log_widget.delete('1.0','end')
            for idx,e in enumerate(s.events_log[-35:]):
                kind='trip' if any(k in e['code'] for k in ('TRIP','ERROR','ESD')) else (
                     'warning' if any(k in e['code'] for k in ('REJECT','BLOCK','STOP_REQUEST','TIMEOUT')) else (
                     'success' if any(k in e['code'] for k in ('REACHED','COMPLETE','SEQUENCE')) else 'info'))
                prefix='' if idx==0 else '\n'
                self.log_widget.insert('end',prefix+f"{e['time_s']:10.1f} s   {e['code']:<20} {e['message']}",kind)
            self.log_widget.see('end');self.log_widget.configure(state='disabled')
            self.event_count.configure(text=f'{len(s.events_log)} events');self._last_log_count=len(s.events_log)
        page=self.notebook.select()
        if force or now-self._last_hmi_refresh>=.18:
            if page==str(self.pages['process']):self.mimic.redraw()
            if page==str(self.pages['logic']):self.refresh_rules()
            if page==str(self.pages['events']):self.refresh_events()
            if page==str(self.pages['equipment']):self.refresh_banks()
            self._last_hmi_refresh=now
        if force or now-self._last_plot_refresh>=.6:
            self.draw_plots();self._last_plot_refresh=now

    def draw_hmi(self):
        self.mimic.redraw()

    def draw_plots(self):
        if hasattr(self,'plot_canvases'):
            for c in self.plot_canvases.values():c.redraw()
        self.trend_windows=[w for w in self.trend_windows if w.winfo_exists()]
        for win in self.trend_windows:win.update_view()

    def update_trend(self):
        self.open_trend(self.trend_kind.get())

    def toggle_mini(self):
        if not hasattr(self,'mini_frame'):return
        if self.mini_var.get():self.mini_frame.grid(row=2,column=0,sticky='ew',padx=14,pady=(3,5))
        else:self.mini_frame.grid_remove()
        self.after_idle(lambda:self.refresh(force=True))

    def toggle_fullscreen(self,event=None):
        self._fullscreen=not self._fullscreen;self.attributes('-fullscreen',self._fullscreen)
        self.after(100,lambda:self.refresh(force=True));return 'break'

    def exit_fullscreen(self,event=None):
        if self._fullscreen:
            self._fullscreen=False;self.attributes('-fullscreen',False)
            self.after(100,lambda:self.refresh(force=True))

    def _present(self,win):
        """Center existing editor windows without changing their callbacks."""
        self._windows.append(win)
        win.update_idletasks()
        sw,sh=self.winfo_screenwidth(),self.winfo_screenheight()
        w=min(win.winfo_width(),sw-40);h=min(win.winfo_height(),sh-65)
        x=max(0,min(sw-w,self.winfo_rootx()+(self.winfo_width()-w)//2))
        y=max(0,min(sh-h-35,self.winfo_rooty()+(self.winfo_height()-h)//2))
        win.geometry(f'{w}x{h}+{x}+{y}');win.lift()
        return win

    def settings(self):
        self.pause();return self._present(ConfigEditor(self))

    def open_setpoints(self):
        return self._present(SetpointEditor(self))

    def open_rule(self,rule=None):
        return self._present(RuleEditor(self,rule))

    def edit_rule(self):
        if self.rule_tree.selection():return self.open_rule(next(r for r in self.sim.cfg.rules if r.name==self.rule_tree.selection()[0]))

    def _faceplate(self,key,title,subtitle,height=470):
        existing=self._faceplates.get(key)
        if existing and existing.winfo_exists():existing.lift();return None
        win=tk.Toplevel(self);win.title(title);win.geometry(f'500x{height}');win.transient(self);win.configure(bg=BG)
        self._faceplates[key]=win
        head=tk.Frame(win,bg=NAVY);head.pack(fill='x')
        tk.Label(head,text=title,bg=NAVY,fg=WHITE,font=(self.face,-20,'bold'),anchor='w').pack(fill='x',padx=18,pady=(15,3))
        tk.Label(head,text=subtitle,bg=NAVY,fg='#B8CFDC',font=(self.face,-11),anchor='w').pack(fill='x',padx=18,pady=(0,13))
        return win

    def _live(self,win,callback):
        def update():
            if not win.winfo_exists():return
            try:callback()
            except (KeyError,tk.TclError):win.destroy();return
            win.after(200,update)
        update()

    def valve_dialog(self,tag):
        if tag not in self.sim.valves:return
        v=self.sim.valves[tag]
        win=self._faceplate('valve:'+tag,tag,'VALVE FACEPLATE  /  명령 · 실제 개도 · 출력 권한',540)
        if win is None:return self._faceplates['valve:'+tag]
        info=tk.Frame(win,bg=BG);info.pack(fill='x',padx=18,pady=14)
        displays={}
        for key,label in [('actual','ACTUAL [%]'),('command','COMMAND [%]')]:
            f=tk.Frame(info,bg=P['display']);f.pack(side='left',fill='x',expand=True,padx=3)
            tk.Label(f,text=label,bg=P['display'],fg='#91ACBE',font=(self.mono,-11)).pack(pady=(9,0))
            l=tk.Label(f,text='0.0',bg=P['display'],fg=P['digit'],font=(self.mono,-30,'bold'));l.pack(pady=(0,9));displays[key]=l
        detail=ttk.Label(win,text='',justify='left',foreground=MUTED);detail.pack(fill='x',padx=22,pady=4)
        source=ttk.Label(win,text='',wraplength=450,justify='left',font=(self.face,-12,'bold'));source.pack(fill='x',padx=22,pady=7)
        ttk.Separator(win).pack(fill='x',padx=20,pady=9)
        req=tk.DoubleVar(value=v.command*100)
        row=ttk.Frame(win,padding=(20,0));row.pack(fill='x')
        ttk.Label(row,text='요청 개도 [%]',font=(self.face,-12,'bold')).pack(side='left')
        ttk.Entry(row,textvariable=req,width=10).pack(side='right')
        ttk.Scale(win,from_=0,to=100,variable=req,length=440).pack(padx=22,pady=14,fill='x')
        row=ttk.Frame(win);row.pack(pady=4)
        for label,val in [('CLOSE 0%',0),('50%',50),('OPEN 100%',100)]:self._btn(row,label,lambda p=val:req.set(p))
        actions=ttk.Frame(win);actions.pack(pady=12)
        def apply():
            try:self.sim.set_valve(tag,round(req.get(),2));self.refresh(force=True)
            except (ValueError,TypeError,tk.TclError) as exc:messagebox.showerror('명령 적용 실패',str(exc),parent=win)
        win.request_var=req;win.apply_command=apply
        self._btn(actions,'수동 명령 적용',apply,'Primary.TButton')
        self._btn(actions,'수동/예약 해제',lambda:self._guard(lambda:self.sim.release_overrides(tag)))
        ttk.Label(win,text='수동 적용 시 MANUAL 전환. 보호·인터록이 조작보다 우선합니다.\nPRV-102 개도는 이상조정기의 가용 Cv 상한입니다.',
                  foreground=MUTED,wraplength=460,justify='left').pack(padx=22,pady=8,anchor='w')
        def refresh_face():
            vv=self.sim.valves[tag]
            displays['actual'].configure(text=f'{vv.opening*100:.1f}')
            displays['command'].configure(text=f'{vv.command*100:.1f}')
            detail.configure(text=f'정격 Cv  {vv.spec.cv:g}   |   {vv.spec.characteristic}\n개방 {vv.spec.stroke_s:g} s   /   폐쇄 {vv.spec.close_s if vv.spec.close_s is not None else vv.spec.stroke_s:g} s')
            source.configure(text='OUTPUT OWNER   '+vv.source)
        self._live(win,refresh_face);return self._present(win)

    def vessel_dialog(self,name):
        if name!='TT' and name not in self.sim.banks:return
        win=self._faceplate('vessel:'+name,'T-101 / TT' if name=='TT' else 'STORAGE BANK '+name,
                             'VESSEL STATUS  /  유한 재고 · 등온 모델',450)
        if win is None:return self._faceplates['vessel:'+name]
        value=tk.Label(win,text='',bg=P['display'],fg='#FFDC96',font=(self.mono,-31,'bold'),pady=14)
        value.pack(fill='x',padx=20,pady=16)
        info=ttk.Label(win,text='',justify='left',font=(self.mono,-14));info.pack(anchor='w',padx=26,pady=4)
        owner=ttk.Label(win,text='',wraplength=450,foreground=MUTED);owner.pack(anchor='w',padx=24,pady=10)
        row=ttk.Frame(win);row.pack(pady=12)
        self._btn(row,'운전 SP 보기',self.open_setpoints,'Primary.TButton')
        if name!='TT':
            self._btn(row,'수동 Bank 선택',lambda:self.manual_bank(name))
            self._btn(row,'밸브 Faceplate',lambda:self.valve_dialog('BANK-'+name))
        else:self._btn(row,'설비 구성',self.settings)
        def update():
            b=self.sim.tt if name=='TT' else self.sim.banks[name]
            value.configure(text=f'{b.pg:,.3f}  barg')
            specs=[('수소 재고',f'{b.mass_kg:,.3f} kg'),('용기 내용적',f'{b.spec.volume_m3:g} m³'),('등온 설정',f'{b.spec.temperature_c:g} °C')]
            if name=='TT':specs.append(('하역 종료 SP',f'{self.sim.cfg.tt_stop_barg:g} barg'))
            else:specs.extend([('충전 목표 SP',f'{b.spec.target_barg:g} barg'),('모델 HH',f'{self.sim.cfg.hh(b.spec):g} barg')])
            info.configure(text='\n'.join(f'{a:<14} {b}' for a,b in specs))
            owner.configure(text='초기조건·내용적 변경은 새 실험, 운전 SP 변경은 현재 상태 유지.\n용기 그림과 P/SP 표시줄은 액체 레벨을 나타내지 않습니다.')
        self._live(win,update);return self._present(win)

    def compressor_dialog(self):
        win=self._faceplate('compressor','C-103 / COMPRESSOR','1 PACKAGE · 2 STAGES  /  등가 압축기 모델',430)
        if win is None:return self._faceplates['compressor']
        state=tk.Label(win,text='',bg=P['display'],fg=P['digit'],font=(self.mono,-24,'bold'),pady=15)
        state.pack(fill='x',padx=20,pady=15)
        values=ttk.Label(win,text='',justify='left',font=(self.face,-13));values.pack(anchor='w',padx=25,pady=5)
        row=ttk.Frame(win);row.pack(pady=18)
        self._btn(row,'COMP RUN',lambda:self._guard(lambda:self.sim.set_compressor(True)),'Run.TButton')
        self._btn(row,'COMP STOP',lambda:self._guard(lambda:self.sim.set_compressor(False)),'Stop.TButton')
        self._btn(row,'경로/Bank 조작',self.operator_dialog)
        ttk.Label(win,text='RECOVERY 경로와 유로 확보조건에 따라 명령 적용.\n표시 전력량은 등가 환산값이며 단별 압력·온도는 계산하지 않습니다.',foreground=MUTED,
                  wraplength=450).pack(anchor='w',padx=25)
        def update():
            s=self.sim
            state.configure(text=f"{'RUN' if s.compressor_speed>1e-6 else 'STOP'}   {s.compressor_speed*100:.1f} %")
            values.configure(text=f'흡입 계산압력     {s.suction_barg:.3f} barg\n정격 처리유량       {s.cfg.compressor_kg_h:g} kg/h\n토출압력 상한       {s.cfg.compressor_max_barg:g} barg\n운전시간              {s.compressor_run_h:.3f} h\n참조 전력량           {s.reference_kwh:.3f} kWh\n출력 소유자          {s.compressor_source}')
        self._live(win,update);return self._present(win)

    def operator_dialog(self):
        win=self._faceplate('operator','OPERATOR PANEL','수동 경로 · Bank · 압축기 조작',430)
        if win is None:return self._faceplates['operator']
        f=ttk.Frame(win,padding=20);f.pack(fill='both',expand=True)
        ttk.Label(f,text='활성 경로',font=(self.face,-12,'bold')).grid(row=0,column=0,sticky='w',pady=8)
        ttk.Combobox(f,textvariable=self.route_var,values=['BYPASS','RECOVERY'],state='readonly',width=18).grid(row=0,column=1,padx=8)
        ttk.Button(f,text='경로 적용',command=self.manual_route).grid(row=0,column=2)
        ttk.Label(f,text='충전 Bank',font=(self.face,-12,'bold')).grid(row=1,column=0,sticky='w',pady=8)
        self.bank_combo=ttk.Combobox(f,textvariable=self.selected_var,values=list(self.sim.banks),state='readonly',width=18)
        self.bank_combo.grid(row=1,column=1,padx=8)
        ttk.Button(f,text='Bank 선택',command=lambda:self.manual_bank(self.selected_var.get())).grid(row=1,column=2)
        for i,(txt,fn,style) in enumerate([
            ('선택 경로 OPEN',self.open_route,'Run.TButton'),
            ('MOV-101 CLOSE',lambda:self.command_valve('MOV-101',0),'Stop.TButton'),
            ('수동/예약 출력 해제',lambda:self._guard(self.sim.release_overrides),'TButton'),
            ('TRIP RESET',lambda:self._guard(self.sim.reset_trip),'TButton')]):
            ttk.Button(f,text=txt,command=fn,style=style).grid(row=2+i//2,column=i%2,columnspan=1 if i%2==0 else 2,sticky='ew',padx=4,pady=8)
        info=ttk.Label(f,text='',wraplength=445,foreground=MUTED,justify='left');info.grid(row=4,column=0,columnspan=3,pady=14,sticky='w')
        self._live(win,lambda:info.configure(text=f'현재: {self.sim.mode} / {self.sim.phase}\n경로: {self.sim.route} / 선택 Bank: {self.sim.selected_bank}\n수동 조작은 MANUAL로 전환하며 보호·인터록이 우선합니다.'))
        return self._present(win)

    def install_config(self,cfg):
        # Same engine installation semantics as v0.2.0; also clear UI-only faceplates.
        replacement=Simulator(cfg)
        for win in list(self._faceplates.values()):
            if win.winfo_exists():win.destroy()
        self._faceplates={}
        self._pending_sim=0;self._last_wall=time.perf_counter();self._last_rate=(self._last_wall,0.0)
        self.running=False;old=self.sim;self.sim=replacement;old.close()
        self._last_log_count=-1;self.selected_var.set(self.sim.selected_bank);self.route_var.set(self.sim.route)
        self.ev_combo.configure(values=list(self.sim.valves))
        if self.ev_target.get() not in self.sim.valves:self.ev_target.set('BYPASS')
        self.mimic.page=0;self.mimic.follow=True
        self.refresh_rules();self.refresh_banks();self.refresh(force=True)

    def about(self):
        messagebox.showinfo('HMI / 모델 범위','HMI v0.2.2 · 계산엔진 v0.2.0\n\n'
            '독립 추세창과 Excel 내보내기를 추가했습니다.\n'
            '물성·Cv·조건식·시퀀스·우선순위·지연차단은 동일합니다.\n\n'
            'F11 전체화면 / F5 RUN-PAUSE / Ctrl+O 열기 / Ctrl+S 저장\n'
            '밸브·용기·압축기를 클릭하면 Faceplate를 엽니다.\n\n'
            '등온 수소, 1 TT, 단일 Bank 선택 충전.\n'
            '실제 PID, 온도변화, 배관 재고, 압력파, PSV, PLC I/O 없음.',parent=self)

    def _guard(self,fn):
        try:
            fn();self.refresh()
        except (ValueError,ArithmeticError,TypeError,KeyError) as exc:
            self.running=False
            messagebox.showerror('입력 / 모델 확인',str(exc),parent=self)

    def run(self):
        self.running=True;self._last_wall=time.perf_counter()

    def pause(self):
        self.running=False;self.actual_rate=0;self.refresh()

    def single_step(self):
        self.pause();self._guard(self.sim.step)

    def reset(self):
        if self.sim.time_s>0 and not messagebox.askyesno('초기화','결과를 저장했습니까? 현재 설정의 초기상태로 돌아갑니다.',parent=self):return
        self.install_config(self.sim.cfg)

    def auto(self,base=False):
        def go():self.sim.start_auto(base);self.run()
        self._guard(go)

    def manual_route(self):
        def go():self.sim.manual();self.sim.set_route(self.route_var.get())
        self._guard(go)

    def manual_bank(self,name):
        def go():self.sim.manual();self.sim.request_bank(name)
        self._guard(go)

    def command_valve(self,tag,pct):self._guard(lambda:self.sim.set_valve(tag,pct))

    def open_route(self):
        def go():self.sim.manual();self.sim.open_route()
        self._guard(go)

    def estop(self):
        self.sim.estop();self.running=not self.sim.settled;self.refresh()

    def _tick(self):
        now=time.perf_counter()
        elapsed=max(0.0,min(.25,now-self._last_wall));self._last_wall=now
        if self.running:
            self._pending_sim=min(self._pending_sim+float(self.speed.get())*elapsed,max(2.0,float(self.speed.get())*.5))
            start=now
            try:
                while self._pending_sim>=self.sim.cfg.dt_s-1e-9 and time.perf_counter()-start<.035:
                    self._pending_sim-=self.sim.step()
                    if self.sim.settled:
                        self.running=False;self._pending_sim=0;break
            except Exception as exc:
                self.running=False;self._pending_sim=0;self.sim.log('ERROR',str(exc));messagebox.showerror('모델 계산 중지',str(exc),parent=self)
        else:self._pending_sim=0
        if now-self._last_rate[0]>=.5:
            self.actual_rate=(self.sim.time_s-self._last_rate[1])/(now-self._last_rate[0]) if self.running else 0.0
            self._last_rate=(now,self.sim.time_s)
        self.refresh();self.after(50,self._tick)

    def add_event(self):
        def go():
            p=float(self.ev_pct.get());t=float(self.ev_time.get())
            self.sim.schedule({'time_s':t,'action':'valve','target':self.ev_target.get(),'value':p,'authority':self.ev_authority.get()})
            self.refresh_events()
        self._guard(go)

    def refresh_events(self):
        for i in self.event_tree.get_children():self.event_tree.delete(i)
        for e in self.sim._due[self.sim._event_index:]:self.event_tree.insert('','end',values=(e['time_s'],e['action'],e.get('target',''),e.get('value','')))

    def load_config(self):
        self.pause();path=filedialog.askopenfilename(filetypes=[('JSON 설정','*.json')],parent=self)
        if not path:return
        def go():
            cfg=Config.load(path)
            if self.sim.time_s>0 and not messagebox.askyesno('JSON 열기','현재 실험을 초기화하고 불러옵니다. 계속합니까?',parent=self):return
            self.install_config(cfg)
        self._guard(go)

    def save_config(self):
        path=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('JSON 설정','*.json')],parent=self)
        if path:self._guard(lambda:self.sim.cfg.save(path))

    def export(self):
        path=filedialog.askdirectory(title='trend.csv / events.csv / config.json / summary.json 저장 폴더',parent=self)
        if path:
            self._guard(lambda:self.sim.export(path));messagebox.showinfo('저장','CSV와 입력조건·결과 요약을 저장했습니다.',parent=self)

    def open_trend(self,group='pressure'):
        win=TrendPopup(self,group);self.trend_windows.append(win)
        return self._present(win)

    def export_excel(self):
        try:
            snap=capture_trend_snapshot(self.sim)
            wanted=set(default_signal_keys(self.sim,'pressure'))
            signals=[s for s in signals_for(self.sim,'pressure') if s.key in wanted]
            path=filedialog.asksaveasfilename(parent=self,defaultextension='.xlsx',filetypes=[('Excel workbook','*.xlsx')],
                initialfile=f'H2_TT_{self.sim.cfg.eos}_{int(self.sim.time_s)}s.xlsx')
            if path:self.start_excel_export(path,snap,'pressure',signals)
        except (ValueError,OSError) as exc:messagebox.showerror('Excel 저장',str(exc),parent=self)

    def start_excel_export(self,path,snapshot,group,signals,start_s=None,end_s=None,parent=None):
        # Capture on GUI thread, serialize immutable snapshot in worker; no Tk calls in worker.
        result=queue.Queue()
        def work():
            try:result.put(('ok',write_trend_xlsx(path,snapshot,group,signals,start_s,end_s)))
            except Exception as exc:result.put(('error',str(exc)))
        thread=threading.Thread(target=work,daemon=True);self._export_jobs.append(thread);thread.start()
        self.hover_var.set('Excel 저장 중 · 독립 Snapshot을 저장합니다. 시뮬레이션 계산은 계속 진행됩니다.')
        def poll():
            if not self.winfo_exists():return
            try:status,payload=result.get_nowait()
            except queue.Empty:self.after(120,poll);return
            self._export_jobs=[t for t in self._export_jobs if t.is_alive()]
            target=parent if parent is not None and parent.winfo_exists() else self
            if status=='ok':
                self.hover_var.set('Excel 저장 완료: '+str(path))
                messagebox.showinfo('Excel 저장 완료',f"{payload['rows']:,}개 표본 · {payload['signals']}개 선택 변수\n"
                    f"EOS: {payload['eos']} / {payload['start_s']:.3f}–{payload['end_s']:.3f} s\n\n{path}",parent=target)
            else:messagebox.showerror('Excel 저장 실패',str(payload)+'\n파일이 Excel에서 열려 있는지도 확인하세요.',parent=target)
        self.after(120,poll)
        return thread

    def eos_help(self):
        messagebox.showinfo('수소 수취 모델 · EOS 선택',
            '설비 구성 → 압축기 / 물성 탭에서 EOS를 선택합니다.\n\n'
            'Leachman: 기본값, normal hydrogen 압력·밀도 EOS\n'
            'PR: 내장 Peng–Robinson, 순수 수소 기상근 계산\n'
            'Ideal: Z=1 비교\n'
            'CoolProp: 선택 설치된 HEOS Hydrogen 사용\n\n'
            'NRTL은 액상 혼합물 활동도 모델입니다. 고압 순수 수소의\n'
            '압력·밀도 EOS 선택항목으로는 제공하지 않습니다.\n\n'
            'PR이라는 이름만으로 HYSYS의 물성패키지와 동일한 결과를\n'
            '보장하지 않습니다. 동일 P/T에서 Z·밀도를 대조하세요.\n'
            'EOS 변경은 새 실험으로 초기화되며 Excel에 모델명이 기록됩니다.',parent=self)

    def stop(self):
        self.sim.request_stop('HOLD','Operator normal STOP');self.running=not self.sim.settled;self.refresh()

    def on_close(self):
        if any(t.is_alive() for t in self._export_jobs):
            messagebox.showinfo('Excel 저장 중','파일 저장이 끝난 뒤 프로그램을 종료하세요.',parent=self);return
        self.running=False;self.sim.close();self.destroy()

    def refresh_rules(self):
        if not hasattr(self,'rule_tree'):return
        existing=set(self.rule_tree.get_children());names={r.name for r in self.sim.cfg.rules}
        for n in existing-names:self.rule_tree.delete(n)
        for r in self.sim.cfg.rules:
            st=self.sim.rule_states[r.name]
            values=(r.name,r.priority,r.mode+'/'+r.phase,r.behavior,'ACTIVE' if st.active else 'ARMED' if r.enabled else 'OFF',st.fired_count)
            if r.name in existing:self.rule_tree.item(r.name,values=values)
            else:self.rule_tree.insert('','end',iid=r.name,values=values)

    def refresh_banks(self):
        if not hasattr(self,'bank_tree'):return
        existing=set(self.bank_tree.get_children())
        for n in existing-set(self.sim.banks):self.bank_tree.delete(n)
        for n,b in self.sim.banks.items():
            v=self.sim.valves['BANK-'+n];values=(n,f'{b.pg:.3f}',b.spec.target_barg,self.sim.cfg.hh(b.spec),f'{b.mass_kg:.3f}',f'{v.opening*100:.1f}',v.source)
            if n in existing:self.bank_tree.item(n,values=values)
            else:self.bank_tree.insert('','end',iid=n,values=values)

    def delete_rule(self):
        if not self.rule_tree.selection():return
        name=self.rule_tree.selection()[0]
        if messagebox.askyesno('규칙 삭제',name+' 규칙을 삭제합니까?',parent=self):
            self._guard(lambda:self.sim.replace_rules([r for r in self.sim.cfg.rules if r.name!=name]));self.refresh_rules()

    def reset_rule(self):
        if self.rule_tree.selection():self._guard(lambda:self.sim.reset_rule(self.rule_tree.selection()[0]));self.refresh_rules()


# ===== main.py =====
"""Launch the offline HMI or run the same engine headlessly."""
from pathlib import Path
import argparse
import json
import sys


def main():
    p=argparse.ArgumentParser(description='H2 TT Receiving HMI v0.2.2 (engine v0.2.0) — offline isothermal simulator')
    p.add_argument('--config',type=Path)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--auto',action='store_true')
    p.add_argument('--compress-only',action='store_true')
    p.add_argument('--hours',type=float,default=12.0)
    p.add_argument('--out',type=Path,default=Path('results'))
    p.add_argument('--xlsx',type=Path,help='Headless: write full journal + editable pressure chart')
    args=p.parse_args()
    try:
        cfg=Config.load(args.config) if args.config else Config()
        if args.headless:
            if not 0<args.hours<=168:raise ValueError('--hours: 0~168시간')
            s=Simulator(cfg)
            try:
                if args.auto or args.compress_only:s.start_auto(args.compress_only)
                end=args.hours*3600
                while s.time_s<end-1e-8:
                    s.step(min(cfg.dt_s,end-s.time_s))
                    if s.phase in ('DONE','TRIP') and s.settled:break
                s.export(args.out)
                if args.xlsx:
                    keys=set(default_signal_keys(s,'pressure'))
                    write_trend_xlsx(args.xlsx,capture_trend_snapshot(s),'pressure',[x for x in signals_for(s,'pressure') if x.key in keys])
                print(json.dumps(s.snapshot(),ensure_ascii=False,indent=2))
            finally:s.close()
        else:
            app=App(cfg)
            if args.auto or args.compress_only:app.auto(args.compress_only)
            app.mainloop()
    except (ValueError,RuntimeError,ImportError,ArithmeticError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
