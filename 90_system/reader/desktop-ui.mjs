import {state,loadPaper,refresh,setMode,renderMarkdown,renderNotes,notice,rememberPosition,settings} from './reader.mjs';

const $=id=>document.getElementById(id);
const edit={base:'',original:'',dirty:false,project:null,loading:false,saving:false,ignoreGuard:false};
let api,status,lastSignature='',uiTimer,draftTimer,previewTimer,pollBusy=false,mode='read';
const el=(tag,attributes={},text='')=>{const item=document.createElement(tag);for(const[k,v]of Object.entries(attributes))item.setAttribute(k,v);item.textContent=text;return item;};
async function call(name,...args){const result=await api[name](...args);if(result?.ok===false&&!result.conflict)throw new Error(result.error||'操作未完成。');return result;}
function banner(text,kind='info'){$('desktop-banner').textContent=text;$('desktop-banner').hidden=!text;$('desktop-banner').dataset.kind=kind;}
function setDirty(value){edit.dirty=value;$('save-note').disabled=!value||edit.saving;$('save-status').textContent=value?'尚未保存':'已保存到 MD';$('save-status').classList.toggle('dirty',value);}
function persistUi(){clearTimeout(uiTimer);uiTimer=setTimeout(()=>{if(edit.project)call('save_ui',edit.project,state.ui).catch(notice);},500);}
async function preserve(){
  if(edit.dirty&&state.paper){await call('preserve_draft',edit.project,state.paper.id,$('note-editor').value,edit.base);}
}
async function beforeNavigate(){
  if(edit.saving && !edit.ignoreGuard)return false;
  if(edit.ignoreGuard||!edit.dirty)return true;
  clearTimeout(draftTimer);
  await preserve();
  return confirm('这篇笔记有未保存修改，已保留本机恢复草稿。确定离开当前笔记吗？');
}
function snapshot(){
  rememberPosition();
  return {project:edit.project,pid:state.paper?.id,text:$('note-editor')?.value||'',base_hash:edit.base,dirty:edit.dirty,ui:state.ui};
}
function syncDraft(){
  clearTimeout(draftTimer);
  draftTimer=setTimeout(()=>{if(state.paper)call('update_draft',edit.project,state.paper.id,$('note-editor').value,edit.base,edit.dirty).catch(notice);},200);
}
function preview(){
  const body=$('edit-preview');body.innerHTML=renderMarkdown($('note-editor').value);
  for(const img of body.querySelectorAll('img')){const src=img.getAttribute('src')||'';if(/^images\/[\w.-]+\.(png|jpe?g|webp)$/i.test(src))img.src='/paper/'+state.paper.id+'/'+src;else img.removeAttribute('src');}
  for(const link of body.querySelectorAll('a')){const href=link.getAttribute('href')||'';if(!/^https?:/.test(href))link.removeAttribute('href');}
}
function showMode(value){
  mode=value;setMode(value==='edit'?'edit':'read');
  $('editor-panel').hidden=value!=='edit';$('mode-edit').classList.toggle('active',value==='edit');
  $('mode-edit').setAttribute('aria-pressed',String(value==='edit'));
  $('mode-read').classList.toggle('active',value!=='edit');$('mode-source').hidden=true;
  $('outline-select').disabled=value==='edit';$('outline').hidden=value==='edit';
  if(value==='edit')$('note-editor').focus();
}
async function loaded(paper){
  clearTimeout(draftTimer);
  clearTimeout(previewTimer);
  edit.loading=true;
  edit.base=paper.note_hash;edit.original=paper.note;edit.project=status?.project;
  $('note-editor').value=paper.note;setDirty(false);showMode(mode);$('recovery-banner').hidden=true;
  $('mode-edit').disabled=false;lastSignature='';
  if(!$('edit-preview').hidden)preview();
  try{
    const value=await call('read_note',edit.project,paper.id);
    if(state.paper!==paper)return;
    if(value.recovery){
      $('recovery-banner').hidden=false;
      $('restore-draft').onclick=()=>{
        $('note-editor').value=value.recovery.text;edit.base=value.recovery.base_hash;
        setDirty($('note-editor').value!==edit.original);showMode('edit');syncDraft();
        $('recovery-banner').hidden=true;banner('已恢复未保存草稿。保存前仍会比较磁盘版本。');
      };
      $('discard-draft').onclick=async()=>{if(confirm('确定放弃这份恢复草稿？原 MD 不会改动。')){await call('discard_recovery',edit.project,paper.id);$('recovery-banner').hidden=true;}};
    }
    if(paper.validation_errors?.length)banner('此笔记有需要核对的证据格式。可继续编辑，保存后查看项目检查结果。','warning');
  }catch(error){banner(error.message,'warning');}
  finally{if(state.paper===paper)edit.loading=false;}
}
async function save(){
  if(!edit.dirty||edit.saving||!state.paper)return;
  edit.saving=true;$('note-editor').readOnly=true;$('save-note').disabled=true;$('save-status').textContent='正在保存…';
  try{
    await preserve();
    const result=await call('save_note',edit.project,state.paper.id,$('note-editor').value,edit.base);
    if(result.conflict){
      $('disk-version').value=result.note;$('my-version').value=$('note-editor').value;
      $('conflict-dialog').showModal();
      $('merge-continue').onclick=()=>{edit.base=result.hash;$('conflict-dialog').close();banner('已保留你的编辑。请对照磁盘版本合并内容，完成后再次保存。','warning');};
      $('reload-disk').onclick=async()=>{
        $('conflict-dialog').close();setDirty(false);edit.ignoreGuard=true;
        try{await loadPaper(state.paper.id,true);banner('已载入磁盘版本；原编辑仍保存在恢复草稿中。');}finally{edit.ignoreGuard=false;}
      };
      return;
    }
    edit.base=result.hash;edit.original=$('note-editor').value;state.paper.note=edit.original;state.paper.note_hash=result.hash;
    const entry=state.papers.find(p=>p.id===state.paper.id);if(entry)entry.note=edit.original;
    setDirty(false);
    edit.ignoreGuard=true;
    try {
      await loadPaper(state.paper.id);
      banner('已保存到原 Markdown 文件。');
    } catch (error) {
      renderNotes();
      banner('笔记已保存。刷新笔记库可重新载入证据索引。','warning');
    } finally {edit.ignoreGuard=false;}
    if(result.validation?.ok===false){
      $('validation-errors').textContent=(result.validation.errors||[]).join('\n');
      $('validation-dialog').showModal();banner('文件已保存。项目检查发现需要核对的记录，请查看详情。','warning');
    }
  }catch(error){notice(error);banner('保存未完成，已尽力保留恢复草稿。请保持窗口打开并检查项目盘。','warning');}
  finally{edit.saving=false;$('note-editor').readOnly=false;$('save-note').disabled=!edit.dirty;$('save-status').textContent=edit.dirty?'尚未保存':'已保存到 MD';}
}
async function adoptProject(value){
  if(value.cancelled)return;
  status=value;edit.project=value.project;state.ui={positions:{},...value.ui};state.paper=null;
  ++state.request;clearTimeout(draftTimer);clearTimeout(previewTimer);clearTimeout(uiTimer);
  mode='read';edit.ignoreGuard=true;setDirty(false);settings();
  $('library-search').value='';$('status-filter').value='';$('tag-filter').value='';$('rq-filter').value='';
  $('project-name').textContent=value.name;$('project-name').title=value.project||'';
  $('welcome-panel').hidden=!!value.project;
  $('note-editor').value='';$('edit-preview').replaceChildren();
  $('recent-projects').replaceChildren(new Option('最近项目',''),...value.recent.map(p=>new Option(p,p)));
  try{await refresh();lastSignature=value.signature;banner(value.error||'');}finally{edit.ignoreGuard=false;}
}
async function chooseProject(){if(!(await beforeNavigate()))return;try{await adoptProject(await call('choose_project'));}catch(error){notice(error);}}
async function poll(){
  if(pollBusy||edit.loading||edit.saving)return;pollBusy=true;
  try{
    const value=await call('get_status');
    if(value.generation!==status.generation){await adoptProject(value);return;}
    if(value.error){banner(value.error,'warning');return;}
    if(!value.available)return;
    if(lastSignature&&value.signature!==lastSignature){
      if(edit.dirty){banner('原文件或项目记录已在外部修改。你的编辑已保留，保存时将检查冲突。','warning');}
      else{edit.ignoreGuard=true;try{await refresh();banner('已更新外部修改的笔记。');}finally{edit.ignoreGuard=false;}}
    }
    lastSignature=value.signature;
  }catch(error){banner('项目暂时无法读取；未保存内容将继续留在本机恢复区。','warning');}
  finally{pollBusy=false;}
}
function buildUi(){
  document.body.classList.add('desktop-app');
  const header=document.querySelector('.workspace-tools');
  const project=el('button',{id:'choose-project'},'打开项目…');
  const recent=el('select',{id:'recent-projects','aria-label':'最近项目'});
  const name=el('span',{id:'project-name'});
  header.append(project, recent, name);
  const toolbar=document.querySelector('.note-toolbar .segmented');toolbar.append(el('button',{id:'mode-edit','aria-pressed':'false',disabled:''},'编辑 MD'));
  const saveButton=el('button',{id:'save-note',disabled:''},'保存 · Ctrl+S');document.querySelector('.actions').append(saveButton);
  const statusElement=el('span',{id:'save-status'},'已保存到 MD');document.querySelector('.note-pane footer').append(statusElement);
  const desktopBanner=el('div',{id:'desktop-banner',role:'status',hidden:''});document.querySelector('.note-pane').insertBefore(desktopBanner,$('note-scroll'));
  const recovery=el('div',{id:'recovery-banner',hidden:''});recovery.append(el('span',{},'发现未保存的本机恢复草稿。'),el('button',{id:'restore-draft'},'恢复草稿'),el('button',{id:'discard-draft'},'放弃草稿'));document.querySelector('.note-pane').insertBefore(recovery,$('note-scroll'));
  const panel=el('div',{id:'editor-panel',hidden:''});
  const tools=el('div',{class:'editor-tools'});tools.append(el('span',{},'直接编辑原 Markdown · 支持 Ctrl+Z 撤销'),el('button',{id:'toggle-preview'},'显示预览'));
  const split=el('div',{id:'editor-split'});split.append(el('textarea',{id:'note-editor','aria-label':'Markdown 编辑器',spellcheck:'false'}),el('div',{id:'edit-preview',class:'note-section',hidden:''}));panel.append(tools,split);document.querySelector('.note-pane').insertBefore(panel,document.querySelector('.note-pane footer'));
  const welcome=el('div',{id:'welcome-panel',hidden:''});welcome.append(el('h2',{},'打开你的文献阅读项目'),el('p',{},'选择已有项目文件夹，直接阅读和维护其中的 Markdown 笔记。'),el('button',{id:'welcome-open'},'选择项目文件夹'));$('note-scroll').prepend(welcome);
  document.body.insertAdjacentHTML('beforeend',`<dialog id="conflict-dialog"><h2>原文件已在外部修改</h2><p>没有覆盖磁盘内容。请对照两份内容，再选择如何继续。</p><div class="conflict-columns"><label>当前磁盘版本<textarea id="disk-version" readonly></textarea></label><label>你的未保存内容<textarea id="my-version" readonly></textarea></label></div><div class="dialog-actions"><button id="reload-disk">保留草稿并载入磁盘</button><button id="merge-continue">继续编辑并手动合并</button><button id="close-conflict">返回</button></div></dialog><dialog id="validation-dialog"><h2>文件已保存，以下记录需要核对</h2><pre id="validation-errors"></pre><button id="close-validation">返回笔记</button></dialog><dialog id="image-dialog"><button id="close-image">关闭图片</button><img id="evidence-image" alt="原文证据图片"></dialog>`);
}
async function init(){
  if(state.desktop||!window.pywebview?.api)return;
  api=window.pywebview.api;state.desktop=true;buildUi();
  window.readerDesktop={beforeNavigate,loaded,persistUi,snapshot};
  $('choose-project').onclick=chooseProject;$('welcome-open').onclick=chooseProject;
  $('recent-projects').onchange=async event=>{if(!event.target.value||!(await beforeNavigate()))return;try{await adoptProject(await call('reopen_project',event.target.value));}catch(error){notice(error);}};
  $('mode-edit').onclick=()=>showMode('edit');$('mode-read').onclick=()=>showMode('read');
  $('save-note').onclick=save;
  $('note-editor').oninput=()=>{setDirty($('note-editor').value!==edit.original);syncDraft();clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(!$('edit-preview').hidden)preview();},250);};
  $('toggle-preview').onclick=()=>{const visible=$('edit-preview').hidden;$('edit-preview').hidden=!visible;$('editor-split').classList.toggle('has-preview',visible);$('toggle-preview').textContent=visible?'收起预览':'显示预览';if(visible)preview();};
  $('refresh').onclick=async()=>{if(!(await beforeNavigate()))return;edit.ignoreGuard=true;try{await refresh();}catch(error){notice(error);}finally{edit.ignoreGuard=false;}};
  $('open-note').onclick=()=>call('open_note',edit.project,state.paper.id).catch(notice);
  $('zotero-link').onclick=event=>{event.preventDefault();call('open_zotero',edit.project,state.paper.id).catch(notice);};
  $('close-conflict').onclick=()=>$('conflict-dialog').close();$('close-validation').onclick=()=>$('validation-dialog').close();$('close-image').onclick=()=>$('image-dialog').close();
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}});
  document.addEventListener('click',event=>{
    const link=event.target.closest('a');if(!link||link.id==='zotero-link')return;
    const href=link.href;if(!href)return;event.preventDefault();
    if(href.startsWith(location.origin+'/paper/')&&/\.(png|jpe?g|webp)$/i.test(href)){$('evidence-image').src=href;$('image-dialog').showModal();}
    else call('open_link',href).catch(notice);
  });
  try{await adoptProject(await call('get_status'));}catch(error){notice(error);}
  setInterval(poll,2000);
}
window.addEventListener('pywebviewready',()=>init().catch(notice));
if(window.pywebview?.api)init().catch(notice);
