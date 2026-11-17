"""v0.2: finite-inventory receiving model with explicit command arbitration.

One TT, configurable parallel banks, one bypass and optional equivalent compressor.
All reservoirs isothermal. No PLC I/O, acoustic/thermal/PSV model.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import deque
import copy
import csv
import json
import math
import tempfile
import shutil
from .config import Config, VesselSpec, ValveSpec, RuleSpec, Action, finite
from .controls import RuleState, Request, PRIORITIES, arbitrate, expression
from .physics import HydrogenEOS, PATM_BAR, normal_flow, series_flow, valve_flow

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
