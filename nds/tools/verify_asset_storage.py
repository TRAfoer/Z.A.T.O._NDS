"""Compare all frames using asset_pack.c, never the Python compactor's decoder."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before',required=True,type=Path)
    p.add_argument('--after',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--expect-frames',type=int,default=505)
    a=p.parse_args()
    sources=[ROOT/'nds/source/asset_pack.c',ROOT/'nds/source/asset_pack.h',ROOT/'nds/tests/validate_asset_storage.c']
    identity=dict(before=dict(path=str(a.before.resolve()),sha256=sha(a.before),bytes=a.before.stat().st_size),
        after=dict(path=str(a.after.resolve()),sha256=sha(a.after),bytes=a.after.stat().st_size),
        source_sha256={str(path.relative_to(ROOT)):sha(path) for path in sources},tool_sha256=sha(Path(__file__)),
        decoder='Unmodified nds/source/asset_pack.c pack_open + pack_decode_alpha, separately opened before/after file backends',
        comparison='C memcmp of every decoded RGB15 byte and alpha8 byte, frame metadata from byte16 onward, semantic header and stage/actor ID lists')
    compiler=shutil.which('gcc')
    if not compiler:raise RuntimeError('Host gcc is required')
    identity['compiler']=compiler
    identity['compiler_version']=subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    report=dict(result='FAIL',identity=identity)
    code=1
    with tempfile.TemporaryDirectory(prefix='zato_asset_storage_') as folder:
        exe=Path(folder)/'validate.exe'
        command=[compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(sources[2]),str(sources[0]),'-o',str(exe)]
        compiled=subprocess.run(command,capture_output=True,text=True)
        report['compile']=dict(returncode=compiled.returncode,stderr=compiled.stderr)
        if compiled.returncode:
            report['error']='Host C verifier compilation failed'
        else:
            identity['verifier_executable_sha256']=sha(exe)
            run=subprocess.run([str(exe),str(a.before.resolve()),str(a.after.resolve())],capture_output=True,text=True)
            report['native_returncode']=run.returncode
            report['native_stderr']=run.stderr
            try:report['validation']=json.loads(run.stdout)
            except json.JSONDecodeError:report.update(error='Invalid verifier JSON',native_stdout=run.stdout)
            if not run.returncode and report.get('validation',{}).get('result')=='PASS':
                if report['validation']['frames_checked']!=a.expect_frames:
                    report['error']='Unexpected frame count'
                elif sha(a.before)!=identity['before']['sha256'] or sha(a.after)!=identity['after']['sha256']:
                    report['error']='An input package changed during verification'
                elif any(sha(path)!=identity['source_sha256'][str(path.relative_to(ROOT))] for path in sources):
                    report['error']='Verifier/decoder source changed during verification'
                else:report['result']='PASS';code=0
            report['bytes_saved']=identity['before']['bytes']-identity['after']['bytes']
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return code


if __name__=='__main__':raise SystemExit(main())
