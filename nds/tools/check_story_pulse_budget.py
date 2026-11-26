"""Check ARM-emulated ppulse work against its authored 6-tick frame interval.

Wall-clock Python speed and cumulative boot maxima are not performance proof.
The input is the read-only actual-ROM trace from verify_story_pulse_perf.py.
"""
from pathlib import Path
import argparse,json,sys

def check(report,budget_ms=100.,stage_prefix='hold_alpha'):
    failures=[];rows=[]
    if report.get('result')!='CAPTURED':failures.append('Input is not a completed actual-ROM capture')
    for row in report.get('segments',[]):
        if not row['stage'].startswith(stage_prefix):continue
        counters=row['counters'];cost=row['observed_last_prepare_max_ms']
        pure=all(counters[key]==0 for key in ('io_vblanks','text_reads','gfx_reads')) and row['lower_dirty_samples']==0
        if not pure:failures.append(row['stage']+': sample includes I/O or lower rendering')
        if cost is None or cost>=budget_ms:failures.append(row['stage']+': upper preparation exceeds requested source interval')
        tail=row['last']
        if tail['errors'] or tail['underruns'] or tail['sfx_underruns']:failures.append(row['stage']+': audio errors/underruns')
        rows.append(dict(stage=row['stage'],observed_prepare_max_ms=cost,pure_upper_render=pure,gfx_changes=row['gfx_changes'],tick_intervals=row['gfx_change_tick_intervals']))
    if not rows:failures.append('No matching held rendering interval was captured')
    return dict(result='FAIL' if failures else 'PASS',budget_ms=budget_ms,stage_prefix=stage_prefix,rom_sha256=report.get('identity',{}).get('rom_sha256'),stages=rows,failures=failures,
        limitation='Sampled last_prepare on the same emulated ARM core, not a physical-R4 guarantee. Checks the requested source/typing interval; does not establish 60fps animation.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget-ms',type=float,default=100.);p.add_argument('--stage-prefix',default='hold_alpha');a=p.parse_args()
    result=check(json.loads(a.report.read_text(encoding='utf8')),a.budget_ms,a.stage_prefix);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2));return result['result']!='PASS'

if __name__=='__main__':sys.exit(main())
