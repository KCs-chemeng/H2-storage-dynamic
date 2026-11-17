"""Pure-hydrogen EOS and quasi-steady compressible valve flow.

SI internally except valve pressures [bar abs] and flow [kg/h].
N8=94.8: Fisher/ISA mass-flow form with pressure in bar, temperature K,
M in kg/kmol. Do not substitute Kv for Cv.
"""
from __future__ import annotations
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
