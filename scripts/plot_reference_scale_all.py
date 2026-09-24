#!/usr/bin/env python3
"""Render the aligned 03/05 summaries in the Nature-style reference layout."""
from __future__ import annotations
import argparse, csv, json, os
from pathlib import Path
os.environ.setdefault("MPLCONFIGDIR", "/tmp/qg-paper-matplotlib")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

ROOT=Path(__file__).resolve().parents[1]
plt.rcParams.update({"font.family":"sans-serif","font.sans-serif":["Arial","DejaVu Sans"],"svg.fonttype":"none","pdf.fonttype":42,"font.size":8,"axes.labelsize":8.5,"xtick.labelsize":7.5,"ytick.labelsize":7.5,"legend.fontsize":8,"axes.spines.top":False,"axes.spines.right":False})

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("dataset"); ap.add_argument("experiment",choices=["03_disk_system","05_memory_budget"]); args=ap.parse_args()
    table=ROOT/"results"/args.experiment/args.dataset/"tables/recall_qps_summary.csv"; out=table.parent.parent/"figures"; out.mkdir(parents=True,exist_ok=True)
    rows=list(csv.DictReader(table.open()))
    if args.experiment.startswith("03"):
        specs=[("DiskANN-PQ-Disk","DiskANN-PQ","#6B6B6B","o",":"),("Glass-NSG-DiskPort","Glass-NSG","#C86A2D","s","-"),("Ours-Disk","Ours","#3A9688","D","-"),("SymphonyQG-DiskPort","SymphonyQG","#1976B8","^","-")]
        groups=[(m,l,c,k,ls) for m,l,c,k,ls in specs]; title=f"{args.dataset.upper()}: Disk Search Comparison"; stem="system_qps_recall"; subtitle="800 test queries per width; 4 GiB budget; beam=1; 32 workers; one run"
        key=lambda r:r["method"]; expected=9
    else:
        colors=["#6B6B6B","#C86A2D","#3A9688","#1976B8"]; groups=[]
        for i,b in enumerate(["1","2","4","8"]): groups.append((b,f"{b} GiB",colors[i],"o","-"))
        title=f"{args.dataset.upper()}: Memory Budget Comparison"; stem="memory_qps_recall"; subtitle="9000 test queries per width; Ours-Disk; beam=1; 32 workers; one run"; key=lambda r:str(r.get("search_dram_budget_gib",r.get("budget_gib"))).split(".")[0]; expected=9
    fig,ax=plt.subplots(figsize=(7.2,4.91),dpi=300)
    for ident,label,color,marker,ls in groups:
        curve=sorted([r for r in rows if key(r)==ident],key=lambda r:int(r["search_width"]))
        if len(curve)!=expected: raise SystemExit(f"{ident}: expected {expected}, got {len(curve)}")
        ax.plot([float(r["recall"]) for r in curve],[float(r["qps"]) for r in curve],color=color,marker=marker,linestyle=ls,linewidth=1.9,markersize=4.5,label=label)
    maxq=max(float(r["qps"]) for r in rows); ymax=max(100,maxq*1.08); ax.set_xlim(min(float(r["recall"]) for r in rows)-.02,1.0); ax.set_ylim(0,ymax); ax.xaxis.set_major_locator(MultipleLocator(.05)); ax.yaxis.set_major_locator(MultipleLocator(max(100,round(ymax/4/100)*100))); ax.grid(axis="y",color="#D9DDE0",linewidth=.8); ax.set_xlabel("Recall@10"); ax.set_ylabel("QPS")
    fig.suptitle(title,fontsize=15,fontweight="bold",y=.985); fig.text(.5,.915,subtitle,ha="center",fontsize=9); ax.legend(loc="upper center",bbox_to_anchor=(.5,1.10),ncol=2,frameon=False,handlelength=2.1,columnspacing=2.2); fig.text(.5,.018,"Measured widths 10–580; no smoothing, interpolation or Pareto filtering.\nDiagnostic: external parity incomplete by user request; formal_ready=false.",ha="center",fontsize=6.8); fig.subplots_adjust(left=.13,right=.96,bottom=.16,top=.70)
    stemp=out/stem
    fig.savefig(stemp.with_suffix('.png'),dpi=300); fig.savefig(stemp.with_suffix('.svg'),bbox_inches='tight'); fig.savefig(stemp.with_suffix('.pdf'),bbox_inches='tight'); fig.savefig(stemp.with_suffix('.tiff'),dpi=600,bbox_inches='tight'); plt.close(fig)
    (out/(stem+'.scale.json')).write_text(json.dumps({'dataset':args.dataset,'experiment':args.experiment,'rows':len(rows),'formal_ready':False,'beam':1},indent=2)+'\n')
if __name__=='__main__': main()
