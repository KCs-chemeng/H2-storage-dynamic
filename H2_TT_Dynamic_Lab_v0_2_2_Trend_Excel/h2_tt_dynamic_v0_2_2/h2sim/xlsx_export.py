"""Portable native XLSX export, using only Python's standard library.

Writes an OOXML workbook and an editable XY chart. No Excel/COM, macros,
network service or mandatory third-party dependency. This module is only an
output serializer; it never evaluates the process model or changes its state.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile
from xml.etree import ElementTree as ET
from .trend_data import TrendSnapshot, TrendSignal, select_rows, flatten_inputs, field_unit, TREND_GROUPS

_X='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_P='http://schemas.openxmlformats.org/package/2006/relationships'
_C='http://schemas.openxmlformats.org/drawingml/2006/chart'
_A='http://schemas.openxmlformats.org/drawingml/2006/main'
_D='http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
_CT='http://schemas.openxmlformats.org/package/2006/content-types'

@dataclass(frozen=True)
class _XFormula:
    expression: str
    cached: float


def _xe(parent,tag,attributes=None,text=None,ns=_X):
    element=ET.SubElement(parent,'{'+ns+'}'+tag,{str(k):str(v) for k,v in (attributes or {}).items()})
    if text is not None: element.text=str(text)
    return element


def _xmlbytes(root):
    data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    # OPC readers commonly expect the document namespace as the default namespace.
    # Local serialization avoids process-global namespace registry changes in workers.
    if root.tag.startswith(('{'+_X+'}', '{'+_P+'}', '{'+_CT+'}')):
        data=data.replace(b'xmlns:ns0=',b'xmlns=').replace(b'<ns0:',b'<').replace(b'</ns0:',b'</')
    return data


def _col(index):
    result=''
    while index:
        index,r=divmod(index-1,26);result=chr(65+r)+result
    return result


def _clean(value):
    # Text fields are inline strings, so =, +, @, - never become formulas.
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]','',str(value))[:32767]


def _styles():
    root=ET.Element('{'+_X+'}styleSheet')
    nf=_xe(root,'numFmts',{'count':3})
    for num,fmt in [(164,'0.000'),(165,'0.000000'),(166,'0.000E+00')]:_xe(nf,'numFmt',{'numFmtId':num,'formatCode':fmt})
    fonts=_xe(root,'fonts',{'count':4})
    for size,bold,color in [(10,False,'243746'),(17,True,'FFFFFF'),(10,True,'FFFFFF'),(10,False,'5B6E7D')]:
        f=_xe(fonts,'font');_xe(f,'sz',{'val':size});_xe(f,'name',{'val':'Malgun Gothic'});_xe(f,'color',{'rgb':'FF'+color})
        if bold:_xe(f,'b')
    fills=_xe(root,'fills',{'count':5})
    for pattern,color in [('none',None),('gray125',None),('solid','17324D'),('solid','EAF0F5'),('solid','F5F8FB')]:
        p=_xe(_xe(fills,'fill'),'patternFill',{'patternType':pattern})
        if color:_xe(p,'fgColor',{'rgb':'FF'+color});_xe(p,'bgColor',{'indexed':64})
    borders=_xe(root,'borders',{'count':1});b=_xe(borders,'border')
    for side in ('left','right','top','bottom','diagonal'):_xe(b,side)
    sx=_xe(root,'cellStyleXfs',{'count':1});_xe(sx,'xf',{'numFmtId':0,'fontId':0,'fillId':0,'borderId':0})
    xfs=_xe(root,'cellXfs',{'count':9})
    # 0=text, 1=number, 2=title, 3=header, 4=subtitle, 5=band-text,
    # 6=scientific, 7=high-precision, 8=band-number.
    definitions=[(0,0,0,False),(164,0,0,False),(0,1,2,False),(0,2,2,True),(0,3,0,True),
                 (0,0,4,False),(166,0,0,False),(165,0,0,False),(164,0,4,False)]
    for fmt,fontid,fill,wrap in definitions:
        xf=_xe(xfs,'xf',{'numFmtId':fmt,'fontId':fontid,'fillId':fill,'borderId':0,'xfId':0,'applyNumberFormat':1,'applyAlignment':1})
        _xe(xf,'alignment',{'vertical':'center','wrapText':int(wrap)})
    cs=_xe(root,'cellStyles',{'count':1});_xe(cs,'cellStyle',{'name':'Normal','xfId':0,'builtinId':0})
    return _xmlbytes(root)


def _cell(row,index,value,style=None):
    if value is None:return
    attrs={'r':f'{_col(index)}{row.attrib["r"]}'}
    is_number=isinstance(value,(float,int)) and not isinstance(value,bool)
    attrs['s']=str(style if style is not None else (1 if is_number else 0))
    if isinstance(value,_XFormula):
        if not math.isfinite(value.cached):raise ValueError('비유한 수식 결과')
        c=_xe(row,'c',attrs);_xe(c,'f',text=value.expression.lstrip('='));_xe(c,'v',text=repr(value.cached))
    elif is_number:
        if not math.isfinite(value):raise ValueError('Excel에 NaN/Inf를 저장할 수 없습니다.')
        _xe(_xe(row,'c',attrs),'v',text=repr(value))
    elif isinstance(value,bool):
        attrs['t']='b';_xe(_xe(row,'c',attrs),'v',text=int(value))
    else:
        attrs['t']='inlineStr';c=_xe(row,'c',attrs);t=_xe(_xe(c,'is'),'t',text=_clean(value))
        t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')


def _sheet(title,subtitle,headers,rows,header_row=4,chart=False,widths=None):
    root=ET.Element('{'+_X+'}worksheet')
    ncol=max(len(headers),1);last=header_row+len(rows)
    title_cols=12 if chart else min(ncol,12)
    _xe(root,'dimension',{'ref':f'A1:{_col(max(ncol,title_cols))}{max(last,header_row+1)}'})
    views=_xe(root,'sheetViews');view=_xe(views,'sheetView',{'workbookViewId':0,'showGridLines':0})
    _xe(view,'pane',{'xSplit':1,'ySplit':header_row,'topLeftCell':f'B{header_row+1}','activePane':'bottomRight','state':'frozen'})
    _xe(root,'sheetFormatPr',{'defaultRowHeight':18})
    cols=_xe(root,'cols')
    for i,h in enumerate(headers,1):
        width=(widths or {}).get(i,min(32,max(15,len(str(h))*.8+3)))
        _xe(cols,'col',{'min':i,'max':i,'width':width,'customWidth':1})
    data=_xe(root,'sheetData')
    r=_xe(data,'row',{'r':1,'ht':32,'customHeight':1});_cell(r,1,title,2)
    for i in range(2,title_cols+1):_cell(r,i,'',2)
    r=_xe(data,'row',{'r':2,'ht':34,'customHeight':1});_cell(r,1,subtitle,4)
    r=_xe(data,'row',{'r':header_row,'ht':36,'customHeight':1})
    for i,h in enumerate(headers,1):_cell(r,i,h,3)
    for ri,values in enumerate(rows,header_row+1):
        r=_xe(data,'row',{'r':ri,'ht':21,'customHeight':1})
        for ci,v in enumerate(values,1):
            sty=None
            if isinstance(v,_XFormula):sty=1
            elif ri%2==0:sty=8 if isinstance(v,(float,int)) and not isinstance(v,bool) else 5
            if ci<=len(headers) and headers[ci-1]=='mass_error_kg':sty=6
            if ci==2 and values and values[0]=='최대 절대 질량수지 오차':sty=6
            if ci==2 and values and values[0] in ('저장 표본 수','선택 변수 수','차트 표시 변수 수'):sty=0
            _cell(r,ci,v,sty)
    if rows:_xe(root,'autoFilter',{'ref':f'A{header_row}:{_col(ncol)}{last}'})
    merges=_xe(root,'mergeCells',{'count':2})
    _xe(merges,'mergeCell',{'ref':f'A1:{_col(title_cols)}1'})
    _xe(merges,'mergeCell',{'ref':f'A2:{_col(title_cols)}2'})
    _xe(root,'pageMargins',{'left':'.3','right':'.3','top':'.4','bottom':'.4','header':'.2','footer':'.2'})
    if chart:_xe(root,'drawing',{'{'+_R+'}id':'rId1'})
    return _xmlbytes(root)


def _chart_title(parent,title):
    elem=_xe(parent,'title',ns=_C);tx=_xe(elem,'tx',ns=_C);rich=_xe(tx,'rich',ns=_C)
    _xe(rich,'bodyPr',ns=_A);_xe(rich,'lstStyle',ns=_A);p=_xe(rich,'p',ns=_A)
    r=_xe(p,'r',ns=_A);_xe(r,'rPr',{'lang':'ko-KR','sz':1250},ns=_A);_xe(r,'t',text=title,ns=_A)
    _xe(elem,'overlay',{'val':0},ns=_C)


def _chart_xml(title,signals,rows):
    root=ET.Element('{'+_C+'}chartSpace')
    _xe(root,'lang',{'val':'ko-KR'},ns=_C)
    chart=_xe(root,'chart',ns=_C);_chart_title(chart,title)
    plot=_xe(chart,'plotArea',ns=_C);_xe(plot,'layout',ns=_C)
    scatter=_xe(plot,'scatterChart',ns=_C);_xe(scatter,'scatterStyle',{'val':'line'},ns=_C)
    start=29;end=start+len(rows)-1
    # A numeric X axis preserves unequal recording intervals and event timing.
    for idx,signal in enumerate(signals):
        ser=_xe(scatter,'ser',ns=_C);_xe(ser,'idx',{'val':idx},ns=_C);_xe(ser,'order',{'val':idx},ns=_C)
        tx=_xe(ser,'tx',ns=_C);_xe(tx,'v',text=signal.label,ns=_C)
        marker=_xe(ser,'marker',ns=_C);_xe(marker,'symbol',{'val':'none'},ns=_C)
        for name,col,vals in [('xVal','B',[r['time_s']/60 for r in rows]),('yVal',_col(idx+3),[r.get(signal.key,0) for r in rows])]:
            ref=_xe(_xe(ser,name,ns=_C),'numRef',ns=_C)
            _xe(ref,'f',text=f"'선택추세'!${col}${start}:${col}${end}",ns=_C)
            # Large runs still reference every data cell. Cache is optional.
            if len(rows)<=10000:
                cache=_xe(ref,'numCache',ns=_C);_xe(cache,'formatCode',text='General',ns=_C)
                _xe(cache,'ptCount',{'val':len(vals)},ns=_C)
                for n,v in enumerate(vals):_xe(_xe(cache,'pt',{'idx':n},ns=_C),'v',text=repr(float(v)),ns=_C)
        _xe(ser,'smooth',{'val':0},ns=_C)
    _xe(scatter,'axId',{'val':101},ns=_C);_xe(scatter,'axId',{'val':102},ns=_C)
    for aid,pos,cross,label in [(101,'b',102,'모사시간 [min]'),(102,'l',101,signals[0].unit)]:
        axis=_xe(plot,'valAx',ns=_C);_xe(axis,'axId',{'val':aid},ns=_C)
        scale=_xe(axis,'scaling',ns=_C);_xe(scale,'orientation',{'val':'minMax'},ns=_C)
        _xe(axis,'delete',{'val':0},ns=_C);_xe(axis,'axPos',{'val':pos},ns=_C)
        if aid==102:_xe(axis,'majorGridlines',ns=_C)
        _chart_title(axis,label);_xe(axis,'numFmt',{'formatCode':'0.00','sourceLinked':0},ns=_C)
        _xe(axis,'majorTickMark',{'val':'out'},ns=_C);_xe(axis,'minorTickMark',{'val':'none'},ns=_C)
        _xe(axis,'tickLblPos',{'val':'nextTo'},ns=_C);_xe(axis,'crossAx',{'val':cross},ns=_C)
        _xe(axis,'crosses',{'val':'autoZero'},ns=_C);_xe(axis,'crossBetween',{'val':'midCat'},ns=_C)
    leg=_xe(chart,'legend',ns=_C);_xe(leg,'legendPos',{'val':'b'},ns=_C);_xe(leg,'overlay',{'val':0},ns=_C)
    _xe(chart,'plotVisOnly',{'val':1},ns=_C);_xe(chart,'dispBlanksAs',{'val':'gap'},ns=_C)
    return _xmlbytes(root)


def _drawing():
    root=ET.Element('{'+_D+'}wsDr');anchor=_xe(root,'twoCellAnchor',ns=_D)
    for name,col,row in [('from',0,3),('to',12,26)]:
        mark=_xe(anchor,name,ns=_D)
        for n,v in [('col',col),('colOff',0),('row',row),('rowOff',0)]:_xe(mark,n,text=v,ns=_D)
    frame=_xe(anchor,'graphicFrame',{'macro':''},ns=_D);nv=_xe(frame,'nvGraphicFramePr',ns=_D)
    _xe(nv,'cNvPr',{'id':2,'name':'Editable trend chart'},ns=_D);_xe(nv,'cNvGraphicFramePr',ns=_D)
    xf=_xe(frame,'xfrm',ns=_D);_xe(xf,'off',{'x':0,'y':0},ns=_A);_xe(xf,'ext',{'cx':0,'cy':0},ns=_A)
    gd=_xe(_xe(frame,'graphic',ns=_A),'graphicData',{'uri':_C},ns=_A)
    _xe(gd,'chart',{'{'+_R+'}id':'rId1'},ns=_C);_xe(anchor,'clientData',ns=_D)
    return _xmlbytes(root)


def _rels(items):
    root=ET.Element('{'+_P+'}Relationships')
    for rid,kind,target in items:_xe(root,'Relationship',{'Id':rid,'Type':_R+'/'+kind,'Target':target},ns=_P)
    return _xmlbytes(root)


def write_trend_xlsx(path, snapshot: TrendSnapshot, group: str, signals: list[TrendSignal],
                     start_s=None, end_s=None) -> dict:
    """Atomically write full-precision numeric records and an editable chart.

    Selection bounds are inclusive and filter recorded samples only. No
    interpolation and no display downsampling is applied to exported values.
    """
    path=Path(path)
    if path.suffix.lower()!='.xlsx':raise ValueError('저장 확장자는 .xlsx여야 합니다.')
    if not signals:raise ValueError('내보낼 변수를 하나 이상 선택하세요.')
    if group not in TREND_GROUPS:raise ValueError('잘못된 추세 항목')
    if len({s.unit for s in signals})!=1:raise ValueError('선택추세에는 동일 단위의 변수만 저장하세요.')
    rows=select_rows(snapshot.rows,start_s,end_s)
    if not rows:raise ValueError('지정 구간에 저장된 표본이 없습니다.')
    if len(rows)>1048500:raise ValueError('Excel 단일 시트 행 한도를 초과합니다. 시간구간을 나눠 저장하세요.')
    fields=list(rows[0])
    if any(s.key not in fields for s in signals):raise ValueError('선택 변수와 실험 구성이 다릅니다.')
    raw_fields=['time_s','time_min']+[k for k in fields if k!='time_s']
    raw_rows=[];trend_rows=[]
    for i,r in enumerate(rows,5):
        raw_rows.append([r['time_s'],_XFormula(f'A{i}/60',r['time_s']/60)]+[r.get(k) for k in fields if k!='time_s'])
    for i,r in enumerate(rows,29):
        trend_rows.append([r['time_s'],_XFormula(f'A{i}/60',r['time_s']/60)]+[r.get(s.key) for s in signals])
    events=[e for e in snapshot.events if (start_s is None or e['time_s']>=start_s-1e-9)
            and (end_s is None or e['time_s']<=end_s+1e-9)]
    lo=rows[0]['time_s'];hi=rows[-1]['time_s'];nlast=4+len(rows)
    cmap={k:_col(i) for i,k in enumerate(raw_fields,1)}
    maxflow=max(float(r['flow_kg_h']) for r in rows)
    masserr=max(abs(float(r['mass_error_kg'])) for r in rows)
    summary=[
        ['프로그램','H₂ TT Dynamic Lab / HMI 0.2.2, Engine 0.2.0','물리·제어 엔진 유지'],
        ['EOS',snapshot.current_config['eos'],'선택한 EOS로 모든 재고·압력 계산'],
        ['모델 범위','순수 수소 / 등온 / 유한재고 / Cv 준정상 유동','실제 PID·열수지·압력파 제외'],
        ['내보내기 시각',datetime.now(timezone.utc).isoformat(timespec='seconds'),'UTC'],
        ['Snapshot 모사시각',snapshot.snapshot_time_s,'s'],
        ['구간 시작',_XFormula("'전체기록'!A5",lo),'s'],
        ['구간 종료',_XFormula(f"'전체기록'!A{nlast}",hi),'s'],
        ['구간 길이',_XFormula(f"'전체기록'!A{nlast}-'전체기록'!A5",hi-lo),'s'],
        ['저장 표본 수',_XFormula(f"ROWS('전체기록'!A5:A{nlast})",len(rows)),'표시 간소화 없이 저장'],
        ['최대 순간유량',_XFormula(f"MAX('전체기록'!{cmap['flow_kg_h']}5:{cmap['flow_kg_h']}{nlast})",maxflow),'kg/h'],
        ['구간 이송량',_XFormula(f"'전체기록'!{cmap['total_transferred_kg']}{nlast}-'전체기록'!{cmap['total_transferred_kg']}5",rows[-1]['total_transferred_kg']-rows[0]['total_transferred_kg']),'kg / 누적값의 차'],
        ['최대 절대 질량수지 오차',masserr,'kg'],
        ['추세 종류',TREND_GROUPS[group][0],TREND_GROUPS[group][1]],
        ['선택 변수 수',len(signals),'모든 선택 변수는 숫자 데이터로 저장'],
        ['차트 표시 변수 수',min(12,len(signals)),'가독성을 위해 최초 12개 / 표의 변수는 생략 없음'],
        ['기록간격',snapshot.current_config['sample_s'],'s / 적분간격과 구분'],
        ['표준체적 기준','0°C / 1.01325 bara','MW=2.016 kg/kmol; Vm=22.414 Nm³/kmol'],
        ['자료 출처',snapshot.source,'현재 실험의 디스크 저널을 포함'],
        ['반올림','표시 형식만 적용','내부 저장값은 계산 정밀도 유지'],
        ['전력량','reference_kWh는 참조부하 환산값','실제 압축기 입력전력 예측값과 구분'],
        ['선택구간','기록 표본에 대해 시작·끝 경계를 포함하여 필터','구간 경계의 보간은 수행하지 않음'],
        ['현재 실험','EOS/설비 구성 변경은 새 실험으로 초기화','서로 다른 EOS 실행결과를 한 시계열에 혼합하지 않음'],
    ]
    inputs=[]
    for prefix,cfg in [('초기',snapshot.initial_config),('현재',snapshot.current_config)]:
        inputs.extend([[prefix,k,v] for k,v in flatten_inputs(cfg)])
    for i,st in enumerate(snapshot.stop_history):inputs.extend([[f'정지이력[{i}]',k,v] for k,v in flatten_inputs(st)])
    key_labels={s.key:s.label for s in signals}
    dictionary=[[k,field_unit(k),key_labels.get(k,k)] for k in raw_fields]
    source_note=f"{lo:.3f}–{hi:.3f} s | EOS: {snapshot.current_config['eos']} | 표본 {len(rows):,}개 | 전체 저장 정밀도 유지"
    chart_signals=signals[:12]
    sheets=[
        ('요약',_sheet('TT 수취 시뮬레이션 · 결과 요약',source_note,['항목','값','단위 / 설명'],summary,widths={1:29,2:48,3:62})),
        ('선택추세',_sheet(TREND_GROUPS[group][0]+' · 편집 가능한 Excel 추세',source_note,
            ['time_s','time_min']+[f'{s.label} [{s.unit}]' for s in signals],trend_rows,header_row=28,chart=True)),
        ('전체기록',_sheet('원시 시간 이력 · 전체 변수',source_note,raw_fields,raw_rows)),
        ('이벤트',_sheet('운전·밸브·설정값 이벤트',source_note,['time_s','code','message','detail'],
            [[e.get(k,'') for k in ('time_s','code','message','detail')] for e in events],widths={1:16,2:27,3:75,4:80})),
        ('입력조건',_sheet('초기·현재 입력값 및 정지 상세이력',source_note,['구분','입력 경로','값'],inputs,widths={1:16,2:62,3:70})),
        ('변수설명',_sheet('시간 이력의 변수·단위',source_note,['변수 키','단위','표시명'],dictionary,widths={1:50,2:40,3:55})),
    ]
    workbook=ET.Element('{'+_X+'}workbook');views=_xe(workbook,'bookViews');_xe(views,'workbookView',{'activeTab':0})
    sheetlist=_xe(workbook,'sheets')
    for i,(name,_) in enumerate(sheets,1):_xe(sheetlist,'sheet',{'name':name,'sheetId':i,'{'+_R+'}id':f'rId{i}'})
    _xe(workbook,'calcPr',{'calcId':191029,'fullCalcOnLoad':1})
    ct=ET.Element('{'+_CT+'}Types')
    for ext,kind in [('rels','application/vnd.openxmlformats-package.relationships+xml'),('xml','application/xml')]:
        _xe(ct,'Default',{'Extension':ext,'ContentType':kind},ns=_CT)
    overrides=[('/xl/workbook.xml','spreadsheetml.sheet.main'),('/xl/styles.xml','spreadsheetml.styles'),
               ('/xl/drawings/drawing1.xml','drawing'),('/xl/charts/chart1.xml','drawingml.chart')]
    overrides += [(f'/xl/worksheets/sheet{i}.xml','spreadsheetml.worksheet') for i in range(1,len(sheets)+1)]
    for part,kind in overrides:
        _xe(ct,'Override',{'PartName':part,'ContentType':'application/vnd.openxmlformats-officedocument.'+kind+'+xml'},ns=_CT)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tempname=tempfile.mkstemp(prefix='h2_export_',suffix='.xlsx',dir=path.parent);os.close(fd)
    try:
        with zipfile.ZipFile(tempname,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.writestr('[Content_Types].xml',_xmlbytes(ct))
            z.writestr('_rels/.rels',_rels([('rId1','officeDocument','xl/workbook.xml')]))
            z.writestr('xl/workbook.xml',_xmlbytes(workbook))
            rels=[(f'rId{i}','worksheet',f'worksheets/sheet{i}.xml') for i in range(1,len(sheets)+1)]
            rels.append((f'rId{len(sheets)+1}','styles','styles.xml'))
            z.writestr('xl/_rels/workbook.xml.rels',_rels(rels));z.writestr('xl/styles.xml',_styles())
            for i,(_,payload) in enumerate(sheets,1):z.writestr(f'xl/worksheets/sheet{i}.xml',payload)
            z.writestr('xl/worksheets/_rels/sheet2.xml.rels',_rels([('rId1','drawing','../drawings/drawing1.xml')]))
            z.writestr('xl/drawings/drawing1.xml',_drawing())
            z.writestr('xl/drawings/_rels/drawing1.xml.rels',_rels([('rId1','chart','../charts/chart1.xml')]))
            z.writestr('xl/charts/chart1.xml',_chart_xml(TREND_GROUPS[group][0]+' / '+snapshot.current_config['eos'],chart_signals,rows))
        os.chmod(tempname, 0o644)
        os.replace(tempname,path)
    except BaseException:
        try:os.unlink(tempname)
        except OSError:pass
        raise
    return {'path':str(path),'rows':len(rows),'events':len(events),'signals':len(signals),
            'chart_signals':len(chart_signals),'eos':snapshot.current_config['eos'],
            'start_s':lo,'end_s':hi,'sheets':[name for name,_ in sheets]}
