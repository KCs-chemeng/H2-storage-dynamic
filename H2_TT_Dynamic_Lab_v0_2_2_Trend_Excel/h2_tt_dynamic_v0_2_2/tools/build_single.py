"""Build the dependency-free single-source edition from the package modules."""
from pathlib import Path
import ast
root=Path(__file__).resolve().parents[1]
parts=['#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n',
       '"""H2 TT Dynamic Lab HMI v0.2.2 (engine v0.2.0) — OFFLINE ONLY, Python >=3.10 + Tkinter.\n'
       'Generated from the accompanying h2sim modules; edit modular source first.\n'
       'See README_KO.md and MODEL_NOTES.md for assumptions and limitations.\n"""\n',
       'from __future__ import annotations\n\n']
parts.append('\n'.join('# '+line for line in (root/'THIRD_PARTY_NOTICES.txt').read_text().splitlines())+'\n')
for rel in ['h2sim/physics.py','h2sim/config.py','h2sim/controls.py','h2sim/engine.py','h2sim/editors.py','h2sim/hmi.py','h2sim/trend_data.py','h2sim/xlsx_export.py','h2sim/trend_popup.py','h2sim/gui.py','main.py']:
    text=(root/rel).read_text(encoding='utf-8')
    tree=ast.parse(text);remove=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and (node.level or (node.module or '').startswith('h2sim') or node.module=='__future__'):
            remove.update(range(node.lineno,node.end_lineno+1))
    lines=[line for i,line in enumerate(text.splitlines(),1) if i not in remove and not line.startswith('#!')]
    parts.append('\n\n# ===== '+rel+' =====\n'+'\n'.join(lines)+'\n')
output=root/'H2_TT_Dynamic_v0_2_2.py';output.write_text(''.join(parts),encoding='utf-8')
ast.parse(output.read_text(),feature_version=(3,10))
print(output)
