#!/usr/bin/env python3
"""Launch the offline HMI or run the same engine headlessly."""
from pathlib import Path
import argparse
import json
import sys
from h2sim.engine import Config, Simulator


def main():
    p=argparse.ArgumentParser(description='H2 TT Receiving HMI v0.2.2 (engine v0.2.0) — offline isothermal simulator')
    p.add_argument('--config',type=Path)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--auto',action='store_true')
    p.add_argument('--compress-only',action='store_true')
    p.add_argument('--hours',type=float,default=12.0)
    p.add_argument('--out',type=Path,default=Path('results'))
    p.add_argument('--xlsx',type=Path,help='Headless: write full journal + editable pressure chart')
    args=p.parse_args()
    try:
        cfg=Config.load(args.config) if args.config else Config()
        if args.headless:
            if not 0<args.hours<=168:raise ValueError('--hours: 0~168시간')
            s=Simulator(cfg)
            try:
                if args.auto or args.compress_only:s.start_auto(args.compress_only)
                end=args.hours*3600
                while s.time_s<end-1e-8:
                    s.step(min(cfg.dt_s,end-s.time_s))
                    if s.phase in ('DONE','TRIP') and s.settled:break
                s.export(args.out)
                if args.xlsx:
                    from h2sim.trend_data import capture_trend_snapshot, signals_for, default_signal_keys
                    from h2sim.xlsx_export import write_trend_xlsx
                    keys=set(default_signal_keys(s,'pressure'))
                    write_trend_xlsx(args.xlsx,capture_trend_snapshot(s),'pressure',[x for x in signals_for(s,'pressure') if x.key in keys])
                print(json.dumps(s.snapshot(),ensure_ascii=False,indent=2))
            finally:s.close()
        else:
            from h2sim.gui import App
            app=App(cfg)
            if args.auto or args.compress_only:app.auto(args.compress_only)
            app.mainloop()
    except (ValueError,RuntimeError,ImportError,ArithmeticError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
