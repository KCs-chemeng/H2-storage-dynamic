import copy
import csv
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from h2sim.engine import Config, Simulator, VesselSpec, ValveSpec
from h2sim.config import RuleSpec, Condition as C, Action as A, CORE_VALVES
from h2sim.controls import RuleState, expression, Request, arbitrate


def bank_config(names=('A',), mode='DELAYED', volume=1.0, target=30.0):
    c=Config(eos='Ideal',shutdown_mode=mode,dt_s=.1,sample_s=.2)
    c.banks=[VesselSpec(n,volume,20,45,target,210) for n in names]
    c.valves={k:v for k,v in c.valves.items() if k in CORE_VALVES}
    c.equalize_order=list(names);c.recovery_order=list(names)
    c.normalize().validate()
    return c

class RevisionTests(unittest.TestCase):
    def setUp(self):self.sims=[]
    def tearDown(self):
        for s in self.sims:s.close()
    def make(self,c=None):
        s=Simulator(c);self.sims.append(s);return s
    def run_for(self,s,seconds):
        end=s.time_s+seconds
        while s.time_s<end-1e-8:s.step(min(s.cfg.dt_s,end-s.time_s))
    def open(self,s):
        s.request_bank(next(iter(s.banks)));s.set_valve('MOV-101',100);s.set_valve('BYPASS',100)

    def test_arbitrary_bank_count_1_5_12_32(self):
        for n in (1,5,12,32):
            s=self.make(bank_config([f'T{i}' for i in range(n)]));s.start_auto()
            self.run_for(s,4)
            self.assertEqual(len(s.banks),n);self.assertGreater(s.bypass_kg,0)
            self.assertAlmostEqual(s.mass_error_kg,0,places=9)

    def test_bank_names_not_final_character(self):
        s=self.make(bank_config(['BANK_LONG_10','SecondBank']));s.request_bank('SecondBank')
        s.set_valve('MOV-101',100);s.set_valve('BYPASS',100);self.run_for(s,5)
        self.assertGreater(s.banks['SecondBank'].pg,20)
        self.assertAlmostEqual(s.banks['BANK_LONG_10'].pg,20)

    def test_legacy_config_import_preserves_ideal_mode(self):
        path=Path(__file__).parents[1]/'examples/legacy_v01.json'
        c=Config.load(path);self.assertEqual(c.version,'0.2.0');self.assertEqual(c.shutdown_mode,'IDEAL')
        self.assertEqual(len(c.banks),3)

    def test_no_compressor_template(self):
        c=bank_config();c.compressor_enabled=False;c.sequence_mode='EQUALIZE_ONLY';c.validate()
        s=self.make(c);s.start_auto();self.run_for(s,50)
        self.assertEqual(s.phase,'DONE');self.assertEqual(s.compressed_kg,0)
        with self.assertRaises(ValueError):s.start_auto(True)

    def test_live_sp_keeps_mass_time_history(self):
        s=self.make(bank_config());self.open(s);self.run_for(s,5)
        state=(s.time_s,s.tt.mass_kg,s.banks['A'].mass_kg,len(s.history))
        s.apply_setpoints({'bank.A.target_barg':31,'tt_stop_barg':9})
        self.assertEqual(state[:3],(s.time_s,s.tt.mass_kg,s.banks['A'].mass_kg))
        self.assertGreaterEqual(len(s.history),state[3]);self.assertEqual(s.banks['A'].spec.target_barg,31)

    def test_live_invalid_transaction_is_atomic(self):
        s=self.make(bank_config());before=asdict(s.cfg)
        with self.assertRaises(ValueError):s.apply_setpoints({'tt_stop_barg':9,'bank.A.target_barg':500})
        self.assertEqual(before,asdict(s.cfg))

    def test_live_geometry_change_rejected(self):
        s=self.make(bank_config())
        with self.assertRaises(ValueError):s.apply_setpoints({'bank.A.volume_m3':5})

    def test_live_lower_target_triggers_stop_no_clipping(self):
        s=self.make(bank_config(target=80));s.start_auto();self.run_for(s,8)
        before=s.banks['A'].pg
        s.apply_setpoints({'bank.A.target_barg':before-.1})
        self.assertEqual(s.phase,'SWITCH');self.run_for(s,5)
        self.assertGreater(s.banks['A'].pg,before)

    def test_delayed_shutdown_residual_inflow(self):
        s=self.make(bank_config(target=180));self.open(s);self.run_for(s,7)
        m=s.tt.mass_kg;s.request_stop('HOLD','test')
        self.assertGreater(s.valves['BYPASS'].opening,0)
        self.run_for(s,6)
        self.assertEqual(s.phase,'HOLD');self.assertLess(s.tt.mass_kg,m)
        self.assertGreater(s.stop_history[-1]['residual_inflow_kg'],0)
        self.assertEqual(s.flow_kg_h,0)

    def test_ideal_shutdown_no_residual(self):
        s=self.make(bank_config(mode='IDEAL'));self.open(s);self.run_for(s,6)
        m=s.tt.mass_kg;s.request_stop('HOLD','test');self.run_for(s,5)
        self.assertEqual(s.tt.mass_kg,m);self.assertEqual(s.valves['BYPASS'].opening,0)

    def test_delayed_auto_target_overshoot(self):
        s=self.make(bank_config(target=21));s.start_auto();self.run_for(s,10)
        self.assertEqual(s.phase,'DONE');self.assertGreater(s.banks['A'].pg,21)
        self.assertLess(s.banks['A'].pg,40)

    def test_manual_target_policy(self):
        a=self.make(bank_config(target=21));self.open(a);self.run_for(a,8)
        self.assertGreater(a.banks['A'].pg,21);self.assertEqual(a.phase,'MANUAL')
        c=bank_config(target=21);c.manual_target_policy='ALL_MODES'
        b=self.make(c);self.open(b);self.run_for(b,8)
        self.assertEqual(b.phase,'HOLD')

    def test_delayed_trip_latch_and_reset_without_reinitializing(self):
        s=self.make(bank_config(target=180));self.open(s);self.run_for(s,5);s.estop()
        self.assertTrue(s.tripped);self.assertEqual(s.phase,'TRIPPING')
        with self.assertRaises(ValueError):s.set_valve('BYPASS',100)
        with self.assertRaises(ValueError):s.reset_trip()
        self.run_for(s,5);t=s.time_s;m=s.tt.mass_kg
        s.reset_trip();self.assertEqual((s.time_s,s.tt.mass_kg),(t,m));self.assertFalse(s.tripped)
        self.assertEqual(s.phase,'READY')

    def test_per_bank_hh_applies_manual(self):
        c=bank_config(target=20.5);c.banks[0].hh_barg=21.0
        s=self.make(c);self.open(s);self.run_for(s,8)
        self.assertTrue(s.tripped);self.assertEqual(s.phase,'TRIP')
        with self.assertRaises(ValueError):s.reset_trip()

    def test_priority_close_wins_equal(self):
        winner=arbitrate([Request(1,50,'open'),Request(0,50,'close')]);self.assertEqual(winner.value,0)
        winner=arbitrate([Request(1,60,'operator'),Request(0,80,'protect')]);self.assertEqual(winner.value,0)

    def test_protection_rule_overrides_manual(self):
        c=bank_config(target=180)
        c.rules=[RuleSpec('P-CLOSE',[C('time_s','>=',3)],[A('valve','BYPASS',0)],priority='PROTECTION',behavior='LATCH')]
        s=self.make(c);self.open(s);self.run_for(s,4)
        s.set_valve('BYPASS',100);self.run_for(s,3)
        self.assertEqual(s.valves['BYPASS'].command,0);self.assertEqual(s.valves['BYPASS'].opening,0)
        self.assertIn('P-CLOSE',s.valves['BYPASS'].source)

    def test_normal_rule_does_not_override_manual(self):
        c=bank_config(target=180);c.rules=[RuleSpec('N-CLOSE',[C('time_s','>=',1)],[A('valve','BYPASS',0)])]
        s=self.make(c);self.open(s);self.run_for(s,5)
        self.assertEqual(s.valves['BYPASS'].command,1);self.assertEqual(s.valves['BYPASS'].source,'OPERATOR')
        s.release_overrides();self.run_for(s,5);self.assertEqual(s.valves['BYPASS'].opening,0)

    def test_rules_and_or_evaluation(self):
        vals={'x':10,'y':2}
        self.assertFalse(expression([C('x','>',5),C('y','>',3)],'ALL',vals))
        self.assertTrue(expression([C('x','>',5),C('y','>',3)],'ANY',vals))

    def test_hold_timer_continuous_and_resets(self):
        rule=RuleSpec('r',[C('flow_kg_h','>',10)],[A('stop')],hold_s=2)
        st=RuleState();v={'mode':'AUTO','phase':'EQ_A','flow_kg_h':11}
        st.evaluate(rule,v,0);self.assertFalse(st.active)
        st.evaluate(rule,v,1.5);self.assertFalse(st.active)
        st.evaluate(rule,dict(v,flow_kg_h=0),1.7)
        st.evaluate(rule,v,2);st.evaluate(rule,v,3.9);self.assertFalse(st.active)
        st.evaluate(rule,v,4);self.assertTrue(st.active)

    def test_hysteresis_no_chatter(self):
        r=RuleSpec('r',[C('selected.pressure','>=',120)],[A('valve','BYPASS',0)],
                   reset_conditions=[C('selected.pressure','<=',115)])
        st=RuleState();v={'mode':'AUTO','phase':'EQ_A','selected.pressure':121}
        st.evaluate(r,v,0);self.assertTrue(st.active)
        st.evaluate(r,dict(v,**{'selected.pressure':118}),1);self.assertTrue(st.active)
        st.evaluate(r,dict(v,**{'selected.pressure':114}),2);self.assertFalse(st.active)

    def test_rule_scope_and_active_latch(self):
        r=RuleSpec('r',[C('time_s','>=',1)],[A('valve','BYPASS',0)],mode='AUTO',behavior='LATCH')
        st=RuleState();v={'mode':'MANUAL','phase':'MANUAL','time_s':2}
        st.evaluate(r,v,2);self.assertFalse(st.active)
        st.evaluate(r,dict(v,mode='AUTO'),3);self.assertTrue(st.active)
        st.evaluate(r,v,4);self.assertTrue(st.active)

    def test_once_not_retrigger_after_auto_release(self):
        r=RuleSpec('once',[C('x','>=',10)],[A('stop')],behavior='ONCE',reset_conditions=[C('x','<=',5)])
        st=RuleState();v={'mode':'AUTO','phase':'EQ_A','x':11}
        st.evaluate(r,v,0);st.evaluate(r,dict(v,x=4),1);st.evaluate(r,v,2)
        self.assertEqual(st.fired_count,1);self.assertFalse(st.active)

    def test_safety_rule_open_rejected(self):
        c=bank_config();c.rules=[RuleSpec('bad',[C('time_s','>',1)],[A('valve','BYPASS',100)],priority='PROTECTION')]
        with self.assertRaises(ValueError):c.validate()

    def test_no_code_evaluation_and_invalid_variable(self):
        c=bank_config();c.rules=[RuleSpec('bad',[C("__import__('os').system('echo bad')",'>',0)],[A('stop')])]
        with self.assertRaises(ValueError):c.validate()

    def test_automatic_scheduled_event_preserves_auto(self):
        c=bank_config(target=180);c.events=[{'time_s':4,'action':'valve','target':'BYPASS','value':40,'authority':'automatic'}]
        s=self.make(c);s.start_auto();self.run_for(s,6)
        self.assertEqual(s.mode,'AUTO');self.assertAlmostEqual(s.valves['BYPASS'].command,.4)

    def test_operator_scheduled_event_changes_to_manual(self):
        c=bank_config(target=180);c.events=[{'time_s':4,'action':'valve','target':'BYPASS','value':40,'authority':'operator'}]
        s=self.make(c);s.start_auto();self.run_for(s,6)
        self.assertEqual(s.mode,'MANUAL');self.assertAlmostEqual(s.valves['BYPASS'].command,.4)

    def test_event_boundary_splits_timestep(self):
        c=bank_config();c.dt_s=.5;c.sample_s=.5;c.events=[{'time_s':.3,'action':'manual'}]
        s=self.make(c);s.step();self.assertAlmostEqual(s.time_s,.3)
        s.step();self.assertTrue(any(e['code']=='EVENT' and e['time_s']==.3 for e in s.events_log))

    def test_switch_bank_waits_actual_closed(self):
        s=self.make(bank_config(['A','B']));self.open(s);self.run_for(s,6);s.request_bank('B')
        for _ in range(60):
            s.step();self.assertLessEqual(sum(v.opening>1e-8 for k,v in s.valves.items() if k.startswith('BANK-')),1)
        self.assertEqual(s.active_bank(),'B')

    def test_route_change_break_before_make(self):
        s=self.make(bank_config(target=180));self.open(s);self.run_for(s,5)
        s.set_route('RECOVERY');self.assertEqual(s.route,'BYPASS')
        self.run_for(s,5);self.assertEqual(s.route,'RECOVERY');self.assertEqual(s.valves['BYPASS'].opening,0)

    def test_rule_next_stage_after_pressure_condition(self):
        c=bank_config(['A','B'],volume=1,target=180)
        c.rules=[RuleSpec('next_A',[C('bank.A.pressure','>=',22)],[A('next')],mode='AUTO',phase='EQ',behavior='ONCE')]
        s=self.make(c);s.start_auto();self.run_for(s,20)
        self.assertEqual(s.rule_states['next_A'].fired_count,1)
        self.assertGreater(s.banks['B'].pg,20)

    def test_rule_edit_keeps_state(self):
        s=self.make(bank_config());self.open(s);self.run_for(s,2)
        before=(s.time_s,s.tt.mass_kg)
        s.replace_rules([RuleSpec('sample',[C('time_s','>=',10)],[A('stop')])])
        self.assertEqual(before,(s.time_s,s.tt.mass_kg))

    def test_active_safety_rule_cannot_be_deleted(self):
        c=bank_config();c.rules=[RuleSpec('safe',[C('time_s','>=',0)],[A('valve','BYPASS',0)],priority='PROTECTION')]
        s=self.make(c);s.step()
        with self.assertRaises(ValueError):s.replace_rules([])
        with self.assertRaises(ValueError):s.reset_rule('safe')

    def test_stop_terminal_rules_cannot_reopen(self):
        c=bank_config(target=180);c.rules=[RuleSpec('open',[C('time_s','>=',0)],[A('valve','BYPASS',100)],behavior='LATCH')]
        s=self.make(c);s.start_auto();self.run_for(s,6);s.request_stop('HOLD','test');self.run_for(s,10)
        self.assertEqual(s.valves['BYPASS'].opening,0);self.assertEqual(s.phase,'HOLD')

    def test_full_trace_export_and_live_sp_log(self):
        s=self.make(bank_config());self.open(s);self.run_for(s,2);s.apply_setpoints({'bank.A.target_barg':32});self.run_for(s,2)
        with tempfile.TemporaryDirectory() as td:
            s.export(td)
            with open(Path(td)/'trend.csv',encoding='utf-8-sig') as fh:rows=list(csv.DictReader(fh))
            self.assertEqual(float(rows[0]['time_s']),0)
            self.assertEqual(float(rows[-1]['time_s']),s.time_s)
            self.assertEqual(float(rows[-1]['bank_A_target_barg']),32)
            self.assertTrue(any(e['code']=='SP_CHANGE' for e in s.events_log))

    def test_delayed_dt_sensitivity(self):
        result=[]
        for dt in (.05,.1,.2):
            c=bank_config(target=23);c.dt_s=dt;c.sample_s=.2
            s=self.make(c);s.start_auto();self.run_for(s,14)
            result.append(s.banks['A'].pg)
        self.assertLess(max(result)-min(result),.3)

    def test_config_roundtrip_with_rule(self):
        c=bank_config();c.rules=[RuleSpec('r',[C('bank.A.pressure','>=',25)],[A('stop')],hold_s=1)]
        d=Config.from_dict(asdict(c));self.assertEqual(asdict(c),asdict(d))

    def test_manual_bank_selection_beats_normal_rule(self):
        c=bank_config(['A','B'],target=180)
        c.rules=[RuleSpec('b_open',[C('time_s','>=',0)],[A('valve','BANK-B',100)])]
        s=self.make(c);self.open(s);self.run_for(s,6)
        self.assertEqual(s.active_bank(),'A')
        self.assertEqual(s.valves['BANK-B'].opening,0)

    def test_auto_release_cannot_release_operator_command(self):
        s=self.make(bank_config(target=180));self.open(s);self.run_for(s,3)
        s.schedule({'time_s':4,'action':'release','target':'BYPASS','authority':'automatic'})
        self.run_for(s,3);self.assertEqual(s.valves['BYPASS'].command,1)
        self.assertEqual(s.valves['BYPASS'].source,'OPERATOR')

    def test_rule_cannot_change_manual_route(self):
        c=bank_config(target=180)
        c.rules=[RuleSpec('route',[C('time_s','>=',1)],[A('route',value='RECOVERY')])]
        s=self.make(c);self.open(s);self.run_for(s,3)
        self.assertEqual(s.route,'BYPASS')
        self.assertTrue(any(e['code']=='RULE_REJECTED' for e in s.events_log))

if __name__=='__main__':unittest.main(verbosity=2)
