import sys,time,json,ast,shutil,dataclasses,hashlib
from collections.abc import Mapping
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description="Compare candidate processing using identical, pre-generated duarouter XML files.")
parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
parser.add_argument('--baseline', type=Path, required=True, help='Original build_traffic.py (read only)')
parser.add_argument('--raw-dir', type=Path, required=True, help='Directory containing candidate_<vclass>.raw.rou.xml')
parser.add_argument('--output', type=Path, required=True, help='New, isolated benchmark directory')
args=parser.parse_args()
root=args.project.resolve();sys.path.insert(0,str(root))
from simulation.sumo.building import build_traffic as m
from simulation_protocol.artifacts import GeneratedArtifactLayout
out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
layout=GeneratedArtifactLayout(out);src=GeneratedArtifactLayout(root/'data/maps/sumo/generated')
for a,b in [(layout.network_file,src.network_file),(layout.signal_programs_file,src.signal_programs_file)]:
 a.parent.mkdir(parents=True,exist_ok=True)
 if not a.exists():a.symlink_to(b)
oldtree=ast.parse(args.baseline.read_text(encoding='utf-8'));ns={};exec('from __future__ import annotations\n'+ast.unparse(next(n for n in oldtree.body if isinstance(n,ast.FunctionDef) and n.name=='_route_contains')),ns)
old=ns['_route_contains'];new=m._route_contains;original=m._build_candidates
rawdir=args.raw_dir.resolve()
def runner(command,**kwargs):
 import subprocess
 dst=Path(command[command.index('--output-file')+1]);shutil.copy2(rawdir/dst.name,dst)
 return subprocess.CompletedProcess(command,0,'')
def norm(v):
 if isinstance(v,Path):return str(v)
 if dataclasses.is_dataclass(v):return {f.name:norm(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,Mapping):return sorted([(repr(k),norm(x)) for k,x in v.items()])
 if isinstance(v,(tuple,list,set,frozenset)):return [norm(x) for x in v]
 return v
class Done(Exception):pass
def wrapped(*args,**kwargs):
 rows=[];results=[]
 for label,func in [('original',old),('optimized',new)]:
  m._route_contains=func;t=time.perf_counter();result=original(*args[:-1],runner);elapsed=time.perf_counter()-t
  encoded=json.dumps(norm(result),sort_keys=True).encode();results.append(encoded);rows.append({'label':label,'seconds':elapsed,'sha256':hashlib.sha256(encoded).hexdigest()});print(rows[-1],flush=True)
 assert results[0]==results[1]
 (out/'result.json').write_text(json.dumps({'results':rows,'identical':True},indent=2));raise Done
m._build_candidates=wrapped
try:m.build_traffic_scenarios(json.loads(src.tls_manifest.read_text()),demand_path=root/'data/maps/sumo/official/traffic/official_traffic_demands.json',vehicle_profile_path=root/'data/maps/sumo/official/traffic/vehicle_profiles.json',traffic_policy_path=root/'data/maps/sumo/official/traffic/traffic_generation_policy.json',output_dir=out,include_dense_scopes=False)
except Done:pass
