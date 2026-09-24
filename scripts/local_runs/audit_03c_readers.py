"""Replay recorded native commands with four queries in a separate audit directory."""
import hashlib
import json
import struct
import subprocess
from pathlib import Path
from audit_03c_all import ROOT, METHODS, digest

OUT = ROOT/'artifacts/graphs/reader_audit'

def subset(source, target):
    with source.open('rb') as f, target.open('wb') as out:
        for _ in range(4):
            h=f.read(4)
            if len(h)!=4: raise ValueError('fewer than four queries')
            n=struct.unpack('<I',h)[0]
            b=f.read(n*4)
            if len(b)!=n*4: raise ValueError('truncated vectors')
            out.write(h+b)

def main():
    reports=[]
    for ds in ['agnews','gist','dbpedia','sift10m']:
        for method in METHODS:
            row=dict(dataset=ds,method=method)
            dest=OUT/ds/method
            dest.mkdir(parents=True,exist_ok=True)
            try:
                if (dest/'result.json').exists():
                    row['status']='reader_smoke_completed_previous_pass'
                    reports.append(row)
                    continue
                logs=list((ROOT/'results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs').glob(f'*/05C_disk_system_fair/{ds}/artifacts/export/{method}*.terminal.log'))
                logs=sorted(logs,key=lambda p:p.stat().st_mtime,reverse=True)
                chosen=next(p for p in logs if p.with_name(p.name.replace('.terminal.log','.json')).exists())
                line=next(l for l in chosen.read_text().splitlines() if l.startswith('argv='))
                argv=json.loads(line[5:]); args=dict(zip(argv[1::2],argv[2::2]))
                index=Path(args['--disk-index-dir'])
                if not index.exists(): raise ValueError('recorded index path missing')
                if method == 'Ours-Disk':
                    graph=ROOT/'artifacts/graphs'/ds/'Ours'/(ds+'_Ours_R64_Lbuild400.graph.bin')
                    args['--ours-graph']=str(graph)
                    args['--ours-graph-sha256']=digest(graph)
                if method in ['Glass-NSG-DiskPort','SymphonyQG-DiskPort']:
                    meta=dict(l.split('=',1) for l in (index/'index.meta').read_text().splitlines() if '=' in l)
                    for field,flag in [('implementation_fingerprint','--implementation-fingerprint'),('input_manifest_sha256','--input-manifest-sha256')]:
                        if meta.get(field)!=args.get(flag): raise ValueError('reuse guard mismatch; refusing auto-export')
                subset(ROOT/'data'/ds/(ds+'_query.fvecs'),dest/'query.fvecs')
                subset(ROOT/'data'/ds/(ds+'_groundtruth.ivecs'),dest/'gt.ivecs')
                (dest/'order.u32').write_bytes(struct.pack('<4I',0,1,2,3))
                args.update({'--phase':'validation','--query':str(dest/'query.fvecs'),'--groundtruth':str(dest/'gt.ivecs'),'--query-order':str(dest/'order.u32'),'--query-order-sha256':digest(dest/'order.u32'),'--result-json':str(dest/'result.json'),'--query-trace':str(dest/'queries.jsonl'),'--workers':'1','--warmup-queries':'0','--integration-widths':'64','--run-id':'reader_audit_only','--native-binary-sha256':digest(Path(argv[0]))})
                command=[argv[0]]+[v for pair in args.items() for v in pair]
                command[command.index('--run-id')+1]='native_integration_reader_audit'
                if method in ['Ours-Disk','DiskANN-PQ-Disk']:
                    command += ['--integration-beams','1']
                (dest/'command.json').write_text(json.dumps(command,indent=2))
                with (dest/'terminal.log').open('w') as log:
                    result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=180)
                row['exit_code']=result.returncode
                row['status']='reader_smoke_completed' if result.returncode==0 and (dest/'result.json').exists() else 'failed'
            except Exception as exc:
                row.update(status='pending_or_failed',error=str(exc))
            reports.append(row)
            (OUT/'report.json').write_text(json.dumps(reports,indent=2))
            print(row,flush=True)

if __name__=='__main__': main()
