"""Render owned ARWs once and compare frozen V1 with experimental V2.

Statistics describe these scenes only; they do not measure Ricoh fidelity.
"""
import argparse
import html
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from s2g.appearance import to_oklab
from s2g.color import srgb_profile
from s2g.edits import Edits
from s2g.pipeline import engine_identity, render_pixels, resize_camera
from s2g.raw import decode, sha256


def signature(rgb):
    encoded=rgb.astype(np.float32)/255
    linear=np.where(encoded<=.04045,encoded/12.92,((encoded+.055)/1.055)**2.4)
    lab=to_oklab(linear)
    return {"oklab_L_percentiles": np.percentile(lab[...,0],[2,50,99.5]).tolist(),
            "mean_oklab_chroma": float(np.hypot(lab[...,1],lab[...,2]).mean()),
            "all_channels_black_percent": float(np.all(rgb==0,axis=-1).mean()*100),
            "all_channels_white_percent": float(np.all(rgb==255,axis=-1).mean()*100)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources",nargs="+",type=Path)
    parser.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1]/"output"/"color-v03")
    args=parser.parse_args()
    root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):
        parser.error("Output folder must be empty; choose a new --output folder.")
    if len({source.stem.casefold() for source in args.sources}) != len(args.sources):
        parser.error("Source filenames must have distinct stems.")
    report={"calibrated":False,"notice":"Scene statistics only, not a GR fit or fidelity score.","sources":[]}
    sections=[]
    for source in args.sources:
        camera,meta=decode(source.resolve(),sensor=True)
        proxy=resize_camera(camera,1600)
        records={}
        stem=source.stem
        for label,look in (("neutral","neutral_v1"),("positive-v1","gr_positive_experimental_v1"),
                           ("positive-v2","gr_positive_experimental_v2"),("negative-v1","gr_negative_experimental_v1"),
                           ("negative-v2","gr_negative_experimental_v2")):
            edit=Edits(look=look)
            rgb=render_pixels(proxy,meta,edit)
            filename=f"{stem}-{label}.jpg"
            Image.fromarray(rgb).save(root/filename,quality=95,subsampling=0,icc_profile=srgb_profile())
            records[label]={"file":filename,"sha256":sha256(root/filename),"engine":engine_identity(edit),
                            "edits":edit.to_dict(),"signature":signature(rgb)}
        if sha256(source)!=meta["sha256"]:
            raise RuntimeError("Source changed during comparison")
        report["sources"].append({"source":str(source.resolve()),"sha256":meta["sha256"],"renders":records})
        rows=[]
        for kind,title in (("positive","正片"),("negative","负片")):
            images="".join(f'<figure><figcaption>{title} {version.upper()}</figcaption><img loading="lazy" src="/comparison/{html.escape(stem)}-{kind}-{version}.jpg" alt="{title} {version.upper()}"></figure>' for version in ("v1","v2"))
            rows.append(f'<h3>{title} · 强度 82%</h3><div class="pair">{images}</div>')
        sections.append(f'<section><h2>{html.escape(source.name)}</h2><a href="/comparison/{html.escape(stem)}-neutral.jpg" target="_blank">查看中性基线</a>{"".join(rows)}</section>')
    (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>S2G V1 / V2 色彩对比</title><style>body{margin:0;padding:30px;background:#181b1d;color:#e4e6e3;font:16px/1.6 system-ui}main{max-width:1600px;margin:auto}h1{font-size:28px}p{color:#b4bab3}a{color:#bbcda9}section{margin:36px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0}figcaption{padding:8px 0;color:#c5cec1}img{width:100%;display:block;cursor:zoom-in}.zoom img{cursor:zoom-out}body.zoom .pair{grid-template-columns:1fr}body.zoom{max-width:none}@media(max-width:700px){.pair{grid-template-columns:1fr}body{padding:16px}}</style><main><a href="/">← 返回编辑器</a><h1>同一张 RAW，比较两代色彩</h1><p>左侧为 V1，右侧为 V2；相同拍摄白平衡、曝光调整为 0、无颗粒。点击照片放大。V2 仍为实验风格，尚无同场景 GR 数据校准。</p>'''+"".join(sections)+'''<p><a href="/comparison/report.json">下载测量记录</a> · 统计反映照片内容，不能用作 GR 相似度评分。</p></main><script>document.querySelectorAll('img').forEach(img=>img.onclick=()=>document.body.classList.toggle('zoom'));</script></html>'''
    # The editor's CSP requires external styles/scripts, including local QA pages.
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    page = page.replace("<style>"+style+"</style>", '<link rel="stylesheet" href="/comparison/style.css">')
    page = page.replace("<script>"+script+"</script>", '<script src="/comparison/app.js"></script>')
    (root/"style.css").write_text(style,encoding="utf-8")
    (root/"app.js").write_text(script,encoding="utf-8")
    (root/"index.html").write_text(page,encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
