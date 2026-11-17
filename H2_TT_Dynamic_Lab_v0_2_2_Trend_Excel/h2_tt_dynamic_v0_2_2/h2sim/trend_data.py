"""Read-only trend snapshots. Never changes simulator time, state or journal samples."""
from __future__ import annotations
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
