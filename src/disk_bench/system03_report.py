"""Aggregate only independently admitted fixed-03 source runs."""
from pathlib import Path
import json
from .system03 import METHODS, PROTOCOL, check_rows
from .native_contract import ContractError, atomic_write_csv, atomic_write_json, sha256_file
from .layout import run_metadata_root


def publish(queue_root, jobs, config, results_root):
    from .plot_05_disk_suite import _load_complete, _validate_run_manifests, COLORS, DISPLAY_LABELS
    import matplotlib.pyplot as plt
    output=Path(queue_root)/'report';output.mkdir(exist_ok=True)
    manifests=[]
    for dataset in config['datasets']:
        group=[j for j in jobs if j['dataset']==dataset]
        missing={j['method']:j['reason'] for j in group if j['status']=='unsupported_layout'}
        if dataset in ('gist','bigann10m') and missing:
            raise ContractError('primary dataset cannot omit a method')
        included=[j for j in group if j['method'] not in missing]
        if any(j['status']!='admitted_source_run' for j in included):
            raise ContractError('baseline pending or failed; refusing incomplete plot')
        rows=[];sources=[];hardware=[]
        for job in included:
            root=run_metadata_root(Path(results_root),job['run_id'])
            _validate_run_manifests(root)
            protocol=json.loads((root/'protocol.json').read_text())
            if protocol.get('ports_registry_sha256')!=config['ports_sha256'] or protocol.get('disk_root')!=config['disk_root']:
                raise ContractError('source registry or SSD differs from frozen queue')
            hardware.append({k:protocol.get(k) for k in ('disk_root','cpu_affinity','numa_node')})
            part=_load_complete(root,'05c',dataset)
            check_rows(part,(job['method'],))
            rows.extend(part)
            sources.append(dict(run_id=job['run_id'],protocol_sha256=sha256_file(root/'protocol.json')))
        if any(h!=hardware[0] for h in hardware): raise ContractError('source hardware differs')
        for field in ('input_manifest_sha256','query_split_sha256','query_order_sha256'):
            if len({r[field] for r in rows})!=1: raise ContractError('source input identity differs: '+field)
        methods=tuple(m for m in METHODS if m not in missing)
        check_rows(rows,methods)
        csv=output/(dataset+'_points.csv');atomic_write_csv(csv,rows)
        matched=[]
        for target in (.9,.95,.99):
            for method in methods:
                candidates=[r for r in rows if r['method']==method and float(r['recall'])>=target]
                best=max(candidates,key=lambda r:float(r['qps'])) if candidates else None
                matched.append(dict(method=method,target_recall=target,status='measured' if best else 'unreached',
                    actual_recall=best['recall'] if best else None,qps=best['qps'] if best else None,
                    search_width=best['search_width'] if best else None,selection='fastest measured point meeting target; no interpolation'))
        import csv as csv_module
        target=output/(dataset+'_matched_recall.csv')
        with target.with_suffix('.tmp').open('w') as stream:
            writer=csv_module.DictWriter(stream,fieldnames=list(matched[0]))
            writer.writeheader();writer.writerows(matched)
        target.with_suffix('.tmp').replace(target)
        fig,ax=plt.subplots(figsize=(5,3.5))
        for method in methods:
            points=sorted((r for r in rows if r['method']==method),key=lambda r:float(r['recall']))
            label=DISPLAY_LABELS[method]
            version=config.get('method_versions',{}).get(method,'')
            if version: label+=' '+version[:8]
            ax.plot([float(r['recall']) for r in points],[float(r['qps']) for r in points],
                    marker='o',markersize=3,label=label,color=COLORS[method])
        ax.set(xlabel='Recall@10',ylabel='QPS (32 workers)',title=dataset+' | 4 GiB RSS | beam=4 | single run')
        ax.set_yscale('log');ax.legend(fontsize=7);fig.tight_layout()
        for ext in ('svg','pdf'):fig.savefig(output/(dataset+'_recall_qps.'+ext))
        plt.close(fig)
        manifest=dict(system03_protocol=PROTOCOL,dataset=dataset,methods=list(methods),unsupported=missing,
            rows=len(rows),sources=sources,csv_sha256=sha256_file(csv),statistic='single_run',
            conclusions='fixed beam/configuration/hardware; no claim of optimality or cross-run stability')
        atomic_write_json(output/(dataset+'_manifest.json'),manifest);manifests.append(manifest)
    atomic_write_json(output/'manifest.json',dict(system03_protocol=PROTOCOL,datasets=manifests))
    return output
