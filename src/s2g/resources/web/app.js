"use strict";
const $ = id => document.getElementById(id);
const defaults = {look:"neutral_v1",strength:.82,exposure_ev:0,temperature:null,tint:0,highlights:0,shadows:0,saturation:0,vibrance:0,split_toning:100,grain:0,seed:42,crop:null,rotation:0};
const state = {assets:[],presets:[],active:null,metadata:null,edits:{...defaults},selected:new Set(),dirty:false,revision:0,previewUrl:null,beforeUrl:null,beforeKey:null,previewAbort:null,job:null,timer:null,toastTimer:null,compare:false};
const token = document.querySelector('meta[name="s2g-token"]').content;
const clone = value => JSON.parse(JSON.stringify(value));
async function api(path, data, signal) {
  const response = await fetch(path, data === undefined ? {signal} : {method:"POST",headers:{"Content-Type":"application/json","X-S2G-Token":token},body:JSON.stringify(data),signal});
  if (!response.ok) {const body=await response.json();throw new Error(body.error || "请求失败");}
  return response;
}
function toast(message, error=false) {clearTimeout(state.toastTimer);$("toast").textContent=message;$("toast").className=error?"error-toast":"";$("toast").hidden=false;state.toastTimer=setTimeout(()=>$("toast").hidden=true,7000);}
function changed() {state.dirty=true;$("dirty").hidden=false;schedulePreview();}
function updateControls() {
  $("look").value=state.edits.look;
  for (const key of ["strength","exposure_ev","temperature","tint","highlights","shadows","saturation","vibrance","split_toning","grain"]) {
    $(key).value=state.edits[key] ?? 5400;
    $(key+"-value").textContent=key==="temperature"?(state.edits.temperature===null?"拍摄时":Math.round(state.edits.temperature)+" K"):key==="exposure_ev"?Number(state.edits[key]).toFixed(2)+" EV":key==="strength"?Math.round(state.edits.strength*100)+"%":key==="split_toning"?Math.round(state.edits[key])+"%":String(state.edits[key]);
  }
  $("strength").disabled=state.edits.look==="neutral_v1";$("split_toning").disabled=state.edits.look==="neutral_v1"||state.edits.strength===0;
  $("look-description").textContent=state.edits.look.includes("positive")?"浓郁中间调，蓝绿分色与冷暖层次；保护肤色和中性色。":state.edits.look.includes("negative")?"柔和反差，克制的黑位抬升与温暖高光；保留暗部色彩。":"中性显影，保留自然色彩与高光层次。";
  $("dirty").hidden=!state.dirty;
}
function renderLibrary() {
  const query=$("search").value.toLowerCase();$("photo-list").replaceChildren();
  for (const asset of state.assets.filter(a=>a.name.toLowerCase().includes(query))) {
    const card=document.createElement("div");card.className="photo-card"+(asset.id===state.active?" active":"");card.tabIndex=0;card.setAttribute("role","button");card.setAttribute("aria-label",asset.name);
    const check=document.createElement("input");check.type="checkbox";check.checked=state.selected.has(asset.id);check.setAttribute("aria-label","勾选 "+asset.name);check.onclick=e=>e.stopPropagation();check.onchange=()=>{check.checked?state.selected.add(asset.id):state.selected.delete(asset.id);updateSelected();};
    const img=document.createElement("img");img.src="/api/thumbnail?id="+asset.id;img.alt="";img.loading="lazy";
    const info=document.createElement("div");info.className="photo-info";const name=document.createElement("strong");name.textContent=asset.name;const type=document.createElement("small");type.textContent="SONY · ARW";info.append(name,type);card.append(check,img,info);
    card.onclick=()=>selectPhoto(asset.id);card.onkeydown=e=>{if(e.key==="Enter")selectPhoto(asset.id);};$("photo-list").append(card);
  }
  $("photo-count").textContent=state.assets.length;updateSelected();
}
function updateSelected() {$("selected-count").textContent=state.selected.size?"已勾选 "+state.selected.size+" 张":"未选择批量照片";$("export").textContent=state.selected.size?"导出勾选照片（"+state.selected.size+"）":"导出照片";}
function renderPresets() {$("presets").replaceChildren(new Option("选择预设…",""));state.presets.forEach((p,i)=>$("presets").add(new Option(p.name,String(i))));}
async function reloadLibrary() {const data=await(await api("/api/library")).json();state.assets=data.assets;state.presets=data.presets;$("look").replaceChildren(...data.looks.sort((a,b)=>(a.id.endsWith("_v1")&&a.id!=="neutral_v1")-(b.id.endsWith("_v1")&&b.id!=="neutral_v1")).map(x=>new Option(x.label+(x.id.includes("experimental_v1")?" · 旧版":""),x.id)));$("import-path").value=data.root+"\\samples";if(!$("export-folder").value)$("export-folder").value=data.root+"\\output\\editor";renderLibrary();renderPresets();if(state.active===null&&state.assets.length)await selectPhoto(state.assets[0].id);}
async function selectPhoto(id) {
  if(state.dirty&&id!==state.active&&!confirm("当前调整尚未保存。切换照片将丢弃未保存调整，是否继续？"))return;
  const revision=++state.revision;state.active=id;state.dirty=false;state.previewAbort?.abort();if(state.beforeUrl)URL.revokeObjectURL(state.beforeUrl);state.beforeUrl=null;state.beforeKey=null;state.compare=false;$("before-label").hidden=true;$("preview").hidden=true;$("preview-loading").hidden=false;$("export").disabled=true;$("compare").disabled=true;renderLibrary();
  try {const data=await(await api("/api/asset?id="+id)).json();if(revision!==state.revision)return;state.metadata=data.metadata;state.edits={...defaults,...data.edits};state.beforeKey=null;$("filename").textContent=data.name;const m=data.metadata;$("photo-meta").textContent=`${m.visible_size[0]} × ${m.visible_size[1]} · ISO ${m.iso_speed} · f/${m.aperture_f_number.toFixed(1)} · ${Math.round(m.focal_length_mm)}mm`;$("empty").hidden=true;$("compare").disabled=false;$("export").disabled=false;$("crop-ratio").value="original";updateControls();await preview();}catch(error){toast(error.message,true);$("preview-loading").hidden=true;}
}
function schedulePreview(){clearTimeout(state.timer);state.timer=setTimeout(preview,180);}
async function preview() {
  if(state.active===null)return;state.previewAbort?.abort();const abort=new AbortController();state.previewAbort=abort;const revision=++state.revision;const id=state.active;const edits=clone(state.edits);$("preview-loading").hidden=false;const started=performance.now();
  try {
    const response=await api("/api/preview",{id,edits},abort.signal);const blob=await response.blob();if(revision!==state.revision)return;const url=URL.createObjectURL(blob);if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);state.previewUrl=url;if(!state.compare){$("preview").src=url;$("preview").hidden=false;}$("render-info").textContent=`预览 ${(performance.now()-started).toFixed(0)} ms · 原片保留 · sRGB`;
    const key=JSON.stringify([id,edits.crop,edits.rotation]);if(state.beforeKey!==key){const beforeEdits={...defaults,crop:edits.crop,rotation:edits.rotation};const beforeBlob=await(await api("/api/preview",{id,edits:beforeEdits},abort.signal)).blob();if(revision!==state.revision)return;if(state.beforeUrl)URL.revokeObjectURL(state.beforeUrl);state.beforeUrl=URL.createObjectURL(beforeBlob);state.beforeKey=key;}
  } catch(error){if(error.name!=="AbortError")toast(error.message,true);}finally{if(revision===state.revision)$("preview-loading").hidden=true;}
}
function compare(down){if(!state.beforeUrl)return;state.compare=down;$("preview").src=down?state.beforeUrl:state.previewUrl;$("before-label").hidden=!down;}
$("compare").addEventListener("pointerdown",e=>{e.preventDefault();$("compare").setPointerCapture(e.pointerId);compare(true);});for(const event of ["pointerup","pointercancel","lostpointercapture"])$("compare").addEventListener(event,()=>compare(false));
window.addEventListener("keydown",e=>{if(e.code==="Space"&&!/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)){e.preventDefault();compare(true);}});window.addEventListener("keyup",e=>{if(e.code==="Space")compare(false);});window.addEventListener("blur",()=>compare(false));
$("preview").onload=()=>{const canvas=$("histogram"),ctx=canvas.getContext("2d");const sample=document.createElement("canvas");sample.width=256;sample.height=128;const sx=sample.getContext("2d");sx.drawImage($("preview"),0,0,256,128);const data=sx.getImageData(0,0,256,128).data;const bins=[new Float32Array(256),new Float32Array(256),new Float32Array(256)];for(let i=0;i<data.length;i+=4)for(let c=0;c<3;c++)bins[c][data[i+c]]++;const max=Math.max(1,...bins.map(b=>Math.max(...b)));ctx.clearRect(0,0,canvas.width,canvas.height);ctx.globalCompositeOperation="screen";["#bb786c","#89a077","#7c94b5"].forEach((color,c)=>{ctx.strokeStyle=color;ctx.beginPath();bins[c].forEach((v,i)=>{const x=i/255*canvas.width,y=canvas.height-v/max*(canvas.height-5);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();});ctx.globalCompositeOperation="source-over";};
for(const key of ["strength","exposure_ev","temperature","tint","highlights","shadows","saturation","vibrance","split_toning","grain"])$(key).oninput=()=>{state.edits[key]=Number($(key).value);updateControls();changed();};
$("look").onchange=()=>{state.edits.look=$("look").value;updateControls();changed();};$("as-shot").onclick=()=>{state.edits.temperature=null;state.edits.tint=0;updateControls();changed();};
$("color-reset").onclick=()=>{state.edits.saturation=0;state.edits.vibrance=0;state.edits.split_toning=100;updateControls();changed();};
$("reset").onclick=()=>{state.edits=clone(defaults);$("crop-ratio").value="original";updateControls();changed();};
$("rotate").onclick=()=>{state.edits.rotation=(state.edits.rotation+90)%360;changed();};
$("crop-ratio").onchange=()=>{if(!state.metadata)return;const ratio=Number($("crop-ratio").value);if(!ratio){state.edits.crop=null;}else{let [w,h]=state.metadata.visible_size;if([5,6].includes(state.metadata.orientation_libraw))[w,h]=[h,w];const current=w/h;const desired=state.edits.rotation%180?1/ratio:ratio;if(current>desired){const cw=desired/current;state.edits.crop=[(1-cw)/2,0,cw,1];}else{const ch=current/desired;state.edits.crop=[0,(1-ch)/2,1,ch];}}changed();};
$("zoom").onclick=()=>$("viewer").classList.toggle("zoomed");$("search").oninput=renderLibrary;$("select-all").onclick=()=>{state.selected=state.selected.size===state.assets.length?new Set():new Set(state.assets.map(a=>a.id));renderLibrary();};
async function save(ids){if(!ids.length){toast("请先选择照片",true);return;}try{const result=await(await api("/api/save",{ids,edits:state.edits})).json();if(result.saved.includes(state.active)){state.dirty=false;$("dirty").hidden=true;}toast(`已保存 ${result.saved.length} 张照片的参数`+(result.errors.length?"；失败："+result.errors.map(e=>e.error).join("；"):""),Boolean(result.errors.length));}catch(error){toast(error.message,true);}}
$("save").onclick=()=>save(state.active===null?[]:[state.active]);$("sync").onclick=()=>save([...state.selected]);
$("presets").onchange=()=>{if($("presets").value==="")return;const preset=clone(state.presets[Number($("presets").value)].edits);if($("preset-lock").checked)for(const key of ["exposure_ev","temperature","tint"])preset[key]=state.edits[key];state.edits={...defaults,...preset};$("crop-ratio").value="original";updateControls();changed();};
$("preset-open").onclick=()=>{$("preset-name").value="";$("preset-dialog").showModal();};$("preset-submit").onclick=async()=>{if(!$("preset-name").reportValidity())return;try{const data=await(await api("/api/preset",{name:$("preset-name").value,edits:state.edits})).json();state.presets=data.presets;renderPresets();$("preset-dialog").close();toast("预设已保存");}catch(error){toast(error.message,true);}};
function openImport(){$("import-errors").textContent="";$("import-dialog").showModal();}$("import-open").onclick=openImport;$("empty-import").onclick=openImport;
$("import-submit").onclick=async()=>{if(!$("import-path").reportValidity())return;$("import-submit").disabled=true;try{const result=await(await api("/api/import",{path:$("import-path").value})).json();await reloadLibrary();if(result.errors.length){$("import-errors").textContent=result.errors.map(e=>e.name+"："+e.error).join("\n");}else{$("import-dialog").close();toast("已导入 "+result.imported.length+" 张照片");}}catch(error){$("import-errors").textContent=error.message;}finally{$("import-submit").disabled=false;}};
$("export").onclick=async()=>{const ids=state.selected.size?[...state.selected]:[state.active];const size=$("export-size").value;try{const job=await(await api("/api/export",{ids,edits:state.edits,folder:$("export-folder").value,format:$("format").value,max_size:size?Number(size):null})).json();state.job=job.id;$("job-progress").hidden=false;$("cancel").hidden=false;$("export").disabled=true;$("job-report").replaceChildren();pollJob();}catch(error){toast(error.message,true);}};
async function pollJob(){try{const job=await(await api("/api/job?id="+state.job)).json();$("progress").value=job.progress;$("job-status").textContent=`${job.completed} / ${job.total} · ${Math.round(job.progress*100)}%`;if(["queued","running"].includes(job.state)){setTimeout(pollJob,500);return;}$("export").disabled=false;$("cancel").hidden=true;for(const file of job.files){const line=document.createElement("div");line.textContent=file.status==="done"?"✓ "+file.output:file.name+"："+file.error;line.className=file.status==="done"?"":"failed";$("job-report").append(line);}if(job.error){const line=document.createElement("div");line.textContent=job.error;line.className="failed";$("job-report").append(line);}toast(job.state==="completed"?"导出完成，文件已写入指定目录":job.state==="cancelled"?"导出已取消":"导出完成，部分文件失败；请查看报告",job.state!=="completed"&&job.state!=="cancelled");}catch(error){$("export").disabled=false;toast(error.message,true);}}
$("cancel").onclick=async()=>{try{await api("/api/cancel",{id:state.job});$("job-status").textContent="正在取消…";}catch(error){toast(error.message,true);}};
window.addEventListener("beforeunload",e=>{if(state.dirty){e.preventDefault();e.returnValue="";}});
updateControls();reloadLibrary().catch(error=>toast(error.message,true));
