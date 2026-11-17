"""Non-modal process trends; viewing and Excel export are read-only operations."""
from __future__ import annotations
import bisect
import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from .trend_data import (TREND_GROUPS, signals_for, default_signal_keys,
    capture_trend_snapshot, read_journal, select_rows, thin_for_plot)
from .hmi import P


class TrendPopup(tk.Toplevel):
    def __init__(self,app,group='pressure'):
        super().__init__(app)
        self.app=app;self.sim_identity=id(app.sim);self.group=group
        self.title('TREND MONITOR · 독립 추세창')
        self.geometry('1180x730');self.minsize(880,570);self.configure(bg=P['bg'])
        # Deliberately non-modal: no grab_set(), no pause(), no transient-only lock.
        self.live=tk.BooleanVar(value=True);self.events_var=tk.BooleanVar(value=True)
        self.group_var=tk.StringVar(value=TREND_GROUPS[group][0])
        self.window_var=tk.StringVar(value='전체')
        self.start_var=tk.StringVar(value='0');self.end_var=tk.StringVar(value='10')
        self._cache_count=-1;self._base_rows=[];self._frozen=None
        self._shown_rows=[];self._domain=None;self._last_signature=None
        self._build();self.bind('<Escape>',lambda e:self.destroy())
        self.bind('<Control-e>',lambda e:self.export_xlsx(False))
        self.bind('<F5>',lambda e:self.refresh_snapshot())
        self.set_group(group);self.update_view(force=True)

    def _build(self):
        head=tk.Frame(self,bg=P['navy']);head.pack(fill='x')
        tk.Label(head,text='TREND MONITOR',font=(self.app.face,-20,'bold'),fg='white',bg=P['navy']).pack(side='left',padx=18,pady=12)
        self.state_label=tk.Label(head,text='',font=(self.app.mono,-11),fg='#BFD4E1',bg=P['navy'])
        self.state_label.pack(side='right',padx=18)
        row=ttk.Frame(self,padding=(12,9));row.pack(fill='x')
        ttk.Label(row,text='항목').pack(side='left',padx=4)
        c=ttk.Combobox(row,textvariable=self.group_var,values=[x[0] for x in TREND_GROUPS.values()],width=14,state='readonly');c.pack(side='left',padx=4)
        c.bind('<<ComboboxSelected>>',lambda e:self.set_group(next(k for k,v in TREND_GROUPS.items() if v[0]==self.group_var.get())))
        ttk.Checkbutton(row,text='LIVE',variable=self.live,command=self.toggle_live).pack(side='left',padx=12)
        ttk.Button(row,text='새 Snapshot',command=self.refresh_snapshot).pack(side='left',padx=4)
        ttk.Checkbutton(row,text='이벤트 표시',variable=self.events_var,command=lambda:self.update_view(True)).pack(side='left',padx=9)
        ttk.Button(row,text='전체 Excel',command=lambda:self.export_xlsx(True)).pack(side='right',padx=4)
        ttk.Button(row,text='표시구간 Excel',style='Primary.TButton',command=lambda:self.export_xlsx(False)).pack(side='right',padx=4)
        row=ttk.Frame(self,padding=(12,0,12,9));row.pack(fill='x')
        ttk.Label(row,text='시간범위').pack(side='left',padx=4)
        self.window_combo=ttk.Combobox(row,textvariable=self.window_var,values=['전체','최근 10분','최근 30분','최근 60분','직접 지정'],width=12,state='readonly')
        self.window_combo.pack(side='left',padx=4);self.window_combo.bind('<<ComboboxSelected>>',lambda e:self.update_view(True))
        ttk.Label(row,text='시작 / 끝 [min]').pack(side='left',padx=(16,4))
        ttk.Entry(row,textvariable=self.start_var,width=8).pack(side='left',padx=3)
        ttk.Entry(row,textvariable=self.end_var,width=8).pack(side='left',padx=3)
        ttk.Button(row,text='구간 적용',command=self.apply_range).pack(side='left',padx=5)
        self.stats=ttk.Label(row,text='',foreground=P['muted']);self.stats.pack(side='right',padx=4)
        body=ttk.Frame(self);body.pack(fill='both',expand=True,padx=12,pady=(0,8))
        left=ttk.Frame(body,width=255);left.pack(side='left',fill='y',padx=(0,9));left.pack_propagate(False)
        ttk.Label(left,text='SIGNALS / 다중 선택',font=(self.app.face,-12,'bold')).pack(anchor='w',padx=4,pady=5)
        area=ttk.Frame(left);area.pack(fill='both',expand=True)
        self.listbox=tk.Listbox(area,selectmode='extended',exportselection=False,
            font=(self.app.face,-12),bg='white',fg=P['ink'],selectbackground='#D6ECE8',selectforeground=P['ink'],borderwidth=0,highlightthickness=1,highlightbackground=P['border'])
        self.listbox.pack(side='left',fill='both',expand=True)
        sb=ttk.Scrollbar(area,orient='vertical',command=self.listbox.yview);sb.pack(side='right',fill='y');self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>',lambda e:self.update_view(True))
        br=ttk.Frame(left);br.pack(fill='x',pady=7)
        ttk.Button(br,text='모두 선택',command=self.select_all).pack(side='left',expand=True,fill='x',padx=2)
        ttk.Button(br,text='기본 선택',command=self.select_defaults).pack(side='left',expand=True,fill='x',padx=2)
        ttk.Label(left,text='Ctrl / Shift로 다중 선택\n명령·실개도 및 SP도 선택 가능\n차트 표시: 최초 12개 변수\nExcel 데이터: 선택 변수 모두',foreground=P['muted'],justify='left').pack(anchor='w',padx=5,pady=(0,4))
        right=ttk.Frame(body,style='Card.TFrame');right.pack(side='left',fill='both',expand=True)
        self.canvas=tk.Canvas(right,bg='white',highlightthickness=1,highlightbackground=P['border'])
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.update_view(True))
        self.canvas.bind('<Motion>',self._hover);self.canvas.bind('<Leave>',lambda e:self.canvas.delete('cursor'))
        self.readout=ttk.Label(right,text='마우스로 기록 시각과 값을 조회합니다.',foreground=P['muted'],padding=8,wraplength=800)
        self.readout.pack(fill='x')
        foot=ttk.Label(self,text='LIVE 해제는 화면만 고정합니다. 시뮬레이션 RUN/PAUSE와 독립적입니다.  |  ESC 닫기 · Ctrl+E 표시구간 Excel',foreground=P['muted'],padding=(15,6))
        foot.pack(fill='x')

    def set_group(self,group):
        self.group=group;self.group_var.set(TREND_GROUPS[group][0]);self.signals=signals_for(self.app.sim,group)
        self.listbox.delete(0,'end')
        for s in self.signals:self.listbox.insert('end',s.label+' ['+s.unit+']')
        self.select_defaults()

    def select_defaults(self):
        default=set(default_signal_keys(self.app.sim,self.group));self.listbox.selection_clear(0,'end')
        for i,s in enumerate(self.signals):
            if s.key in default:self.listbox.selection_set(i)
        self.update_view(True)

    def select_all(self):
        self.listbox.selection_set(0,'end');self.update_view(True)

    def selected_signals(self):return [self.signals[i] for i in self.listbox.curselection()]

    def _rows(self):
        if self._frozen is not None and not self.live.get():return self._frozen.rows
        sim=self.app.sim
        if self._cache_count!=sim._sample_count:
            self._base_rows=read_journal(sim);self._cache_count=sim._sample_count
        rows=list(self._base_rows);current=sim.snapshot()
        # The last cached unsampled state can be refreshed without recording it.
        if rows and rows[-1]['time_s']>sim._last_sample+1e-9:rows.pop()
        if not rows or rows[-1]!=current:rows.append(current)
        return rows

    def range_bounds(self,rows=None):
        rows=self._rows() if rows is None else rows
        maximum=rows[-1]['time_s'] if rows else 0.0
        choice=self.window_var.get()
        if choice=='전체':return None,None
        if choice=='직접 지정':
            a=float(self.start_var.get())*60;b=float(self.end_var.get())*60
            select_rows([],a,b);return a,b
        mins=int(choice.split()[1].replace('분',''))
        return max(0,maximum-mins*60),maximum

    def apply_range(self):
        self.window_var.set('직접 지정')
        try:self.range_bounds();self.update_view(True)
        except ValueError as exc:messagebox.showerror('시간범위',str(exc),parent=self)

    def toggle_live(self):
        if not self.live.get():self._frozen=capture_trend_snapshot(self.app.sim)
        else:self._frozen=None;self._cache_count=-1
        self.update_view(True)

    def refresh_snapshot(self):
        self._cache_count=-1
        if not self.live.get():self._frozen=capture_trend_snapshot(self.app.sim)
        self.update_view(True)

    def on_new_simulation(self):
        self.sim_identity=id(self.app.sim);self._frozen=None;self.live.set(True);self._cache_count=-1
        self.window_var.set('전체');self.set_group(self.group)

    def update_view(self,force=False):
        if not self.winfo_exists():return
        if id(self.app.sim)!=self.sim_identity:self.on_new_simulation();return
        rows=self._rows()
        try:lo,hi=self.range_bounds(rows);selected=select_rows(rows,lo,hi)
        except (ValueError,TypeError):return
        signals=self.selected_signals();drawsignals=signals[:12]
        events=self._frozen.events if self._frozen is not None and not self.live.get() else self.app.sim.events_log
        self._shown_rows=selected;self._shown_signals=drawsignals
        self._shown_events=events
        currenttime=rows[-1]['time_s'] if rows else 0
        self.state_label.configure(text=f"{'LIVE' if self.live.get() else 'FROZEN'}  |  EOS {self.app.sim.cfg.eos}  |  {currenttime:.1f} s")
        self.stats.configure(text=f'{len(selected):,} samples · {len(signals)} signals')
        self._draw()

    def _draw(self):
        c=self.canvas;c.delete('all');w=c.winfo_width();h=c.winfo_height()
        if w<150 or h<120:return
        rows=self._shown_rows;signals=self._shown_signals
        c.create_text(18,22,text=f'{TREND_GROUPS[self.group][0]}  /  {TREND_GROUPS[self.group][1]}',anchor='w',fill=P['ink'],font=(self.app.face,-17,'bold'))
        if not rows or not signals:
            c.create_text(w/2,h/2,text='표시할 변수와 기록구간을 선택하세요.',fill=P['muted'],font=(self.app.face,-14));self._domain=None;return
        palette=['#23699C','#B47722','#168773','#7B5DB7','#B1476E','#148C9C','#7C8638','#AA5C38','#485F95','#807375','#B14343','#447564']
        x=18;y=51
        for i,s in enumerate(signals):
            text=s.label
            needed=max(92,min(220,len(text)*8+35))
            if x+needed>w-20:x=18;y+=21
            c.create_line(x,y,x+16,y,fill=palette[i],width=2.4)
            c.create_text(x+22,y,text=text,anchor='w',fill=P['muted'],font=(self.app.face,-11));x+=needed
        left=70;right=w-25;top=y+25;bottom=h-45
        if bottom-top<35:return
        vals=[float(r.get(s.key,0)) for r in rows for s in signals]
        low=min(0,min(vals));high=max(1,max(vals))
        if self.group=='opening':low=0;high=105
        else:
            span=max(1e-6,high-low);low-=span*.035 if low<0 else 0;high+=span*.08
        t0=rows[0]['time_s'];t1=max(t0+.01,rows[-1]['time_s'])
        self._domain=(left,right,top,bottom,t0,t1,low,high)
        for i in range(6):
            yy=bottom-(bottom-top)*i/5;v=low+(high-low)*i/5
            c.create_line(left,yy,right,yy,fill='#E1E9EF')
            c.create_text(left-8,yy,text=f'{v:,.1f}' if abs(high)<1000 else f'{v:,.0f}',anchor='e',fill=P['muted'],font=(self.app.mono,-10))
            xx=left+(right-left)*i/5
            c.create_text(xx,bottom+17,text=f'{(t0+(t1-t0)*i/5)/60:.2f}',fill=P['muted'],font=(self.app.mono,-10))
        c.create_text(right,bottom+32,text='min',anchor='e',fill=P['muted'],font=(self.app.mono,-10))
        if self.events_var.get():
            marks=[e for e in self._shown_events if t0<=e['time_s']<=t1 and any(a in e['code'] for a in ('RULE','SETPOINT','STOP','SEQUENCE','VALVE','ESD'))]
            for e in marks[-35:]:
                xx=left+(right-left)*(e['time_s']-t0)/(t1-t0)
                c.create_line(xx,top,xx,bottom,fill='#D5B8AA',dash=(2,5))
        plotted=thin_for_plot(rows,[s.key for s in signals])
        for i,s in enumerate(signals):
            points=[]
            for r in plotted:
                points.extend((left+(right-left)*(r['time_s']-t0)/(t1-t0),bottom-(bottom-top)*(float(r.get(s.key,0))-low)/(high-low)))
            if len(points)>=4:c.create_line(*points,fill=palette[i],width=2.0)
            if points:c.create_oval(points[-2]-2,points[-1]-2,points[-2]+2,points[-1]+2,fill=palette[i],outline='')
        self.readout.configure(text=f"{len(rows):,}개 기록 / 화면 {len(plotted):,}개 점 · Excel은 화면 간소화 없이 저장 · {t0:.3f}–{rows[-1]['time_s']:.3f} s")

    def _hover(self,event):
        self.canvas.delete('cursor')
        if not self._domain or not self._shown_rows:return
        left,right,top,bottom,t0,t1,low,high=self._domain
        if not left<=event.x<=right or not top<=event.y<=bottom:return
        target=t0+(t1-t0)*(event.x-left)/(right-left);times=[r['time_s'] for r in self._shown_rows]
        j=bisect.bisect_left(times,target);candidates=[i for i in (j-1,j) if 0<=i<len(times)]
        row=self._shown_rows[min(candidates,key=lambda i:abs(times[i]-target))]
        x=left+(right-left)*(row['time_s']-t0)/(t1-t0)
        self.canvas.create_line(x,top,x,bottom,fill='#61798A',dash=(3,3),tags='cursor')
        text=f"t={row['time_s']:.3f} s | "+'  ·  '.join(f'{s.label}: {float(row.get(s.key,0)):.3f}' for s in self._shown_signals[:6])
        self.readout.configure(text=text)

    def export_xlsx(self,all_rows=False):
        try:
            signals=self.selected_signals()
            if not signals:raise ValueError('변수를 하나 이상 선택하세요.')
            snap=self._frozen if self._frozen is not None and not self.live.get() else capture_trend_snapshot(self.app.sim)
            lo,hi=(None,None) if all_rows else self.range_bounds(snap.rows)
            if not select_rows(snap.rows,lo,hi):raise ValueError('지정 구간에 저장된 표본이 없습니다.')
            path=filedialog.asksaveasfilename(parent=self,defaultextension='.xlsx',filetypes=[('Excel workbook','*.xlsx')],
                initialfile=f'H2_TT_{self.group}_{snap.current_config["eos"]}_{int(snap.snapshot_time_s)}s.xlsx')
            if path:self.app.start_excel_export(path,snap,self.group,signals,lo,hi,parent=self)
        except (ValueError,TypeError,OSError) as exc:messagebox.showerror('Excel 저장',str(exc),parent=self)
