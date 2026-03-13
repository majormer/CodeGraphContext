#!/usr/bin/env python3
"""Generate a 3D CodeCity visualization from a CodeGraphContext KùzuDB database.

Reads the graph database produced by `cgc index` and generates a standalone
interactive HTML file using Three.js.  Each source file becomes a 3D building:

  - Skyscrapers  (many functions)  → tall, narrow, purple glow
  - Warehouses   (many lines of code) → wide, flat, amber tones
  - Houses       (small files)     → tiny cubes, green/teal

Header files (.h/.hpp) get a translucent glass + wireframe overlay.
Cross-file CALLS edges are shown as colored arcs with animated data pulses
whose speed scales with the number of calls between the two files.

Usage:
    python codecity.py [REPO_ROOT] [--db PATH] [--name NAME] [--no-open]

Examples:
    python codecity.py                          # visualize everything in the DB
    python codecity.py /path/to/my/src           # relativize paths under src/
    python codecity.py /path/to/src --name myapp # output: codecity_myapp.html
"""
import argparse, json, math, os, sys, webbrowser
from pathlib import Path
from collections import defaultdict
import kuzu


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate a 3D CodeCity visualization from a CodeGraphContext database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Requires: pip install kuzu\nOutput: standalone HTML with Three.js (no server needed).")
    p.add_argument("repo_root", nargs="?", default="",
                   help="Optional repo root path. File paths will be shown relative to this.")
    p.add_argument("--db", default=os.environ.get(
        "KUZUDB_PATH", str(Path.home() / ".codegraphcontext" / "kuzudb")),
        help="Path to KùzuDB database (default: ~/.codegraphcontext/kuzudb or $KUZUDB_PATH)")
    p.add_argument("--name", default="",
                   help="Project name for the output file and title (default: auto-detect from repo root)")
    p.add_argument("--no-open", action="store_true",
                   help="Don't open the HTML file in the browser after generation")
    return p.parse_args()

def qdb(conn, q):
    r = conn.execute(q)
    rows = []
    while r.has_next(): rows.append(r.get_next())
    return rows

def main():
    args = parse_args()
    DB_PATH = args.db
    REPO_ROOT = args.repo_root
    proj_name = args.name or (Path(REPO_ROOT).name if REPO_ROOT else "project")

    db = kuzu.Database(DB_PATH, read_only=True)
    conn = kuzu.Connection(db)
    print("Querying graph...")
    files_raw = qdb(conn, "MATCH (f:File) RETURN f.name, f.path")
    funcs_raw = qdb(conn, "MATCH (f:File)-[:CONTAINS]->(fn:Function) RETURN f.path, fn.name, fn.line_number, fn.end_line")
    classes_raw = qdb(conn, "MATCH (f:File)-[:CONTAINS]->(c:Class) RETURN f.path, c.name")
    calls_raw = qdb(conn, "MATCH (a:Function)-[:CALLS]->(b:Function) WHERE a.path<>b.path RETURN a.path, b.path LIMIT 3000")

    ff = defaultdict(list); fml = defaultdict(int)
    for r in funcs_raw:
        ff[r[0]].append(r[1])
        if r[3] and int(r[3]) > fml[r[0]]: fml[r[0]] = int(r[3])
    fc = defaultdict(list)
    for r in classes_raw: fc[r[0]].append(r[1])

    ext_map = {"cpp":"C++","h":"C++ Header","c":"C","cs":"C#","py":"Python"}
    dir_files = defaultdict(list); fi = {}
    for r in files_raw:
        name, path = r[0], r[1]
        if not path: continue
        p = Path(path); ext = p.suffix.lstrip(".").lower()
        lang = ext_map.get(ext, ext); hdr = ext in ("h","hpp","hxx")
        rel = str(p)
        if args.repo_root:
            try: rel = str(p.relative_to(args.repo_root))
            except: pass
        par = str(Path(rel).parent).replace("\\","/")
        fnc = len(ff.get(path,[])); clc = len(fc.get(path,[]))
        ml = fml.get(path, 0); lc = max(ml, 30 if fnc==0 else ml)
        sym = fnc + clc
        rfp = math.pow(max(lc,10)/50.0, 0.45)*3.0; fp = max(2.0, min(rfp, 18.0))
        rh = math.pow(max(sym,1), 0.65)*3.0; h = max(1.5, min(rh, 70.0))
        if sym >= 8 and h > fp*1.2: arch="skyscraper"; fp*=0.8; h*=1.15
        elif lc >= 150 and (sym < 6 or fp > h*0.7): arch="warehouse"; fp*=1.3; h=max(h*0.7,2.0)
        else: arch="house"
        fi[path] = dict(name=name,rel=rel.replace("\\","/"),dir=par,funcs=fnc,classes=clc,
            lines=lc,lang=lang,hdr=hdr,h=round(min(h,70),2),fp=round(min(fp,20),2),arch=arch)
        dir_files[par].append(path)

    GAP=2.0; DPAD=30; dl=sorted(dir_files.keys()); cols=max(1,int(math.ceil(math.sqrt(len(dl)))))
    districts=[]; fpos={}; dx=dz=mrd=0.0
    for idx, dn in enumerate(dl):
        paths=dir_files[dn]; paths.sort(key=lambda p:-fi[p]["fp"]); n=len(paths)
        bc=max(1,int(math.ceil(math.sqrt(n)))); mfp=max(fi[p]["fp"] for p in paths)
        cell=mfp+GAP; br=max(1,int(math.ceil(n/bc))); w=bc*cell+4; d=br*cell+4
        districts.append(dict(name=dn,x=round(dx,2),z=round(dz,2),w=round(w,2),d=round(d,2),n=len(paths)))
        for i, fp in enumerate(paths):
            r,c=i//bc,i%bc; bx=dx+2+c*cell+(cell-fi[fp]["fp"])/2; bz=dz+2+r*cell+(cell-fi[fp]["fp"])/2
            fpos[fp]=(round(bx,2),round(bz,2))
        mrd=max(mrd,d)
        if (idx+1)%cols==0: dx=0; dz+=mrd+DPAD; mrd=0
        else: dx+=w+DPAD

    blds=[]; 
    for fp,pos in fpos.items():
        i=fi[fp]; blds.append(dict(x=pos[0],z=pos[1],h=i["h"],fp=i["fp"],name=i["name"],
            dir=i["dir"],funcs=i["funcs"],classes=i["classes"],lines=i["lines"],
            lang=i["lang"],hdr=i["hdr"],arch=i["arch"]))
    ces=defaultdict(int)
    for r in calls_raw:
        s,d=r[0],r[1]
        if s in fi and d in fi and s!=d: ces[(s,d)]+=1
    edges=[]
    for (s,d),cnt in ces.items():
        if s in fpos and d in fpos:
            sx,sz=fpos[s]; dx2,dz2=fpos[d]
            edges.append(dict(sx=sx,sz=sz,sh=fi[s]["h"],sfp=fi[s]["fp"],
                dx=dx2,dz=dz2,dh=fi[d]["h"],dfp=fi[d]["fp"],cnt=cnt,
                sa=fi[s]["arch"],sd=fi[s]["dir"]))
    ac=defaultdict(int); hc=0
    for b in blds: ac[b["arch"]]+=1; hc+=(1 if b["hdr"] else 0)
    st=dict(files=len(fi),functions=sum(f["funcs"] for f in fi.values()),
        classes=sum(f["classes"] for f in fi.values()),edges=len(edges),
        dirs=len(districts),sky=ac["skyscraper"],ware=ac["warehouse"],house=ac["house"],hdrs=hc)
    print(f"  {st['files']} files, {st['functions']} funcs, {st['edges']} call edges")
    print(f"  {st['sky']} skyscrapers, {st['ware']} warehouses, {st['house']} houses, {st['hdrs']} headers")
    data=dict(districts=districts,buildings=blds,edges=edges,stats=st)
    html=gen_html(data)
    slug = proj_name.lower().replace(" ","_").replace("/","_")[:40]
    out=Path.home()/".codegraphcontext"/"visualizations"/f"codecity_{slug}.html"
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(html,encoding="utf-8")
    print(f"Saved: {out}")
    if not args.no_open: webbrowser.open(str(out))

def gen_html(data):
    jd=json.dumps(data,separators=(",",":"))
    s=data["stats"]
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>CodeCity | {s["files"]} files</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#050810;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden;height:100vh}}
#hud{{position:fixed;top:14px;left:14px;z-index:100;background:rgba(8,12,24,0.88);backdrop-filter:blur(14px);padding:14px 20px;border-radius:14px;border:1px solid rgba(129,140,248,0.15);max-width:320px}}
#hud h1{{font-size:1.05rem;font-weight:800;background:linear-gradient(135deg,#c084fc,#38bdf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
#hud .sub{{font-size:0.68rem;color:#64748b;margin-top:2px}}
.row{{display:flex;gap:14px;margin-top:8px;font-size:0.72rem;color:#94a3b8}}.row span{{color:#818cf8;font-weight:700}}
#legend{{position:fixed;bottom:14px;left:14px;z-index:100;background:rgba(8,12,24,0.88);backdrop-filter:blur(14px);padding:12px 16px;border-radius:12px;border:1px solid rgba(129,140,248,0.12);font-size:0.7rem}}
#legend h3{{font-size:0.75rem;color:#818cf8;margin-bottom:6px}}
.lg{{display:flex;align-items:center;gap:8px;margin:4px 0;color:#94a3b8}}
.ls{{width:14px;height:14px;border-radius:3px;flex-shrink:0}}
#tt{{position:fixed;display:none;z-index:200;background:rgba(8,12,24,0.95);backdrop-filter:blur(14px);padding:14px 18px;border-radius:12px;border:1px solid rgba(129,140,248,0.25);font-size:0.78rem;pointer-events:none;max-width:340px}}
.tn{{font-weight:700;font-size:0.92rem;color:#fff}}.td{{color:#64748b;font-size:0.68rem;margin-top:2px}}
.tb{{display:inline-block;font-size:0.6rem;padding:1px 7px;border-radius:6px;margin-top:4px;font-weight:600;text-transform:uppercase}}
.ts{{margin-top:8px;color:#94a3b8;line-height:1.6}}.ts span{{color:#818cf8;font-weight:600}}
#ctrls{{position:fixed;bottom:14px;right:14px;z-index:100;background:rgba(8,12,24,0.8);padding:10px 14px;border-radius:10px;font-size:0.63rem;color:#475569}}
#flt{{position:fixed;top:14px;right:14px;z-index:100;background:rgba(8,12,24,0.88);backdrop-filter:blur(14px);padding:14px 18px;border-radius:14px;border:1px solid rgba(129,140,248,0.12);font-size:0.72rem}}
#flt label{{display:block;margin:3px 0;color:#94a3b8;cursor:pointer}}
#flt input[type=checkbox]{{margin-right:6px;accent-color:#818cf8}}
.sr{{display:flex;align-items:center;gap:8px;margin-top:8px}}
#flt input[type=range]{{width:100px;accent-color:#818cf8}}
canvas{{display:block}}
</style></head><body>
<div id="hud"><h1>CODECITY</h1><div class="sub">3D Codebase Visualization</div>
<div class="row"><div><span>{s["files"]}</span> files</div><div><span>{s["functions"]}</span> funcs</div><div><span>{s["classes"]}</span> classes</div><div><span>{s["edges"]}</span> calls</div></div></div>
<div id="legend"><h3>BUILDING TYPES</h3>
<div class="lg"><div class="ls" style="background:linear-gradient(180deg,#c084fc,#7c3aed)"></div>Skyscraper &mdash; many functions ({s["sky"]})</div>
<div class="lg"><div class="ls" style="background:linear-gradient(180deg,#f59e0b,#b45309)"></div>Warehouse &mdash; many LOC ({s["ware"]})</div>
<div class="lg"><div class="ls" style="background:linear-gradient(180deg,#34d399,#059669)"></div>House &mdash; small file ({s["house"]})</div>
<div class="lg"><div class="ls" style="background:rgba(56,189,248,0.3);border:1.5px solid #38bdf8"></div>Glass = .h header ({s["hdrs"]})</div></div>
<div id="tt"><div class="tn"></div><div class="td"></div><div class="tb"></div><div class="ts"></div></div>
<div id="flt">
<label><input type="checkbox" id="chkE" checked> Call edges</label>
<label><input type="checkbox" id="chkL"> File labels</label>
<label><input type="checkbox" id="chkD" checked> District plates</label>
<div class="sr"><span style="color:#64748b">Height</span><input type="range" id="hs" min="0.3" max="3" step="0.1" value="1"><span id="hsV" style="color:#818cf8;min-width:28px">1x</span></div></div>
<div id="ctrls">Drag: rotate &bull; Scroll: zoom &bull; Right-drag: pan &bull; Hover: inspect</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/shaders/CopyShader.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/shaders/LuminosityHighPassShader.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/EffectComposer.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/RenderPass.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/ShaderPass.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/UnrealBloomPass.js"></script>
<script>
const D={jd};
let HS=1.0;
const scene=new THREE.Scene();
scene.background=new THREE.Color(0x050810);
scene.fog=new THREE.FogExp2(0x050810,0.0005);
const cam=new THREE.PerspectiveCamera(55,innerWidth/innerHeight,0.5,6000);
const R=new THREE.WebGLRenderer({{antialias:true}});
R.setSize(innerWidth,innerHeight);R.setPixelRatio(Math.min(devicePixelRatio,2));
R.shadowMap.enabled=true;R.shadowMap.type=THREE.PCFSoftShadowMap;
R.toneMapping=THREE.ACESFilmicToneMapping;R.toneMappingExposure=1.1;
document.body.appendChild(R.domElement);
const ctl=new THREE.OrbitControls(cam,R.domElement);
ctl.enableDamping=true;ctl.dampingFactor=0.07;ctl.maxPolarAngle=Math.PI*0.47;
ctl.minDistance=8;ctl.maxDistance=2500;
let comp=null;
try{{if(THREE.EffectComposer){{comp=new THREE.EffectComposer(R);
comp.addPass(new THREE.RenderPass(scene,cam));
comp.addPass(new THREE.UnrealBloomPass(new THREE.Vector2(innerWidth,innerHeight),0.65,0.3,0.82))}}}}catch(ex){{comp=null;console.warn('Bloom unavailable:',ex)}}
scene.add(new THREE.AmbientLight(0x1a1a3e,0.5));
const dl=new THREE.DirectionalLight(0xeeeeff,0.7);
dl.position.set(300,500,200);dl.castShadow=true;
dl.shadow.mapSize.set(2048,2048);dl.shadow.camera.far=2000;
dl.shadow.camera.left=dl.shadow.camera.bottom=-800;
dl.shadow.camera.right=dl.shadow.camera.top=800;
scene.add(dl);scene.add(new THREE.HemisphereLight(0x6366f1,0x0f172a,0.25));
const pl=new THREE.PointLight(0xc084fc,0.3,800);pl.position.set(-200,150,-200);scene.add(pl);
!function(){{const N=4000,p=new Float32Array(N*3);
for(let i=0;i<N;i++){{p[i*3]=(Math.random()-.5)*5000;p[i*3+1]=200+Math.random()*2000;p[i*3+2]=(Math.random()-.5)*5000}}
const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(p,3));
scene.add(new THREE.Points(g,new THREE.PointsMaterial({{color:0xc8d0e0,size:1.2,transparent:true,opacity:0.7,sizeAttenuation:true}})))}}();
const gnd=new THREE.Mesh(new THREE.PlaneGeometry(4000,4000),new THREE.MeshStandardMaterial({{color:0x080c18,roughness:0.95,metalness:0.1}}));
gnd.rotation.x=-Math.PI/2;gnd.position.y=-0.05;gnd.receiveShadow=true;scene.add(gnd);
scene.add(new THREE.GridHelper(4000,200,0x111830,0x0c1020));
const SC=[0xc084fc,0xa78bfa,0x818cf8,0x7c3aed,0x8b5cf6];
const WC=[0xf59e0b,0xd97706,0xb45309,0xe67e22,0xfbbf24];
const HC=[0x34d399,0x10b981,0x059669,0x22d3ee,0x2dd4bf];
function hd(s){{let h=0;for(let i=0;i<s.length;i++)h=((h<<5)-h+s.charCodeAt(i))|0;return Math.abs(h)}}
function pC(a,d){{const i=hd(d);if(a==='skyscraper')return SC[i%SC.length];if(a==='warehouse')return WC[i%WC.length];return HC[i%HC.length]}}
const dG=new THREE.Group();scene.add(dG);
D.districts.forEach(d=>{{
const geo=new THREE.BoxGeometry(d.w,0.25,d.d);
const mat=new THREE.MeshStandardMaterial({{color:0x0f172a,roughness:0.8,metalness:0.3,transparent:true,opacity:0.5}});
const m=new THREE.Mesh(geo,mat);m.position.set(d.x+d.w/2,0.12,d.z+d.d/2);m.receiveShadow=true;dG.add(m);
const pts=[new THREE.Vector3(d.x,0.3,d.z),new THREE.Vector3(d.x+d.w,0.3,d.z),new THREE.Vector3(d.x+d.w,0.3,d.z+d.d),new THREE.Vector3(d.x,0.3,d.z+d.d),new THREE.Vector3(d.x,0.3,d.z)];
dG.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({{color:0x334155,transparent:true,opacity:0.4}})));
const cv=document.createElement('canvas');cv.width=512;cv.height=48;const cx=cv.getContext('2d');
cx.fillStyle='#475569';cx.font='bold 20px sans-serif';
cx.fillText((d.name.split('/').pop()||d.name)+' ('+d.n+')',6,22);
const tx=new THREE.CanvasTexture(cv);const sp=new THREE.Sprite(new THREE.SpriteMaterial({{map:tx,transparent:true,opacity:0.5}}));
sp.position.set(d.x+d.w/2,0.8,d.z-1.2);sp.scale.set(Math.min(d.w,28),Math.min(d.w,28)/8,1);dG.add(sp)}});
const bM=[],bE=[];
D.buildings.forEach(b=>{{
const h=b.h*HS,fp=b.fp,c=pC(b.arch,b.dir),hdr=b.hdr;
let gw,gd;
if(b.arch==='skyscraper'){{gw=fp*0.85;gd=fp*0.85}}
else if(b.arch==='warehouse'){{gw=fp*1.2;gd=fp*0.9}}
else{{gw=fp*0.8;gd=fp*0.8}}
const geo=new THREE.BoxGeometry(gw,h,gd);
const ei=hdr?(b.arch==='skyscraper'?0.08:0.05):(b.arch==='skyscraper'?0.25:b.arch==='warehouse'?0.12:0.15);
const mat=new THREE.MeshStandardMaterial({{
color:c,emissive:new THREE.Color(c),emissiveIntensity:ei,
roughness:hdr?0.2:b.arch==='warehouse'?0.7:0.4,
metalness:hdr?0.8:b.arch==='warehouse'?0.3:0.6,
transparent:hdr,opacity:hdr?0.33:b.arch==='warehouse'?0.88:0.92}});
const m=new THREE.Mesh(geo,mat);m.position.set(b.x+fp/2,h/2,b.z+fp/2);
m.castShadow=true;m.receiveShadow=true;m.userData=b;scene.add(m);bM.push(m);
if(hdr){{const eg=new THREE.EdgesGeometry(geo);
const wf=new THREE.LineSegments(eg,new THREE.LineBasicMaterial({{color:0x38bdf8,transparent:true,opacity:0.5}}));
wf.position.copy(m.position);scene.add(wf);bE.push({{w:wf,m:m}})}}
if(b.arch==='skyscraper'&&!hdr){{const cg=new THREE.BoxGeometry(fp*0.4,0.4,fp*0.4);
const cm=new THREE.MeshStandardMaterial({{color:0xffffff,emissive:new THREE.Color(c),emissiveIntensity:0.8,roughness:0.1,metalness:0.9}});
const cp=new THREE.Mesh(cg,cm);cp.position.set(b.x+fp/2,h+0.2,b.z+fp/2);scene.add(cp)}}
if(b.arch==='warehouse'&&!hdr){{const sg=new THREE.BoxGeometry(gw*0.3,0.15,gd);
const sm=new THREE.MeshStandardMaterial({{color:0x1e293b,roughness:0.9}});
const sd=new THREE.Mesh(sg,sm);sd.position.set(b.x+fp/2-gw*0.35,h/3,b.z+fp/2);scene.add(sd)}}
}});
const eG=new THREE.Group();scene.add(eG);
let eLns=[],curves=[],pulseSpeeds=[],edgeColors=[];
function buildEdges(){{
while(eG.children.length)eG.remove(eG.children[0]);eLns=[];curves=[];pulseSpeeds=[];edgeColors=[];
D.edges.forEach(e=>{{
const sx=e.sx+e.sfp/2,sz=e.sz+e.sfp/2,dx=e.dx+e.dfp/2,dz=e.dz+e.dfp/2;
const sh=e.sh*HS,dh=e.dh*HS;
const dist=Math.sqrt((dx-sx)**2+(dz-sz)**2);
const arcH=Math.max(sh,dh)+dist*0.12+8;
const curve=new THREE.QuadraticBezierCurve3(
new THREE.Vector3(sx,sh,sz),new THREE.Vector3((sx+dx)/2,arcH,(sz+dz)/2),new THREE.Vector3(dx,dh,dz));
curves.push(curve);
pulseSpeeds.push(0.5*(1+Math.log2(Math.max(e.cnt||1,1))));
const ec=new THREE.Color(pC(e.sa,e.sd));edgeColors.push(ec);
const pts=curve.getPoints(32);
const geo=new THREE.BufferGeometry().setFromPoints(pts);
const mat=new THREE.LineBasicMaterial({{color:ec,transparent:true,opacity:0.32}});
const ln=new THREE.Line(geo,mat);eG.add(ln);eLns.push(mat)}})}}
buildEdges();
const PPC=2;
const pulseCount=curves.length*PPC;
const pulsePos=new Float32Array(pulseCount*3);
const pulseCol=new Float32Array(pulseCount*3);
const pulseGeo=new THREE.BufferGeometry();
pulseGeo.setAttribute('position',new THREE.BufferAttribute(pulsePos,3));
pulseGeo.setAttribute('color',new THREE.BufferAttribute(pulseCol,3));
const pulseMat=new THREE.PointsMaterial({{size:2.0,transparent:true,opacity:0.9,
vertexColors:true,sizeAttenuation:true,blending:THREE.AdditiveBlending,depthWrite:false}});
const pulsePoints=new THREE.Points(pulseGeo,pulseMat);
scene.add(pulsePoints);
const pulseGlowPos=new Float32Array(pulseCount*3);
const pulseGlowCol=new Float32Array(pulseCount*3);
const pulseGlowGeo=new THREE.BufferGeometry();
pulseGlowGeo.setAttribute('position',new THREE.BufferAttribute(pulseGlowPos,3));
pulseGlowGeo.setAttribute('color',new THREE.BufferAttribute(pulseGlowCol,3));
const pulseGlowMat=new THREE.PointsMaterial({{size:5.0,transparent:true,opacity:0.3,
vertexColors:true,sizeAttenuation:true,blending:THREE.AdditiveBlending,depthWrite:false}});
const pulseGlow=new THREE.Points(pulseGlowGeo,pulseGlowMat);
scene.add(pulseGlow);
function syncPulseColors(){{const ca=pulseCol,ga=pulseGlowCol;
for(let i=0;i<edgeColors.length;i++){{const c=edgeColors[i];
const bright=new THREE.Color(c).lerp(new THREE.Color(0xffffff),0.4);
for(let p=0;p<PPC;p++){{const idx=(i*PPC+p)*3;
ca[idx]=bright.r;ca[idx+1]=bright.g;ca[idx+2]=bright.b;
ga[idx]=c.r;ga[idx+1]=c.g;ga[idx+2]=c.b}}}}
pulseGeo.attributes.color.needsUpdate=true;
pulseGlowGeo.attributes.color.needsUpdate=true}}
syncPulseColors();
const bb=new THREE.Box3();bM.forEach(m=>bb.expandByObject(m));
const ctr=new THREE.Vector3(),bs=new THREE.Vector3();bb.getCenter(ctr);bb.getSize(bs);
const mxD=Math.max(bs.x,bs.z);
cam.position.set(ctr.x+mxD*0.45,mxD*0.55,ctr.z+mxD*0.45);ctl.target.copy(ctr);
const rc=new THREE.Raycaster(),ms=new THREE.Vector2();
const tt=document.getElementById('tt');let hv=null,oei=null;
const BS={{skyscraper:'background:#7c3aed;color:#f5f3ff',warehouse:'background:#b45309;color:#fffbeb',house:'background:#059669;color:#ecfdf5'}};
window.addEventListener('mousemove',ev=>{{
ms.x=(ev.clientX/innerWidth)*2-1;ms.y=-(ev.clientY/innerHeight)*2+1;
rc.setFromCamera(ms,cam);const hits=rc.intersectObjects(bM);
if(hits.length){{const m=hits[0].object,b=m.userData;
if(hv!==m){{if(hv&&oei!==null)hv.material.emissiveIntensity=oei;hv=m;oei=m.material.emissiveIntensity;m.material.emissiveIntensity=1.0}}
tt.style.display='block';tt.style.left=(ev.clientX+16)+'px';tt.style.top=(ev.clientY+16)+'px';
tt.querySelector('.tn').textContent=b.name;tt.querySelector('.td').textContent=b.dir;
const badge=tt.querySelector('.tb');badge.textContent=(b.hdr?'header ':'')+b.arch;badge.style.cssText=BS[b.arch];
tt.querySelector('.ts').innerHTML='<span>'+b.funcs+'</span> functions &bull; <span>'+b.classes+'</span> classes<br>~<span>'+b.lines+'</span> lines &bull; '+b.lang
}}else{{if(hv&&oei!==null){{hv.material.emissiveIntensity=oei;hv=null}}tt.style.display='none'}}}});
document.getElementById('chkE').addEventListener('change',e=>{{eG.visible=e.target.checked;pulsePoints.visible=e.target.checked;pulseGlow.visible=e.target.checked}});
document.getElementById('chkD').addEventListener('change',e=>dG.visible=e.target.checked);
const hsl=document.getElementById('hs'),hsv=document.getElementById('hsV');
hsl.addEventListener('input',()=>{{HS=parseFloat(hsl.value);hsv.textContent=HS.toFixed(1)+'x';
bM.forEach(m=>{{const b=m.userData,h=b.h*HS;m.scale.y=HS;m.position.y=h/2}});
bE.forEach(o=>{{o.w.scale.y=HS;o.w.position.y=o.m.position.y}});buildEdges();syncPulseColors()}});
const lS=[];
document.getElementById('chkL').addEventListener('change',e=>{{
if(e.target.checked&&!lS.length){{D.buildings.forEach(b=>{{
const cv=document.createElement('canvas');cv.width=512;cv.height=48;const cx=cv.getContext('2d');
cx.fillStyle=b.hdr?'#38bdf8':'#e2e8f0';cx.font='bold 16px sans-serif';cx.fillText(b.name,4,18);
const tx=new THREE.CanvasTexture(cv);
const sp=new THREE.Sprite(new THREE.SpriteMaterial({{map:tx,transparent:true,opacity:0.65}}));
sp.position.set(b.x+b.fp/2,b.h*HS+2,b.z+b.fp/2);sp.scale.set(8,2,1);scene.add(sp);lS.push(sp)}})}}
lS.forEach(s=>s.visible=e.target.checked)}});
let tick=0,clock=new THREE.Clock();
function animate(){{requestAnimationFrame(animate);ctl.update();
const dt=clock.getDelta(),t=clock.getElapsedTime();
tick+=0.02;
const pp=pulseGeo.attributes.position.array;
const gp=pulseGlowGeo.attributes.position.array;
for(let i=0;i<curves.length;i++){{const spd=pulseSpeeds[i];
for(let p=0;p<PPC;p++){{const idx=(i*PPC+p)*3;
const phase=(t*spd+p/PPC)%1;
const pt=curves[i].getPoint(phase);
pp[idx]=pt.x;pp[idx+1]=pt.y;pp[idx+2]=pt.z;
gp[idx]=pt.x;gp[idx+1]=pt.y;gp[idx+2]=pt.z}}}}
pulseGeo.attributes.position.needsUpdate=true;
pulseGlowGeo.attributes.position.needsUpdate=true;
if(comp)comp.render();else R.render(scene,cam)}}
animate();
window.addEventListener('resize',()=>{{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();
R.setSize(innerWidth,innerHeight);if(comp)comp.setSize(innerWidth,innerHeight)}});
</script></body></html>'''

if __name__=="__main__": main()
