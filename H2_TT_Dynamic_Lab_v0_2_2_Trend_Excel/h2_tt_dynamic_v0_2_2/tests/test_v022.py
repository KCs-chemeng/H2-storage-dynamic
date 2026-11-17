import copy
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
from h2sim.engine import Simulator
from h2sim.config import Config
from h2sim.trend_data import capture_trend_snapshot,read_journal,signals_for,select_rows,thin_for_plot,field_unit
from h2sim.xlsx_export import write_trend_xlsx
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}
class ExportTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=Simulator(Config(eos='Ideal',sample_s=.5));self.s.start_auto()
  for _ in range(20):self.s.step()
 def tearDown(self):self.s.close();self.temp.cleanup()
 def write(self,group='pressure',lo=None,hi=None):
  self.snap=capture_trend_snapshot(self.s);self.path=Path(self.temp.name)/'result.xlsx'
  self.result=write_trend_xlsx(self.path,self.snap,group,signals_for(self.s,group),lo,hi)
  return self.path
 def test_capture_read_only(self):
  before=(copy.deepcopy(self.s.snapshot()),self.s._sample_count,len(self.s.events_log),len(self.s.history))
  capture_trend_snapshot(self.s)
  self.assertEqual(before,(self.s.snapshot(),self.s._sample_count,len(self.s.events_log),len(self.s.history)))
 def test_full_disk_journal_not_display_buffer(self):
  self.s.history.clear();snap=capture_trend_snapshot(self.s)
  self.assertEqual(len(snap.rows),self.s._sample_count);self.assertEqual(snap.rows[0]['time_s'],0)
 def test_unsampled_endpoint(self):
  self.s.cfg.sample_s=30;self.s.step();snap=capture_trend_snapshot(self.s)
  self.assertEqual(snap.rows[-1]['time_s'],self.s.time_s);self.assertGreater(len(snap.rows),self.s._sample_count)
 def test_snapshot_independent(self):
  snap=capture_trend_snapshot(self.s);last=copy.deepcopy(snap.rows[-1]);self.s.step();self.assertEqual(snap.rows[-1],last)
 def test_numeric_types(self):
  snap=capture_trend_snapshot(self.s)
  self.assertIsInstance(snap.rows[0]['time_s'],float);self.assertIsInstance(snap.rows[0]['tripped'],int);self.assertIsInstance(snap.rows[0]['eos'],str)
 def test_range_inclusive(self):
  data=select_rows(read_journal(self.s),2,4);self.assertEqual(data[0]['time_s'],2);self.assertEqual(data[-1]['time_s'],4)
 def test_invalid_range(self):
  with self.assertRaises(ValueError):select_rows([],5,4)
  with self.assertRaises(ValueError):select_rows([],float('nan'),9)
 def test_display_thinning_preserves_extrema(self):
  rows=[{'time_s':i,'x':999 if i==371 else i%8} for i in range(10000)];data=thin_for_plot(rows,['x'],200)
  self.assertEqual(data[0],rows[0]);self.assertEqual(data[-1],rows[-1]);self.assertEqual(max(r['x'] for r in data),999)
 def test_signal_definitions(self):
  keys=[s.key for s in signals_for(self.s,'opening')]
  self.assertIn('BYPASS_actual_pct',keys);self.assertIn('BYPASS_command_pct',keys)
  self.assertIn('bank_A_target_barg',[s.key for s in signals_for(self.s,'pressure')])
 def test_xlsx_structure_numeric_chart(self):
  self.write()
  with zipfile.ZipFile(self.path) as z:
   self.assertIsNone(z.testzip())
   for n in z.namelist():
    if n.endswith(('.xml','.rels')):ET.fromstring(z.read(n))
   chart=ET.fromstring(z.read('xl/charts/chart1.xml'));self.assertIsNotNone(chart.find('.//c:scatterChart',NS))
   self.assertTrue(all("'선택추세'!$B$29" in x.text for x in chart.findall('.//c:xVal/c:numRef/c:f',NS)))
   wb=ET.fromstring(z.read('xl/workbook.xml'))
   self.assertEqual([x.attrib['name'] for x in wb.findall('s:sheets/s:sheet',NS)],['요약','선택추세','전체기록','이벤트','입력조건','변수설명'])
 def test_no_sample_loss_in_export(self):
  self.write()
  with zipfile.ZipFile(self.path) as z:
   sh=ET.fromstring(z.read('xl/worksheets/sheet3.xml'));rows=[r for r in sh.findall('s:sheetData/s:row',NS) if int(r.attrib['r'])>=5]
   self.assertEqual(len(rows),len(self.snap.rows))
 def test_formula_caches(self):
  self.write()
  with zipfile.ZipFile(self.path) as z:
   sh=ET.fromstring(z.read('xl/worksheets/sheet3.xml'));b=sh.find('.//s:c[@r="B6"]',NS)
   self.assertEqual(b.find('s:f',NS).text,'A6/60');self.assertAlmostEqual(float(b.find('s:v',NS).text),.5/60)
 def test_selected_range_export(self):
  self.write(lo=2,hi=4);self.assertEqual(self.result['start_s'],2);self.assertEqual(self.result['end_s'],4);self.assertEqual(self.result['rows'],5)
 def test_empty_range_fails(self):
  with self.assertRaises(ValueError):self.write(lo=100,hi=200)
  self.assertFalse(self.path.exists())
 def test_string_injection_safe(self):
  self.s.log('TEST','=1+1 <수소> & test');self.write()
  with zipfile.ZipFile(self.path) as z:
   sh=ET.fromstring(z.read('xl/worksheets/sheet4.xml'))
   cell=next(c for c in sh.findall('.//s:c',NS) if c.find('s:is/s:t',NS) is not None and c.find('s:is/s:t',NS).text=='=1+1 <수소> & test')
   self.assertEqual(cell.attrib['t'],'inlineStr');self.assertIsNone(cell.find('s:f',NS))
 def test_eos_and_input_metadata(self):
  self.write()
  with zipfile.ZipFile(self.path) as z:
   xml=z.read('xl/worksheets/sheet5.xml').decode();self.assertIn('Ideal',xml);self.assertIn('초기',xml);self.assertIn('현재',xml)
 def test_no_external_links(self):
  self.write()
  with zipfile.ZipFile(self.path) as z:self.assertFalse(any('externalLink' in n or 'vba' in n for n in z.namelist()))
 def test_each_group_export(self):
  for group in ['flow','normal_flow','opening','inventory','transferred']:
   self.write(group);self.assertTrue(self.path.exists())
 def test_export_read_only(self):
  before=(copy.deepcopy(self.s.snapshot()),copy.deepcopy(self.s.events_log),self.s._sample_count)
  self.write();self.assertEqual(before,(self.s.snapshot(),self.s.events_log,self.s._sample_count))
 def test_normal_vs_actual_volume_units(self):
  self.assertIn('0°C',field_unit('flow_Nm3_h'));self.assertIn('actual',field_unit('flow_at_TT_m3_h'))
if __name__=='__main__':unittest.main()
