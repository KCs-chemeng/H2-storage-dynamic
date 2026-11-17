from pathlib import Path
import sys,time,copy,json,tempfile,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import h2sim.gui as gui
from h2sim.config import Config
root=Path(__file__).resolve().parents[1]
app=gui.App(Config(eos='PR',sample_s=.5));app.update()
app.sim.start_auto()
for _ in range(20):app.sim.step()
app.refresh(force=True);app.update();state=copy.deepcopy(app.sim.snapshot())
notifications=[];failures=[];checks=[]
gui.messagebox.showinfo=lambda *a,**k:notifications.append(a)
gui.messagebox.showerror=lambda *a,**k:failures.append(a)
def wait():
 end=time.time()+12
 while time.time()<end and (any(t.is_alive() for t in app._export_jobs) or not notifications):app.update();time.sleep(.02)
 app.update()
def check(name,condition):
 assert condition,name
 checks.append({'check':name,'result':'PASS'})
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'toolbar.xlsx';gui.filedialog.asksaveasfilename=lambda **k:str(p)
 app.export_excel();wait()
 check('Main EXPORT Excel action writes native workbook',p.exists() and not failures)
 check('Main EXPORT preserves state',state==app.sim.snapshot())
 notifications.clear();win=app.open_trend('opening');app.update()
 win.start_var.set(str(2/60));win.end_var.set(str(4/60));win.apply_range()
 p=Path(td)/'popup.xlsx';gui.filedialog.asksaveasfilename=lambda **k:str(p)
 win.export_xlsx(False);wait()
 check('Popup selected-range Excel action writes workbook',p.exists() and not failures)
 check('Popup export preserves state',state==app.sim.snapshot())
 win.destroy()
app.on_close()
(root/'validation/EXCEL_UI_ACTION_TESTS.json').write_text(json.dumps({'checks':checks,'errors':failures},ensure_ascii=False,indent=2))
print('4 Excel GUI action checks passed')
