import copy
import math
import unittest
from h2sim.engine import Config, Simulator, Valve, ValveSpec
from h2sim.physics import HydrogenEOS, PATM_BAR, valve_flow, series_flow


# v0.1 regression uses the explicit ideal-stop compatibility mode.
# v0.2 DELAYED behavior is tested separately in test_v02.py.
_BaseConfig = Config
class Config(_BaseConfig):
    def __init__(self, **kwargs):
        kwargs.setdefault('shutdown_mode','IDEAL')
        kwargs.setdefault('compressor_stop_s',0.0)
        super().__init__(**kwargs)


class PhysicsTests(unittest.TestCase):
    def test_leachman_report_reference_points(self):
        e=HydrogenEOS('Leachman')
        for pg,rho in [(10,.8341091872123317),(20,1.5825728169030746),
                       (190,13.096556426214816),(200,13.707809565063345)]:
            self.assertAlmostEqual(e.density(pg+PATM_BAR,318.15),rho,places=10)

    def test_leachman_derivative_and_roundtrip(self):
        e=HydrogenEOS('Leachman')
        for T in (253.15,303.15,318.15,423.15):
            for P in (1.01325,8.01325,21.01325,201.01325,351.01325):
                rho=e.density(P,T)
                self.assertAlmostEqual(e.pressure(rho,T),P,places=8)
                p,der,z=e._leachman_pd(rho,T)
                h=rho*1e-5
                finite=(e.pressure(rho+h,T)-e.pressure(rho-h,T))/(2*h)
                self.assertAlmostEqual(der,finite,delta=abs(der)*1e-7)

    def test_z_interpolation(self):
        e=HydrogenEOS('Leachman')
        for p in (1.03325,8.01325,11.01325,27.456,201.01325,350.77):
            exact=e._leachman_pd(e.density(p,318.15),318.15)[2]
            self.assertAlmostEqual(e.z(p,318.15),exact,delta=2e-8)

    def test_pr_roundtrip(self):
        e=HydrogenEOS('PR')
        for temp in (253.15,303.15,318.15,373.15):
            for p in (1.01325,8.01325,21.01325,201.01325,301.01325):
                self.assertAlmostEqual(e.pressure(e.density(p,temp),temp),p,places=8)

    def test_cv_reference(self):
        # Prior worksheet: 20 -> 10 barg, 45 C, Z=1.011938, required Cv ~0.319.
        f=valve_flow(21.01325,11.01325,318.15,.318666864,1.011938084)
        self.assertAlmostEqual(f.kg_h,27.0193349,delta=.002)
        self.assertFalse(f.choked)

    def test_choking_plateau(self):
        a=valve_flow(201.01325,30,318.15,.3,1.11)
        b=valve_flow(201.01325,1.01325,318.15,.3,1.11)
        self.assertTrue(a.choked and b.choked)
        self.assertAlmostEqual(a.kg_h,b.kg_h,places=12)

    def test_closed_and_no_reverse(self):
        self.assertEqual(valve_flow(200,20,318.15,0).kg_h,0)
        self.assertEqual(valve_flow(20,200,318.15,1).kg_h,0)

    def test_series_identical_half_pressure(self):
        e=HydrogenEOS('Ideal')
        f=series_flow(20,10,300,[.5,.5],e)
        # Independently solve the internal pressure from q1(p)=q2(p).
        lo=10;hi=20
        for _ in range(70):
            mid=(lo+hi)/2
            a=valve_flow(20,mid,300,.5).kg_h;b=valve_flow(mid,10,300,.5).kg_h
            if a>b:lo=mid
            else:hi=mid
        ref=valve_flow(20,(lo+hi)/2,300,.5).kg_h
        self.assertAlmostEqual(f.kg_h,ref,delta=1e-5)

    def test_series_not_more_than_single(self):
        e=HydrogenEOS('PR')
        for p2 in (1.01325,21.01325,150):
            single=valve_flow(201.01325,p2,318.15,.3,e.z(201.01325,318.15)).kg_h
            series=series_flow(201.01325,p2,318.15,[1.5,.3,.5],e).kg_h
            self.assertGreater(series,0);self.assertLessEqual(series,single+1e-5)

    def test_stroke(self):
        v=Valve(ValveSpec(.3,4),1,0)
        self.assertAlmostEqual(v.position_after(1),.25)
        self.assertEqual(v.position_after(10),1)

    def test_equal_percent_shutoff(self):
        v=Valve(ValveSpec(.6,3,'Equal percentage',50))
        self.assertEqual(v.effective_cv(0),0)
        self.assertAlmostEqual(v.effective_cv(1),.6)
        self.assertGreater(v.effective_cv(.3),0)
        self.assertLess(v.effective_cv(.3),v.effective_cv(.6))


class ModelTests(unittest.TestCase):
    def manual_case(self,dt=.5):
        c=Config();c.dt_s=dt
        s=Simulator(c)
        s.request_bank('C');s.set_valve('MOV-101',100);s.set_valve('BYPASS',100)
        return s

    def test_closed_system_no_flow(self):
        s=Simulator();m=s.tt.mass_kg
        for _ in range(20):s.step()
        self.assertEqual(m,s.tt.mass_kg);self.assertEqual(s.flow_kg_h,0)

    def test_open_close_mass_conservation(self):
        s=self.manual_case();initial=s.tt.mass_kg
        for _ in range(120):s.step()
        self.assertLess(s.tt.mass_kg,initial);self.assertGreater(s.banks['C'].pg,20)
        s.set_valve('BYPASS',0)
        for _ in range(20):s.step()
        mass=s.tt.mass_kg;pressure=s.banks['C'].pg
        for _ in range(50):s.step()
        self.assertAlmostEqual(s.tt.mass_kg,mass,places=10)
        self.assertAlmostEqual(s.banks['C'].pg,pressure,places=10)
        self.assertAlmostEqual(s.mass_error_kg,0,places=8)

    def test_estop_latched(self):
        s=self.manual_case()
        for _ in range(20):s.step()
        s.estop();mass=s.tt.mass_kg
        for _ in range(10):s.step()
        self.assertEqual(s.tt.mass_kg,mass);self.assertTrue(s.tripped)
        with self.assertRaises(ValueError):s.set_valve('BYPASS',100)

    def test_single_bank_interlock(self):
        s=self.manual_case()
        for _ in range(10):s.step()
        s.request_bank('A')
        for _ in range(30):
            s.step()
            self.assertLessEqual(sum(v.opening>1e-8 for k,v in s.valves.items() if k.startswith('BANK-')),1)
        self.assertEqual(s.active_bank(),'A')

    def test_dt_consistency(self):
        result=[]
        for dt in (.25,.5,1):
            s=self.manual_case(dt)
            for _ in range(round(120/dt)):s.step()
            result.append(s.tt.pg)
        self.assertLess(max(result)-min(result),.002)

    def test_volume_sensitivity(self):
        s1=self.manual_case();c=Config();c.banks[2].volume_m3*=2;s2=Simulator(c)
        s2.request_bank('C');s2.set_valve('MOV-101',100);s2.set_valve('BYPASS',100)
        for _ in range(120):s1.step();s2.step()
        self.assertGreater(s1.banks['C'].pg,s2.banks['C'].pg)

    def test_cv_sensitivity(self):
        s1=self.manual_case();s2=self.manual_case();s2.valves['BYPASS'].spec.cv=.15
        for _ in range(30):s1.step();s2.step()
        self.assertGreater(s1.bypass_kg,s2.bypass_kg)

    def test_compressor_bank_valve_limit(self):
        s=Simulator();s.set_route('RECOVERY');s.request_bank('C')
        for tag in ('MOV-101','PRV-102','COMP-OUT'):s.set_valve(tag,100)
        s.set_compressor(True)
        for _ in range(40):s.step()
        self.assertAlmostEqual(s.flow_kg_h,22.48,delta=.01)
        s.set_valve('COMP-OUT',0)
        for _ in range(40):s.step()
        self.assertEqual(s.flow_kg_h,0)
        self.assertFalse(s.compressor_command)

    def test_invalid_config(self):
        for attr,val in [('xt',0),('dt_s',-1),('compressor_kg_h',float('nan'))]:
            c=Config();setattr(c,attr,val)
            with self.assertRaises(ValueError):Simulator(c)

    def test_json_roundtrip(self):
        from dataclasses import asdict
        a=Config();b=Config.from_dict(asdict(a));self.assertEqual(asdict(a),asdict(b))

    def test_equalization_does_not_reverse(self):
        s=self.manual_case(1)
        for _ in range(4000):s.step()
        self.assertGreaterEqual(s.tt.pg+1e-7,s.banks['C'].pg)
        self.assertAlmostEqual(s.tt.pg,s.banks['C'].pg,delta=.01)
        self.assertAlmostEqual(s.mass_error_kg,0,places=7)

if __name__=='__main__':unittest.main(verbosity=2)
