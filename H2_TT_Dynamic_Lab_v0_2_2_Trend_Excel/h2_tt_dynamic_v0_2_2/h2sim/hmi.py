"""Read-only industrial HMI renderer. No model or control logic is implemented here.

Every indicated process value is read from Simulator/Config. The artwork is a
functional mimic, not additional physical equipment. Uses Tk only.
"""
from __future__ import annotations
import math
import time
import tkinter as tk

P = dict(bg='#E8EDF1', paper='#F8FAFC', panel='#F1F5F7', white='#FFFFFF',
         navy='#142B3D', ink='#203747', muted='#657B8C', border='#B7C7D2',
         steel='#91A5B4', pipe='#9AADB9', live='#168D78', green='#16815C',
         red='#B34848', amber='#BD7C24', blue='#2475B1', cyan='#31A9BD',
         display='#122D3B', digit='#96E6CA')


def blend(a, b, t):
    ar = [int(a[i:i+2], 16) for i in (1,3,5)]
    br = [int(b[i:i+2], 16) for i in (1,3,5)]
    return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x,y in zip(ar,br))


class Mimic(tk.Canvas):
    """Vector HMI: 1 TT / 1 compressor package / three banks per display page."""
    BASE_W=1540
    BASE_H=540

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, bg=P['paper'], highlightthickness=0, **kwargs)
        self.app=app
        self.page=0
        self.follow=True
        self._last_selected=None
        self._last_size=None
        self._hit_targets={}
        self.bind('<Configure>', lambda e:self.redraw())
        self.bind('<Leave>', lambda e:self.configure(cursor=''))
        self.bind('<Button-4>', lambda e:self.change_page(-1))
        self.bind('<Button-5>', lambda e:self.change_page(1))
        self.bind('<MouseWheel>',self._wheel)

    def _wheel(self,e):
        if len(self.app.sim.banks)>3:
            self.change_page(-1 if e.delta>0 else 1)

    def change_page(self, direction):
        count=max(1,math.ceil(len(self.app.sim.banks)/3))
        self.page=(self.page+direction)%count
        self.follow=False
        self.redraw()

    def follow_active(self):
        self.follow=True
        self.redraw()

    def coords_(self, values):
        return [self.ox+v*self.scale if i%2==0 else self.oy+v*self.scale
                for i,v in enumerate(values)]

    def rect(self,x,y,w,h,fill=None,outline=None,width=1,tag=None):
        return self.create_rectangle(*self.coords_((x,y,x+w,y+h)),fill=fill or '',
                outline=outline or '',width=width,tags=tag or ())

    def line(self,pts,color=None,width=1,dash=None,arrow=None,tag=None):
        return self.create_line(*self.coords_([v for xy in pts for v in xy]),
              fill=color or P['ink'],width=width*self.scale,dash=dash,
              arrow=arrow or 'none',arrowshape=(9*self.scale,11*self.scale,4*self.scale),
              capstyle='round',joinstyle='round',tags=tag or ())

    def text(self,x,y,value,size=10,color=None,bold=False,anchor='center',mono=False,width=None,tag=None):
        # Negative sizes are pixel fonts: stable across Windows display scaling.
        face=self.app.mono if mono else self.app.face
        return self.create_text(*self.coords_((x,y)),text=value,fill=color or P['ink'],
            anchor=anchor,font=(face,-max(9,round(size*self.scale)),'bold' if bold else 'normal'),
            width=0 if width is None else width*self.scale,tags=tag or ())

    def oval(self,x,y,w,h,fill=None,outline=None,width=1,tag=None):
        return self.create_oval(*self.coords_((x,y,x+w,y+h)),fill=fill or '',
            outline=outline or '',width=width*self.scale,tags=tag or ())

    def polygon(self,pts,fill,outline=None,width=1,tag=None):
        return self.create_polygon(*self.coords_([v for xy in pts for v in xy]),fill=fill,
                outline=outline or '',width=width*self.scale,tags=tag or ())

    def rounded(self,x,y,w,h,r=9,fill=None,outline=None,width=1,tag=None):
        pts=[(x+r,y),(x+w-r,y),(x+w,y),(x+w,y+r),(x+w,y+h-r),(x+w,y+h),
             (x+w-r,y+h),(x+r,y+h),(x,y+h),(x,y+h-r),(x,y+r),(x,y)]
        return self.create_polygon(*self.coords_([v for xy in pts for v in xy]),
            smooth=True,splinesteps=16,fill=fill or '',outline=outline or '',
            width=width*self.scale,tags=tag or ())

    def hit(self,tag,x,y,w,h,callback,hint):
        self.rect(x,y,w,h,tag=tag)
        self.tag_bind(tag,'<Button-1>',lambda e:callback())
        self.tag_bind(tag,'<Enter>',lambda e:self._hover(hint))
        self.tag_bind(tag,'<Leave>',lambda e:self._hover(''))
        self._hit_targets[tag]=(x+w/2,y+h/2)

    def _hover(self, text):
        self.configure(cursor='hand2' if text else '')
        self.app.hover_var.set(text or '밸브·용기·압축기 심벌 클릭: 운전 상태 및 조작 Faceplate')

    def pipe(self,pts,active=False):
        # Neutral pipe has visible edging; flowing pipe has a teal core.
        self.line(pts,P['steel'],7)
        self.line(pts,'#D5DFE5',4.6)
        if active:
            self.line(pts,P['live'],3.3)
        for (x1,y1),(x2,y2) in zip(pts[:-1],pts[1:]):
            length=math.hypot(x2-x1,y2-y1)
            if length<58:continue
            dx=(x2-x1)/length;dy=(y2-y1)/length
            phase=(time.monotonic()*.34)%1 if self.app.running and active else .55
            for dist in [length*(.25+.5*phase)]:
                x=x1+dx*dist;y=y1+dy*dist
                self.polygon([(x+dx*7,y+dy*7),(x-dx*4-dy*4,y-dy*4+dx*4),
                              (x-dx*4+dy*4,y-dy*4-dx*4)],
                             P['live'] if active else P['muted'])

    def section(self,x,y,w,h,num,title,sub=''):
        self.rounded(x,y,w,h,8,P['white'],P['border'])
        self.rect(x+1,y+1,w-2,38,P['panel'])
        self.text(x+16,y+18,num,11,P['blue'],True,anchor='w',mono=True)
        self.text(x+45,y+18,title,13,P['ink'],True,anchor='w')
        if sub:self.text(x+w-12,y+18,sub,10,P['muted'],anchor='e')
        self.line([(x,y+39),(x+w,y+39)],P['border'],.8)

    def display(self,x,y,w,label,value,unit='',color=None):
        self.text(x,y-12,label,10,P['muted'],True,anchor='w')
        self.rounded(x,y,w,38,4,P['display'])
        self.text(x+w-10,y+19,value,23,color or P['digit'],True,anchor='e',mono=True)
        if unit:self.text(x+w,y+48,unit,9,P['muted'],anchor='e')

    def pill(self,x,y,w,label,fill=None,color=None):
        self.rounded(x,y,w,22,5,fill or P['panel'])
        self.text(x+w/2,y+11,label,10,color or P['muted'],True)

    def vessel(self,x,y,w,h,active=False):
        self.rounded(x+2,y+3,w,h,h/2,'#CCD7DF')
        self.rounded(x,y,w,h,h/2,'#DCE5EB',P['steel'],1.3)
        for i in range(1,8):
            t=i/8
            self.rounded(x+5,y+3+i*2,w-10,h-6-i*4,max(3,h/2-i*2),
                         blend('#A4B6C3','#F9FCFE',t),None)
        self.line([(x+22,y+6),(x+w-22,y+6)],'#FFFFFF',1.2)
        for xx in (x+40,x+w-40):
            self.rect(xx-8,y+h-1,16,8,'#A5B6C3',P['steel'])
        if active:self.rounded(x,y,w,h,h/2,None,P['live'],1.8)

    def valve(self,x,y,tag,compact=False):
        v=self.app.sim.valves[tag]
        moving=abs(v.opening-v.command)>1e-6
        color=P['amber'] if moving else P['green'] if v.opening>1e-6 else P['red']
        face=blend(color,P['white'],.82)
        self.line([(x,y-4),(x,y-23)],P['ink'],1.2)
        self.rounded(x-11,y-34,22,12,2,face,color,1.2)
        self.polygon([(x-14,y-10),(x-14,y+10),(x,y)],face,color,1.5)
        self.polygon([(x+14,y-10),(x+14,y+10),(x,y)],face,color,1.5)
        self.oval(x-3,y-3,6,6,color)
        display_tag=tag if len(tag)<=13 else tag[:10]+'…'
        self.text(x,y-45,display_tag,11,P['ink'],True)
        status=f'{v.opening*100:.0f} → {v.command*100:.0f}%' if moving else (
            'OPEN' if v.opening>.999 else 'CLOSED' if v.opening<1e-6 else f'{v.opening*100:.1f}%')
        self.pill(x-39,y+17,78,status,blend(color,P['white'],.9),color)
        if not compact:self.text(x,y+52,f'Cv {v.spec.cv:g}',10,P['muted'],mono=True)
        self.hit('valve:'+tag,x-42,y-52,84,113,lambda:self.app.valve_dialog(tag),
                 f'{tag} | 실제 {v.opening*100:.1f}% · 명령 {v.command*100:.1f}% · {v.source}')

    def cooler(self,x,y,tag):
        self.oval(x-19,y-19,38,38,'#E8F1F6',P['steel'],1.6)
        self.line([(x-15,y+5),(x-8,y-6),(x,y+6),(x+8,y-6),(x+15,y+5)],P['blue'],1.5)
        self.text(x,y+29,tag,10,P['muted'])

    def compressor(self,x,y,active):
        # Two diaphragm heads, one drive/package; no invented stage readings.
        c=P['cyan'] if active else '#97ACB9'
        self.rounded(x-55,y+50,175,13,3,'#94A8B5')
        self.rounded(x-36,y-2,133,51,5,blend(c,P['white'],.72),P['steel'],1.4)
        self.oval(x+3,y+6,43,35,blend(c,P['white'],.2),P['ink'],1.1)
        self.oval(x+15,y+15,18,18,'#DCECF1',P['ink'],1)
        self.rect(x-57,y-32,28,63,blend(c,P['white'],.25),P['ink'],1.3)
        self.rect(x+87,y-32,28,63,blend(c,P['white'],.25),P['ink'],1.3)
        for xx in (x-53,x+91):
            for yy in (y-25,y+21):self.oval(xx,yy,4,4,P['ink'])
        self.text(x-43,y-2,'1',13,P['white'],True)
        self.text(x+101,y-2,'2',13,P['white'],True)
        self.line([(x-29,y+12),(x-15,y+12)],P['steel'],6)
        self.line([(x+70,y+12),(x+87,y+12)],P['steel'],6)
        self.text(x+29,y+39,'DIAPHRAGM',8,P['ink'],True)
        self.text(x+29,y+81,'C-103  /  1기 · 2단',12,P['ink'],True)
        self.hit('compressor',x-70,y-42,202,139,self.app.compressor_dialog,
                 'C-103 | 등가 압축기 상태·기동/정지 · 단별 온도/압력은 계산하지 않음')

    def redraw(self):
        if not self.winfo_exists() or self.winfo_width()<10:return
        s=self.app.sim
        names=list(s.cfg.equalize_order)+[n for n in s.banks if n not in s.cfg.equalize_order]
        if self.follow and s.selected_bank in names:self.page=names.index(s.selected_bank)//3
        self.page=min(self.page,max(0,(len(names)-1)//3))
        self._last_selected=s.selected_bank
        w,h=self.winfo_width(),self.winfo_height()
        self.scale=min(w/self.BASE_W,h/self.BASE_H)
        self.ox=(w-self.BASE_W*self.scale)/2;self.oy=(h-self.BASE_H*self.scale)/2
        self.delete('all');self._hit_targets={}
        live=s.flow_kg_h>1e-7
        by=live and s.route=='BYPASS';cp=live and s.route=='RECOVERY'
        # Area frames
        self.section(12,12,282,468,'01','TT 수취 / INLET','T-101')
        self.section(309,12,669,158,'02','직접 균압 / BYPASS')
        self.section(309,186,669,294,'03','압축 회수 / COMPRESSION',
                     'RUN' if s.compressor_speed>1e-6 else 'STANDBY')
        self.section(995,12,533,468,'04','BANK 충전 / PRIORITY')
        count=max(1,math.ceil(len(names)/3))
        self.pill(1320,20,82,f'{self.page+1:02} / {count:02}')
        self.text(1426,30,'‹',23,P['blue'],True)
        self.text(1461,30,'›',23,P['blue'],True)
        self.hit('bankprev',1410,16,30,28,lambda:self.change_page(-1),'이전 Bank 화면')
        self.hit('banknext',1446,16,30,28,lambda:self.change_page(1),'다음 Bank 화면')
        self.text(1506,30,'◎',19,P['live'] if self.follow else P['muted'])
        self.hit('bankfollow',1489,16,30,28,self.follow_active,'현재 선택 Bank 따라가기')
        # TT vessel illustration
        self.text(151,75,'HYDROGEN TUBE TRAILER',12,P['muted'],True)
        for yy in (103,128,153,178):self.vessel(43,yy,209,20)
        self.line([(36,214),(263,214)],P['ink'],3)
        self.rect(48,214,202,8,'#AFBECA')
        for xx in (80,218):
            self.oval(xx-13,222,26,26,P['ink'])
            self.oval(xx-7,228,14,14,'#C5D3DD')
        self.hit('tt',33,86,237,164,lambda:self.app.vessel_dialog('TT'),'T-101 | TT 재고·압력 및 종료압력 조회')
        self.display(35,276,156,'TT PRESSURE',f'{s.tt.pg:.2f}','barg')
        self.text(209,278,'종료 SP',10,P['muted'])
        self.text(210,303,f'{s.cfg.tt_stop_barg:g}',17,P['ink'],True,mono=True)
        self.text(210,321,'barg',9,P['muted'])
        self.line([(35,344),(270,344)],P['border'])
        for yy,label,value in [(366,'남은 수소',f'{s.tt.mass_kg:,.3f} kg'),
                               (393,'용기 내용적',f'{s.tt.spec.volume_m3:g} m³'),
                               (420,'등온 설정',f'{s.tt.spec.temperature_c:g} °C'),
                               (447,'총 이송량',f'{s.bypass_kg+s.compressed_kg:,.3f} kg')]:
            self.text(36,yy,label,11,P['muted'],anchor='w')
            self.text(268,yy,value,13,P['ink'],True,anchor='e',mono=True)
        # Main flow: drawing leaves geometry gaps for equipment, not fake topology.
        self.pipe([(252,156),(278,156),(278,110),(376,110)],live)
        self.valve(342,110,'MOV-101',True)
        self.pipe([(376,110),(470,110),(970,110),(1018,110)],by)
        self.valve(602,110,'BYPASS',True)
        self.text(760,63,'DIRECT TRANSFER',10,P['muted'],True)
        self.text(765,85,f'{s.bypass_kg:,.3f} kg',19,P['live'] if by else P['muted'],True,mono=True)
        self.text(874,143,f'종료 ΔP {s.cfg.equalize_dp_bar:g} bar / {s.cfg.equalize_hold_s:g} s',10,P['muted'],anchor='e')
        # Branch tee to compressor route
        self.oval(373,107,6,6,P['live'] if live else P['steel'])
        self.pipe([(376,110),(376,305),(418,305)],cp)
        self.pipe([(418,305),(496,305)],cp)
        self.valve(436,305,'PRV-102')
        self.compressor(553,305,s.compressor_speed>1e-6)
        self.pipe([(668,305),(713,305)],cp)
        self.cooler(734,305,'IC / AC')
        self.pipe([(755,305),(970,305),(1018,305)],cp)
        self.valve(884,305,'COMP-OUT')
        # Regulator setpoint and package values are not measured stage pressures.
        self.text(626,249,f'흡입 SP  {s.cfg.suction_set_barg:.2f} barg',13,P['ink'],True)
        # Header and faceplate identify the package equivalent; no duplicate label here.
        self.text(734,355,f'냉각 설정 {s.cfg.aftercooler_temperature_c:g} °C',10,P['muted'])
        for xx,ww,label,value,unit in [(329,139,'흡입 계산압력',f'{s.suction_barg:.2f}' if s.compressor_speed>1e-6 and s.route=='RECOVERY' else '—','barg'),
                  (489,137,'등가 구동',f'{s.compressor_speed*100:.1f}','%'),
                  (647,137,'정격 처리량',f'{s.cfg.compressor_kg_h:.2f}','kg/h'),
                  (806,147,'압축 이송량',f'{s.compressed_kg:.3f}','kg')]:
            self.text(xx,403,label,10,P['muted'],True,anchor='w')
            self.rounded(xx,415,ww,32,4,P['display'])
            self.text(xx+ww-9,431,value,18,P['digit'] if cp else '#BECED8',True,anchor='e',mono=True)
            self.text(xx+ww,461,unit,10,P['muted'],anchor='e')
        # Header and 3 visible banks
        self.pipe([(1018,110),(1018,428)],False)
        visible=names[self.page*3:self.page*3+3]
        active_name=s.active_bank()
        if live and active_name in visible:
            y_active=125+137*visible.index(active_name)
            self.pipe([(1018,110 if by else 305),(1018,y_active)],True)
        self.text(1018,69,'HEADER',9,P['muted'],True)
        for i,name in enumerate(names[self.page*3:self.page*3+3]):
            y=125+i*137
            b=s.banks[name];v=s.valves['BANK-'+name]
            active=live and s.active_bank()==name
            selected=name==s.selected_bank
            self.rounded(1050,y-60,459,124,7,'#EFF8F6' if selected else '#F7F9FB',
                         '#74B4A7' if selected else '#D1DCE4',1.5 if selected else .8)
            self.pipe([(1018,y),(1152,y)],active)
            self.valve(1090,y,'BANK-'+name,True)
            self.vessel(1155,y-13,333,51,active)
            shown=name if len(name)<=17 else name[:14]+'…'
            self.text(1171,y-39,f'BANK {shown}',14,P['ink'],True,anchor='w')
            self.text(1490,y-39,f'{b.spec.volume_m3:g} m³',12,P['muted'],anchor='e',mono=True)
            self.rounded(1184,y-6,151,32,4,P['display'])
            self.text(1324,y+10,f'{b.pg:.2f}',21,'#FFDC96',True,anchor='e',mono=True)
            self.text(1346,y+10,'barg',11,P['muted'],anchor='w')
            self.text(1468,y+8,f'{b.mass_kg:,.2f} kg',13,P['ink'],True,anchor='e',mono=True)
            self.text(1161,y+56,f'SP {b.spec.target_barg:g}  /  HH {s.cfg.hh(b.spec):g} barg',10,P['muted'],anchor='w')
            ratio=min(1,max(0,b.pg/max(1,b.spec.target_barg)))
            self.rect(1380,y+51,105,6,'#DCE6ED')
            self.rect(1380,y+51,105*ratio,6,P['live'] if selected else P['steel'])
            self.text(1373,y+54,'P/SP',8,P['muted'],anchor='e')
            self.hit('bank:'+name,1154,y-55,344,114,lambda n=name:self.app.vessel_dialog(n),
                     f'Bank {name} | P {b.pg:.3f} barg · 재고 {b.mass_kg:.3f} kg · SP {b.spec.target_barg:g} barg')
        # Legend / model fence, so no unmodelled gauges are implied.
        self.rounded(12,493,1516,35,5,P['panel'])
        legend=[(30,P['green'],'OPEN'),(128,P['red'],'CLOSED'),(234,P['amber'],'MOVING')]
        for x,col,lab in legend:
            self.oval(x,505,10,10,col)
            self.text(x+18,510,lab,10,P['muted'],anchor='w')
        self.text(365,510,'H₂ 유동 배관',10,P['muted'],anchor='w')
        self.line([(453,510),(498,510)],P['live'],3)
        self.text(525,510,'P/SP = 압력/목표압력 (액위 아님)',10,P['muted'],anchor='w')
        self.text(1508,510,'등온 · 1 Bank 선택 충전 · 실제 PLC I/O 없음',10,P['muted'],anchor='e')


class Trend(tk.Canvas):
    """Read-only trend with numeric axes and decimated draw data."""
    def __init__(self,parent,app,kind='pressure',mini=False,**kw):
        super().__init__(parent,bg=P['white'],highlightthickness=0,**kw)
        self.app=app;self.kind=kind;self.mini=mini
        self.bind('<Configure>',lambda e:self.redraw())
        self.bind('<Motion>',self._motion)
        self.bind('<Leave>',lambda e:self.delete('crosshair'))
        self._domain=None
    def _motion(self,e):
        self.delete('crosshair')
        if not self._domain or self.mini:return
        left,right,top,bottom,x0,x1,ymax=self._domain
        if not left<=e.x<=right or not top<=e.y<=bottom:return
        self.create_line(e.x,top,e.x,bottom,fill='#93A9B8',dash=(3,3),tags='crosshair')
        t=x0+(x1-x0)*(e.x-left)/(right-left)
        self.create_text(min(right-75,e.x+8),top+10,text=f'{t:.2f} min',anchor='w',
                         fill=P['ink'],font=(self.app.mono,-11),tags='crosshair')
    def redraw(self):
        if not self.winfo_ismapped() or self.winfo_width()<30:return
        s=self.app.sim;rows=list(s.history)
        r=s.snapshot()
        if not rows or r['time_s']>rows[-1]['time_s']:rows.append(r)
        # Limit draw points only. Model journal/sample interval are untouched.
        stride=max(1,len(rows)//650)
        if rows:rows=rows[::stride]+([rows[-1]] if len(rows)>1 else [])
        colors=['#BB7525','#21856F','#7963B2','#B34E7A','#348D9C','#879034','#A25938','#55738C']
        names=list(s.banks)[:8]
        defs={
            'pressure':('압력','barg',[('tt_barg','TT',P['blue'])]+[(f'bank_{n}_barg',n,colors[i]) for i,n in enumerate(names)]),
            'flow':('순간 이송 유량','kg/h',[('flow_kg_h','TOTAL',P['live'])]+([] if self.mini else [(f'bank_{n}_in_kg_h',n,colors[i]) for i,n in enumerate(names)])),
            'opening':('밸브 실개도','%',[(tag+'_actual_pct',tag,col) for tag,col in
                 [('MOV-101',P['blue']),('BYPASS',P['live']),('PRV-102',colors[2]),('BANK-'+s.selected_bank,colors[0])]]),
            'inventory':('수소 재고','kg',[('tt_kg','TT',P['blue'])]+[(f'bank_{n}_kg',n,colors[i]) for i,n in enumerate(names)])}
        title,unit,series=defs[self.kind]
        self.delete('all');w=self.winfo_width();h=self.winfo_height()
        left=54;right=w-25;top=49 if self.mini else 74;bottom=h-31 if self.mini else h-42
        if right<=left or bottom<=top:return
        self.create_text(14,17,text=title+'  /  '+unit,anchor='w',fill=P['ink'],font=(self.app.face,-11 if self.mini else -16,'bold'))
        max_val=max([float(row.get(k,0)) for row in rows for k,_,_ in series] or [1.0])
        ymax=105 if self.kind=='opening' else max(1,max_val*1.12)
        if self.kind!='opening':
            order=10**math.floor(math.log10(ymax));ymax=math.ceil(ymax/order*2)/2*order
        x0=rows[0]['time_s']/60 if rows else 0;x1=max(x0+1,rows[-1]['time_s']/60 if rows else 1)
        self._domain=(left,right,top,bottom,x0,x1,ymax)
        for i in range(5):
            y=bottom-(bottom-top)*i/4
            self.create_line(left,y,right,y,fill='#E5ECF1')
            val=ymax*i/4
            self.create_text(left-8,y,text=f'{val:.0f}' if ymax>10 else f'{val:.1f}',anchor='e',
                             fill=P['muted'],font=(self.app.mono,-9 if self.mini else -11))
        for i in range(6):
            x=left+(right-left)*i/5
            self.create_text(x,bottom+15,text=f'{x0+(x1-x0)*i/5:.1f}',fill=P['muted'],font=(self.app.mono,-9 if self.mini else -11))
        self.create_text(right,bottom+29,text='min',anchor='e',fill=P['muted'],font=(self.app.mono,-9))
        lx=155 if self.mini else 20;ly=17 if self.mini else 45
        for j,(key,label,col) in enumerate(series):
            full_label=label
            if self.mini and len(label)>10:label=label[:8]+'…'
            if lx+65>right:lx=155 if self.mini else 20;ly+=16
            self.create_line(lx,ly,lx+14,ly,fill=col,width=2)
            self.create_text(lx+19,ly,text=label,anchor='w',fill=P['muted'],font=(self.app.face,-9 if self.mini else -11))
            lx+=max(50,len(label)*7+35)
            pts=[]
            for row in rows:
                x=left+(row['time_s']/60-x0)/(x1-x0)*(right-left)
                y=bottom-float(row.get(key,0))/ymax*(bottom-top)
                pts.extend((x,y))
            if len(pts)>=4:self.create_line(*pts,fill=col,width=1.7 if self.mini else 2.2)
            if pts:self.create_oval(pts[-2]-2,pts[-1]-2,pts[-2]+2,pts[-1]+2,fill=col,outline='')
        if self.kind=='flow' and self.mini:
            self.create_text(right,17,text=f'{s.flow_kg_h:.2f} kg/h',anchor='e',fill=P['live'],font=(self.app.mono,-12,'bold'))
