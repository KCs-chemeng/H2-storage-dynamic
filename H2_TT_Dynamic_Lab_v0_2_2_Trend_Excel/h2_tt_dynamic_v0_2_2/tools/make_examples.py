from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from h2sim.config import Config,VesselSpec,ValveSpec,RuleSpec,Condition as C,Action as A,CORE_VALVES
out=Path(__file__).parents[1]/'examples'
base=Config();base.save(out/'project_default_v02.json')
# Five bank configurable topology, unchanged pure hydrogen/isothermal physics.
five=Config();five.tt.volume_m3=5
five.banks=[VesselSpec('B'+str(i),2.0,20,45,175,210) for i in range(1,6)]
five.valves={k:v for k,v in five.valves.items() if k in CORE_VALVES}
five.equalize_order=[b.name for b in five.banks];five.recovery_order=list(reversed(five.equalize_order))
five.normalize().save(out/'five_bank_receiving.json')
# A reproducible pressure-triggered, delayed shutdown demo. No vendor settings claimed.
demo=Config();demo.eos='Ideal';demo.dt_s=.1;demo.sample_s=.1
demo.banks=[VesselSpec('A',1.0,20,45,100,210)]
demo.valves={k:v for k,v in demo.valves.items() if k in CORE_VALVES}
demo.equalize_order=['A'];demo.recovery_order=['A'];demo.normalize()
demo.rules=[RuleSpec('P23_STOP',[C('bank.A.pressure','>=',23),C('flow_kg_h','>',0)],
                    [A('stop')],hold_s=.5,mode='AUTO',phase='EQ',behavior='ONCE')]
demo.save(out/'pressure_rule_stop.json')
# A timed automatic override does not force MANUAL.
pulse=Config();pulse.events=[
    {'time_s':60,'action':'valve','target':'BYPASS','value':0,'authority':'automatic'},
    {'time_s':100,'action':'valve','target':'BYPASS','value':50,'authority':'automatic'},
    {'time_s':150,'action':'release','target':'BYPASS','authority':'automatic'},
    {'time_s':210,'action':'estop','authority':'automatic'}]
pulse.save(out/'auto_preserving_valve_pulse.json')
# Hysteresis / AND+OR editor example. Pressure cannot fall after isolation in this isothermal model.
hys=Config();hys.rules=[RuleSpec('C_PRESSURE_HYSTERESIS',[C('bank.C.pressure','>=',120)],
    [A('valve','BYPASS',0)],hold_s=2,mode='AUTO',phase='EQ',behavior='WHILE',
    reset_conditions=[C('bank.C.pressure','<=',115)],reset_hold_s=1),
    RuleSpec('TIME_OR_PRESSURE_TRIP',[C('tt.pressure','<=',12),C('time_s','>=',50000)],
    [A('trip')],combine='ANY',hold_s=1,priority='PROTECTION',behavior='LATCH',enabled=False)]
hys.save(out/'hysteresis_rule_template.json')
# Compressor-free template.
direct=Config();direct.compressor_enabled=False;direct.sequence_mode='EQUALIZE_ONLY';direct.save(out/'equalization_only.json')
