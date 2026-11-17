from pathlib import Path
import sys,copy,json,time,hashlib,traceback,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from h2sim.gui import App
from h2sim.config import Config
from h2sim.trend_data import capture_trend_snapshot,signals_for
from h2sim.xlsx_export import write_trend_xlsx
from h2sim.physics import HydrogenEOS,PATM_BAR
root=Path(__file__).resolve().parents[1];out=root/'validation';out.mkdir(exist_ok=True)
app=App(Config(eos='PR',sample_s=.5));app.geometry('1500x900+0+0');app.update()
checks=[];errors=[]
app.report_callback_exception=lambda typ,val,tb:errors.append(''.join(traceback.format_exception(typ,val,tb)))
def chk(name,test):
 assert test,name
 checks.append({'check':name,'result':'PASS'})
def ui():app.update();app.refresh(force=True);app.update()
def grab(win,name):
 win.update();time.sleep(.08)
 ImageGrab.grab(bbox=(win.winfo_rootx(),win.winfo_rooty(),win.winfo_rootx()+win.winfo_width(),win.winfo_rooty()+win.winfo_height())).save(out/name)
app.sim.start_auto()
for _ in range(360):app.sim.step()
ui();state=copy.deepcopy(app.sim.snapshot());sample_count=app.sim._sample_count
p=app.open_trend('pressure');p.geometry('1180x730+230+130');ui()
chk('Popup is non-modal',app.grab_current() is None)
chk('Opening popup preserves simulation state',app.sim.snapshot()==state and app.sim._sample_count==sample_count)
chk('All pressure records available',len(p._shown_rows)==app.sim._sample_count)
grab(p,'pressure_popup.png');grab(app,'hmi_popup_overlay.png')
p.live.set(False);p.toggle_live();frozen=copy.deepcopy(p._shown_rows)
for _ in range(30):app.sim.step()
p.update_view();ui();chk('Chart freeze does not stop engine or advance chart',p._shown_rows==frozen and app.sim.time_s>state['time_s'])
p.live.set(True);p.toggle_live();chk('Live view resumes at current time',p._shown_rows[-1]['time_s']==app.sim.time_s)
p.start_var.set('1');p.end_var.set('2');p.apply_range();chk('Custom time range uses minute inputs',p._shown_rows[0]['time_s']==60 and p._shown_rows[-1]['time_s']==120)
p.window_var.set('전체');p.set_group('opening');ui()
chk('Commands and actual openings both selectable',any(s.key=='BYPASS_command_pct' for s in p.signals) and any(s.key=='BYPASS_actual_pct' for s in p.signals))
# Produce a manual event so the chart demonstrates position changes.
app.sim.set_valve('BYPASS',0)
for _ in range(40):app.sim.step()
app.sim.set_valve('BYPASS',50)
for _ in range(40):app.sim.step()
app.sim.set_valve('BYPASS',100)
for _ in range(40):app.sim.step()
ui();p.listbox.selection_clear(0,'end')
for i,s in enumerate(p.signals):
 if s.key in ('BYPASS_actual_pct','BYPASS_command_pct'):p.listbox.selection_set(i)
p.update_view(True);grab(p,'valve_trend_popup.png')
p2=app.open_trend('flow');ui();chk('Multiple independent popups',p.winfo_exists() and p2.winfo_exists())
p2.geometry('1000x650+350+180');ui();grab(p2,'flow_popup.png')
# Direct snapshot export exercises the same serializer as the GUI save action.
before=copy.deepcopy(app.sim.snapshot());snap=capture_trend_snapshot(app.sim)
selected=[s for s in signals_for(app.sim,'pressure') if s.key in ('tt_barg','bank_A_barg','bank_B_barg','bank_C_barg')]
meta=write_trend_xlsx(out/'H2_TT_Trend_Export_Demo.xlsx',snap,'pressure',selected)
chk('Excel export leaves engine unchanged',before==app.sim.snapshot())
chk('Excel raw data includes every record',meta['rows']==len(snap.rows))
chk('Excel EOS metadata is PR',meta['eos']=='PR')
# All six groups render without state changes.
for group in ('pressure','flow','normal_flow','opening','inventory','transferred'):
 p.set_group(group);ui();chk('Rendering group '+group+' is read-only',app.sim.snapshot()==before)
p2.destroy();p.geometry('900x580+250+180');p.set_group('pressure');ui();grab(p,'popup_compact.png')
chk('Compact popup has visible plot',p.canvas.winfo_width()>400 and p.canvas.winfo_height()>150)
# Entire history is read from disk, not the display buffer.
app.sim.history.clear();p._cache_count=-1;p.update_view(True)
chk('Popup survives emptied display buffer',len(p._shown_rows)>300)
# Reset changes the popup source rather than mixing experiments/EOS.
app.install_config(Config(eos='Ideal'));ui()
chk('Popup follows new experiment without old records',p._shown_rows[-1]['time_s']==0 and p.sim_identity==id(app.sim))
chk('No modal grab after reset',app.grab_current() is None)
p.destroy();ui();chk('Popup close does not stop parent',app.winfo_exists())
app.on_close();chk('No Tk callback exceptions',not errors)
(out/'UI_TESTS_v022.json').write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2))
# Verify physical/control sources byte-for-byte against v0.2.1 ZIP.
files=['physics.py','config.py','controls.py','engine.py','editors.py','hmi.py']
hashes=[]
with zipfile.ZipFile('/mnt/data/H2_TT_Dynamic_Lab_v0_2_1_PLC_UI.zip') as z:
 for fn in files:
  old=z.read('h2_tt_dynamic_v0_2_1/h2sim/'+fn);new=(root/'h2sim'/fn).read_bytes()
  assert old==new,fn
  hashes.append({'file':'h2sim/'+fn,'sha256':hashlib.sha256(new).hexdigest(),'unchanged':True})
(out/'ENGINE_UNCHANGED_v022.json').write_text(json.dumps(hashes,indent=2))
comparison=[]
for pg in [10,20,190,200]:
 lh=HydrogenEOS('Leachman').density(pg+PATM_BAR,318.15);pr=HydrogenEOS('PR').density(pg+PATM_BAR,318.15)
 comparison.append({'pressure_barg':pg,'temperature_C':45,'Leachman_rho_kg_m3':lh,'PR_rho_kg_m3':pr,'PR_vs_Leachman_percent':100*(pr/lh-1)})
(out/'EOS_comparison_45C.json').write_text(json.dumps(comparison,indent=2))
print(json.dumps({'ui_checks':len(checks),'errors':errors,'xlsx':meta,'engine_files_unchanged':len(hashes)},ensure_ascii=False,indent=2))
