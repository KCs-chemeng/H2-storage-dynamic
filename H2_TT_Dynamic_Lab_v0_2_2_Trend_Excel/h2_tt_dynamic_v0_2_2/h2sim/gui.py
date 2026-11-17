"""v0.2.2 PLC-style HMI with independent trends and native Excel export."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import time
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font
from .engine import Config, Simulator
from .physics import normal_flow
from .editors import ConfigEditor, SetpointEditor, RuleEditor, table
from .config import RuleSpec, Condition, Action
from .hmi import Mimic, Trend, P, blend
from .trend_popup import TrendPopup
from .trend_data import capture_trend_snapshot, signals_for, default_signal_keys
from .xlsx_export import write_trend_xlsx

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
