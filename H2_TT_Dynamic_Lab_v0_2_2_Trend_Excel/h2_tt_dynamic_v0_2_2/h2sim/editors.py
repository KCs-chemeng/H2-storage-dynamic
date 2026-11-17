"""Tk table editors for model structure, live SPs and data-only condition rules."""
from __future__ import annotations
import copy
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, messagebox
from .config import Config, VesselSpec, ValveSpec, RuleSpec, Condition, Action


def table(parent, columns, height=6):
    frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,pady=5)
    tree=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',height=height)
    for name,label,width in columns:
        tree.heading(name,text=label);tree.column(name,width=width,anchor='center',minwidth=55)
    sy=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
    sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
    tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
    frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
    return tree


def fields(parent, definitions):
    """definitions=(key,label,value,choices_or_None)"""
    result={}
    for row,(key,label,value,choices) in enumerate(definitions):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=8,pady=5)
        v=tk.StringVar(value='' if value is None else str(value));result[key]=v
        widget=ttk.Combobox(parent,textvariable=v,values=choices,state='readonly',width=25) if choices else ttk.Entry(parent,textvariable=v,width=28)
        widget.grid(row=row,column=1,sticky='ew',padx=8,pady=5)
    return result


class ItemEditor(tk.Toplevel):
    def __init__(self,parent,title,definitions,callback):
        super().__init__(parent);self.title(title);self.transient(parent)
        f=ttk.Frame(self,padding=15);f.pack(fill='both',expand=True)
        self.vars=fields(f,definitions)
        def save():
            try:callback({k:v.get() for k,v in self.vars.items()});self.destroy()
            except (ValueError,TypeError,KeyError) as exc:messagebox.showerror('입력 확인',str(exc),parent=self)
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Button(row,text='적용',command=save).pack(side='right');ttk.Button(row,text='취소',command=self.destroy).pack(side='right',padx=6)
        self.grab_set()


class SetpointEditor(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.title('운전 SP 변경 — 재고·시간·이력 유지');self.geometry('830x650');self.transient(app)
        self.app=app;self.vars={}
        ttk.Label(self,text='운전 중 변경 가능 | 용적·초기압력·Cv는 설비 구성에서 수정',font=(app.face,12,'bold')).pack(anchor='w',padx=15,pady=12)
        nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=12)
        f=ttk.Frame(nb,padding=10);nb.add(f,text='공통 운전값')
        definitions=[]
        for key,label in [('tt_stop_barg','TT 종료압력 [barg]'),('bank_hh_barg','기본 Bank HH [barg]'),
                          ('equalize_dp_bar','균압 종료차압 [bar]'),('equalize_hold_s','균압 확인시간 [s]'),
                          ('suction_set_barg','이상 Regulator 흡입 SP [barg]'),('suction_min_barg','최소흡입압력 [barg]'),
                          ('actuator_timeout_s','밸브 동작 Timeout [s]'),('no_flow_delay_s','압축기 무유량 지연 [s]')]:
            definitions.append((key,label,getattr(app.sim.cfg,key),None))
        definitions.append(('manual_target_policy','수동운전 목표압력 정책',app.sim.cfg.manual_target_policy,['AUTO_ONLY','ALL_MODES']))
        self.vars.update(fields(f,definitions))
        b=ttk.Frame(nb,padding=8);nb.add(b,text='Bank 목표 / HH')
        canvas=tk.Canvas(b,highlightthickness=0);scroll=ttk.Scrollbar(b,command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        inner=ttk.Frame(canvas);canvas.create_window(0,0,window=inner,anchor='nw')
        inner.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        for j,text in enumerate(['Bank','현재압력 barg','목표압력 barg','HH barg (개별값)']):ttk.Label(inner,text=text).grid(row=0,column=j,padx=10,pady=8)
        for i,(name,vessel) in enumerate(app.sim.banks.items(),1):
            ttk.Label(inner,text=name).grid(row=i,column=0,padx=10)
            ttk.Label(inner,text=f'{vessel.pg:.3f}').grid(row=i,column=1,padx=10)
            for j,attr,value in [(2,'target_barg',vessel.spec.target_barg),(3,'hh_barg',app.sim.cfg.hh(vessel.spec))]:
                v=tk.StringVar(value=str(value));self.vars[f'bank.{name}.{attr}']=v
                ttk.Entry(inner,textvariable=v,width=17).grid(row=i,column=j,padx=8,pady=6)
        ttk.Label(self,text='목표압력보다 현재압력이 높으면 해당 운전정책에 따라 종료 절차가 시작됩니다.\n보호 HH는 제조사 설정값이 아닌 모의실험 입력입니다.',foreground='#617789').pack(anchor='w',padx=15,pady=10)
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Button(row,text='상태 유지하고 적용',command=self.apply).pack(side='right');ttk.Button(row,text='닫기',command=self.destroy).pack(side='right',padx=6)

    def apply(self):
        try:
            changes={k:(v.get() if k=='manual_target_policy' else float(v.get())) for k,v in self.vars.items()}
            self.app.sim.apply_setpoints(changes);self.app.refresh();self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('적용 실패 — 기존 상태 유지',str(exc),parent=self)


class ConfigEditor(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.cfg=copy.deepcopy(app.sim.cfg)
        self.title('설비 구성 / 계산조건 — 적용 시 새 실험');self.geometry('1050x750');self.transient(app)
        self.vars={};nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=10,pady=10)
        bank=ttk.Frame(nb,padding=10);nb.add(bank,text='TT / Bank 구성')
        self.ttvars=fields(bank,[('volume_m3','TT 내용적 [m³]',self.cfg.tt.volume_m3,None),('pressure_barg','TT 초기압력 [barg]',self.cfg.tt.pressure_barg,None),('temperature_c','TT 등온온도 [°C]',self.cfg.tt.temperature_c,None)])
        # fields use grid, so put them in a separate nested frame.
        bar=ttk.Frame(bank);bar.grid(row=3,column=0,columnspan=2,sticky='ew',pady=8)
        for text,fn in [('Bank 추가',lambda:self.bank_edit()),('선택 Bank 수정',lambda:self.bank_edit(True)),('선택 Bank 삭제',self.bank_delete)]:
            ttk.Button(bar,text=text,command=fn).pack(side='left',padx=4)
        area=ttk.Frame(bank);area.grid(row=4,column=0,columnspan=2,sticky='nsew');bank.rowconfigure(4,weight=1);bank.columnconfigure(1,weight=1)
        self.banktree=table(area,[('name','이름',90),('v','내용적 m³',110),('p','초기 barg',110),('t','온도 °C',100),('target','목표 barg',110),('hh','HH barg',100)])
        self.banktree.bind('<Double-1>',lambda e:self.bank_edit(True))
        valves=ttk.Frame(nb,padding=10);nb.add(valves,text='밸브 Cv / 응답')
        ttk.Button(valves,text='선택 밸브 수정',command=self.valve_edit).pack(anchor='w')
        self.valvetree=table(valves,[('tag','Tag',150),('cv','정격 Cv',95),('open','개방 전행정 s',120),('close','폐쇄 전행정 s',120),('char','특성',160),('R','Rangeability',100)],10)
        self.valvetree.bind('<Double-1>',lambda e:self.valve_edit())
        ttk.Label(valves,text='Close가 공란이면 개방시간과 동일. Equal-%는 0%에서 Cv=0으로 정규화한 등가특성.\nPRV 개도는 이상 Regulator의 가용 Cv 상한이며 PID 출력이 아닙니다.',foreground='#617789').pack(anchor='w',pady=10)
        for tabname,defs in [
            ('압축기 / 물성',[
                ('eos','물성모델',self.cfg.eos,['Leachman','PR','Ideal','CoolProp']),
                ('compressor_enabled','압축기 사용',str(self.cfg.compressor_enabled),['True','False']),
                ('compressor_kg_h','정격 처리유량 kg/h',self.cfg.compressor_kg_h,None),
                ('compressor_max_barg','토출 상한 barg',self.cfg.compressor_max_barg,None),
                ('compressor_ramp_s','기동 ramp s',self.cfg.compressor_ramp_s,None),
                ('compressor_stop_s','정지 ramp s',self.cfg.compressor_stop_s,None),
                ('compressor_reference_kw','참조 전력 kW',self.cfg.compressor_reference_kw,None),
                ('compressor_temperature_c','흡입 온도 °C',self.cfg.compressor_temperature_c,None),
                ('aftercooler_temperature_c','냉각 후 온도 °C',self.cfg.aftercooler_temperature_c,None),
                ('k','비열비 k',self.cfg.k,None),('xt','공통 xT',self.cfg.xt,None),('fp','공통 Fp',self.cfg.fp,None)]),
            ('시퀀스 / 차단',[
                ('sequence_mode','자동 시퀀스',self.cfg.sequence_mode,['EQ_RECOVERY','EQUALIZE_ONLY','COMPRESS_ONLY']),
                ('equalize_order','균압순서 (쉼표 구분)',','.join(self.cfg.equalize_order),None),
                ('recovery_order','압축순서 (쉼표 구분)',','.join(self.cfg.recovery_order),None),
                ('shutdown_mode','종료 응답모드',self.cfg.shutdown_mode,['DELAYED','IDEAL']),
                ('dt_s','적분간격 s',self.cfg.dt_s,None),('sample_s','기록간격 s',self.cfg.sample_s,None),
                ('tt_stop_barg','TT 종료압력 barg',self.cfg.tt_stop_barg,None),
                ('bank_hh_barg','기본 Bank HH barg',self.cfg.bank_hh_barg,None),
                ('equalize_dp_bar','균압 종료차압 bar',self.cfg.equalize_dp_bar,None),
                ('equalize_hold_s','균압 확인시간 s',self.cfg.equalize_hold_s,None),
                ('suction_set_barg','흡입 SP barg',self.cfg.suction_set_barg,None),
                ('suction_min_barg','최소흡입 barg',self.cfg.suction_min_barg,None)])]:
            f=ttk.Frame(nb,padding=10);nb.add(f,text=tabname);self.vars.update(fields(f,defs))
        row=ttk.Frame(self,padding=10);row.pack(fill='x')
        ttk.Label(row,text='1 TT + 1~32 Bank / 고정 2경로 템플릿. 구성 변경은 초기화, 운전 SP 변경은 별도 메뉴.').pack(side='left')
        ttk.Button(row,text='새 실험으로 적용',command=self.apply).pack(side='right')
        self.refresh_tables()

    def refresh_tables(self):
        for tree in (self.banktree,self.valvetree):tree.delete(*tree.get_children())
        for b in self.cfg.banks:self.banktree.insert('','end',iid=b.name,values=(b.name,b.volume_m3,b.pressure_barg,b.temperature_c,b.target_barg,self.cfg.hh(b)))
        for tag,v in self.cfg.valves.items():self.valvetree.insert('','end',iid=tag,values=(tag,v.cv,v.stroke_s,v.close_s if v.close_s is not None else '동일',v.characteristic,v.rangeability))

    def bank_edit(self,edit=False):
        if edit and not self.banktree.selection():return
        old=next((b for b in self.cfg.banks if edit and b.name==self.banktree.selection()[0]),None)
        b=copy.deepcopy(old or VesselSpec(f'B{len(self.cfg.banks)+1}',5.0,20.0))
        defs=[('name','Bank 이름 (영문 시작)',b.name,None),('volume_m3','전체 내용적 m³',b.volume_m3,None),('pressure_barg','초기압력 barg',b.pressure_barg,None),('temperature_c','등온온도 °C',b.temperature_c,None),('target_barg','충전 목표압력 barg',b.target_barg,None),('hh_barg','HH barg',self.cfg.hh(b),None)]
        def save(d):
            if old and d['name']!=old.name:raise ValueError('기존 Bank 이름은 유지하세요. 이름 변경은 삭제 후 추가하세요.')
            if not old and any(x.name==d['name'] for x in self.cfg.banks):raise ValueError('Bank 이름 중복')
            new=VesselSpec(d['name'],*[float(d[k]) for k in ('volume_m3','pressure_barg','temperature_c','target_barg','hh_barg')])
            test=copy.deepcopy(self.cfg)
            if old:test.banks=[new if x.name==old.name else x for x in test.banks]
            else:
                test.banks.append(new);test.equalize_order.append(new.name);test.recovery_order.append(new.name)
            test.normalize().validate();self.cfg=test
            self.vars['equalize_order'].set(','.join(test.equalize_order));self.vars['recovery_order'].set(','.join(test.recovery_order))
            self.refresh_tables()
        ItemEditor(self,'Bank 사양',defs,save)

    def bank_delete(self):
        if not self.banktree.selection():return
        name=self.banktree.selection()[0]
        if len(self.cfg.banks)<=1:return messagebox.showerror('Bank','최소 1개 Bank 필요',parent=self)
        if not messagebox.askyesno('Bank 삭제',name+' 및 해당 충전밸브를 삭제합니까?',parent=self):return
        test=copy.deepcopy(self.cfg);test.banks=[b for b in test.banks if b.name!=name]
        test.valves.pop('BANK-'+name);test.equalize_order=[n for n in test.equalize_order if n!=name];test.recovery_order=[n for n in test.recovery_order if n!=name]
        if not test.recovery_order:test.recovery_order=[test.banks[0].name]
        try:test.validate()
        except ValueError as exc:return messagebox.showerror('참조자료 확인',str(exc)+'\n관련 조건규칙/예약이벤트부터 정리하세요.',parent=self)
        self.cfg=test;self.vars['equalize_order'].set(','.join(test.equalize_order));self.vars['recovery_order'].set(','.join(test.recovery_order));self.refresh_tables()

    def valve_edit(self):
        if not self.valvetree.selection():return
        tag=self.valvetree.selection()[0];v=self.cfg.valves[tag]
        defs=[('cv','정격 Cv',v.cv,None),('stroke_s','개방 전행정 s',v.stroke_s,None),('close_s','폐쇄 전행정 s (빈칸=동일)',v.close_s,None),('characteristic','특성',v.characteristic,['Linear','Equal percentage']),('rangeability','Rangeability',v.rangeability,None)]
        def save(d):
            nv=ValveSpec(float(d['cv']),float(d['stroke_s']),d['characteristic'],float(d['rangeability']),float(d['close_s']) if d['close_s'].strip() else None)
            test=copy.deepcopy(self.cfg);test.valves[tag]=nv;test.validate();self.cfg=test;self.refresh_tables()
        ItemEditor(self,tag,defs,save)

    def apply(self):
        try:
            for k,v in self.ttvars.items():setattr(self.cfg.tt,k,float(v.get()))
            for key,v in self.vars.items():
                value=v.get()
                if key in ('equalize_order','recovery_order'):value=[x.strip() for x in value.split(',') if x.strip()]
                elif key=='compressor_enabled':value=value=='True'
                elif key not in ('eos','sequence_mode','shutdown_mode'):value=float(value)
                setattr(self.cfg,key,value)
            self.cfg.validate()
            if self.app.sim.time_s>0 and not messagebox.askyesno('새 실험','현재 재고·시간·이력을 초기화합니다. 결과 저장을 완료했습니까?',parent=self):return
            self.app.install_config(self.cfg);self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('입력 확인',str(exc),parent=self)


class RuleEditor(tk.Toplevel):
    def __init__(self,app,rule=None):
        super().__init__(app);self.app=app;self.original=copy.deepcopy(rule)
        self.rule=copy.deepcopy(rule or RuleSpec('Rule_'+str(len(app.sim.cfg.rules)+1),
            [Condition('selected.pressure','>=',190)],[Action('stop')]))
        self.title('조건–동작 로직 편집');self.geometry('1130x790');self.transient(app)
        top=ttk.Frame(self,padding=10);top.pack(fill='x')
        self.vars={}
        defs=[('name','규칙 이름',self.rule.name,None),('mode','운전모드',self.rule.mode,['ALL','AUTO','MANUAL']),
              ('phase','적용 단계',self.rule.phase,['ANY','EQ','RECOVERY']),('priority','우선순위',self.rule.priority,['NORMAL','INTERLOCK','PROTECTION']),
              ('behavior','유지방식',self.rule.behavior,['WHILE','LATCH','ONCE']),('hold_s','발동 유지시간 s',self.rule.hold_s,None),
              ('reset_hold_s','복귀 유지시간 s',self.rule.reset_hold_s,None),('enabled','사용',str(self.rule.enabled),['True','False'])]
        for i,(k,label,value,choices) in enumerate(defs):
            row,col=divmod(i,4);f=ttk.Frame(top);f.grid(row=row,column=col,sticky='ew',padx=6,pady=4)
            ttk.Label(f,text=label).pack(anchor='w');v=tk.StringVar(value=str(value));self.vars[k]=v
            wid=ttk.Combobox(f,textvariable=v,values=choices,width=20,state='readonly') if choices else ttk.Entry(f,textvariable=v,width=22)
            wid.pack(fill='x')
        nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=12)
        self.condlists={'conditions':copy.deepcopy(self.rule.conditions),'reset_conditions':copy.deepcopy(self.rule.reset_conditions)}
        self.trees={};self.combines={}
        for key,title in [('conditions','발동조건'),('reset_conditions','복귀조건 / 히스테리시스')]:
            f=ttk.Frame(nb,padding=10);nb.add(f,text=title)
            row=ttk.Frame(f);row.pack(fill='x')
            cv=tk.StringVar(value=self.rule.combine if key=='conditions' else self.rule.reset_combine);self.combines[key]=cv
            ttk.Label(row,text='조건 결합').pack(side='left');ttk.Combobox(row,textvariable=cv,values=['ALL','ANY'],state='readonly',width=8).pack(side='left',padx=6)
            ttk.Label(row,text='ALL = AND / ANY = OR · 숫자압력은 barg, 차압은 bar, 개도는 %').pack(side='left',padx=10)
            self.trees[key]=table(f,[('var','감시변수',370),('op','비교',100),('value','설정값',170)],8)
            control=ttk.Frame(f);control.pack(fill='x',pady=5)
            for text,fn in [('조건 추가',lambda k=key:self.condition_edit(k)),('선택 수정',lambda k=key:self.condition_edit(k,True)),('선택 삭제',lambda k=key:self.condition_delete(k))]:ttk.Button(control,text=text,command=fn).pack(side='left',padx=4)
            self.trees[key].bind('<Double-1>',lambda e,k=key:self.condition_edit(k,True))
            ttk.Label(f,text='복귀조건이 있으면 발동 후 그 조건이 성립할 때까지 출력 유지.\n예: 120 barg 이상 CLOSE, 115 barg 이하 복귀. LATCH는 복귀조건 없이 수동 Reset도 가능.',foreground='#617789').pack(anchor='w',pady=10)
        a=ttk.Frame(nb,padding=10);nb.add(a,text='실행 동작')
        self.actions=copy.deepcopy(self.rule.actions)
        self.actiontree=table(a,[('action','동작',180),('target','대상',240),('value','값',190)],8)
        bar=ttk.Frame(a);bar.pack(fill='x')
        for text,fn in [('동작 추가',lambda:self.action_edit()),('선택 수정',lambda:self.action_edit(True)),('선택 삭제',self.action_delete)]:ttk.Button(bar,text=text,command=fn).pack(side='left',padx=4)
        ttk.Label(a,text='valve: 개도 0~100 / compressor: true 또는 false / bank: Bank 이름 / route: 경로\nnext: AUTO 다음 단계 / stop: 전체 정상정지 / trip: 모의 보호정지 / release: 수동·예약 출력 해제\n보호·인터록 우선순위는 CLOSE/STOP만 허용. 경로·Bank·NEXT 동작은 발동 시 1회 실행.',foreground='#617789').pack(anchor='w',pady=12)
        bot=ttk.Frame(self,padding=12);bot.pack(fill='x')
        ttk.Label(bot,text='WHILE: 조건 유지 · LATCH: 복귀/Reset까지 · ONCE: 1회 발동 후 Reset 전 재발동 금지',foreground='#617789').pack(side='left')
        ttk.Button(bot,text='상태 유지하고 적용',command=self.save).pack(side='right')
        self.refresh()

    def refresh(self):
        for key,tree in self.trees.items():
            tree.delete(*tree.get_children())
            for i,c in enumerate(self.condlists[key]):tree.insert('','end',iid=str(i),values=(c.variable,c.op,c.value))
        self.actiontree.delete(*self.actiontree.get_children())
        for i,a in enumerate(self.actions):self.actiontree.insert('','end',iid=str(i),values=(a.action,a.target,a.value))

    def condition_edit(self,key,edit=False):
        selected=self.trees[key].selection()
        if edit and not selected:return
        index=int(selected[0]) if edit else None
        c=self.condlists[key][index] if edit else Condition('selected.pressure','>=',190)
        def save(d):
            value=d['value'] if d['variable'] in ('mode','phase','route') else float(d['value'])
            nc=Condition(d['variable'],d['op'],value)
            if edit:self.condlists[key][index]=nc
            else:self.condlists[key].append(nc)
            self.refresh()
        ItemEditor(self,'조건 입력',[('variable','감시변수',c.variable,self.app.sim.cfg.variable_names()),('op','연산자',c.op,['>=','<=','>','<','==','!=']),('value','설정값',c.value,None)],save)

    def condition_delete(self,key):
        if self.trees[key].selection():del self.condlists[key][int(self.trees[key].selection()[0])];self.refresh()

    def action_edit(self,edit=False):
        selected=self.actiontree.selection()
        if edit and not selected:return
        index=int(selected[0]) if edit else None
        a=self.actions[index] if edit else Action('valve','BYPASS',0)
        def save(d):
            action=d['action'];v=d['value']
            if action=='valve':v=float(v)
            elif action=='compressor':
                if v.lower() not in ('true','false','1','0'):raise ValueError('true 또는 false 입력')
                v=v.lower() in ('true','1')
            na=Action(action,d['target'],v)
            if edit:self.actions[index]=na
            else:self.actions.append(na)
            self.refresh()
        ItemEditor(self,'동작 입력',[('action','동작 종류',a.action,['valve','compressor','bank','route','next','stop','trip','release']),('target','대상 (valve/release)',a.target,['',*self.app.sim.valves,'COMPRESSOR','*']),('value','값 (Bank/경로도 여기에 입력)',a.value,None)],save)

    def action_delete(self):
        if self.actiontree.selection():del self.actions[int(self.actiontree.selection()[0])];self.refresh()

    def save(self):
        try:
            d={k:v.get() for k,v in self.vars.items()};d['hold_s']=float(d['hold_s']);d['reset_hold_s']=float(d['reset_hold_s']);d['enabled']=d['enabled']=='True'
            d.update(conditions=self.condlists['conditions'],reset_conditions=self.condlists['reset_conditions'],actions=self.actions,combine=self.combines['conditions'].get(),reset_combine=self.combines['reset_conditions'].get())
            r=RuleSpec(**d)
            rules=[r if self.original and x.name==self.original.name else x for x in self.app.sim.cfg.rules]
            if not self.original:rules.append(r)
            self.app.sim.replace_rules(rules);self.app.refresh_rules();self.app.refresh();self.destroy()
        except (ValueError,TypeError) as exc:messagebox.showerror('규칙 확인',str(exc),parent=self)
