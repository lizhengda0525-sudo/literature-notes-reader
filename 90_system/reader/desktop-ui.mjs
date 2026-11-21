import {state,loadPaper,refresh,setMode,renderMarkdown,renderNotes,imageUrl,notice,rememberPosition,settings} from './reader.mjs';

const $=id=>document.getElementById(id);
const edit={base:'',original:'',dirty:false,project:null,loading:false,saving:false,importing:false,ignoreGuard:false};
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
  if((edit.saving||edit.importing) && !edit.ignoreGuard)return false;
  if(edit.ignoreGuard||!edit.dirty)return true;
  clearTimeout(draftTimer);
  await preserve();
  return confirm('这篇笔记有未保存修改，已保留本机恢复草稿。确定离开当前笔记吗？');
}
function snapshot(){
  if(edit.saving || edit.importing){
    notice('正在写入笔记或图片，请在完成后退出。');
    return {busy:true};
  }
  document.body.inert = true;
  rememberPosition();
  return {project:edit.project,pid:state.paper?.id,text:$('note-editor')?.value||'',base_hash:edit.base,dirty:edit.dirty,ui:state.ui};
}
function syncDraft(){
  clearTimeout(draftTimer);
  draftTimer=setTimeout(()=>{if(state.paper)call('update_draft',edit.project,state.paper.id,$('note-editor').value,edit.base,edit.dirty).catch(notice);},200);
}
function preview(){
  const body=$('edit-preview');body.innerHTML=renderMarkdown($('note-editor').value);
  for(const img of body.querySelectorAll('img')){
    const src = imageUrl(img.getAttribute('src') || '', state.paper.id);
    if(src) img.src = src;
    else img.removeAttribute('src');
  }
  for(const link of body.querySelectorAll('a')){const href=link.getAttribute('href')||'';if(!/^https?:/.test(href))link.removeAttribute('href');}
}
function showMode(value){
  mode=value;setMode(value);
  document.body.classList.toggle('editing-note',value==='edit');
  $('editor-panel').hidden=value!=='edit';$('mode-edit').classList.toggle('active',value==='edit');
  $('mode-edit').setAttribute('aria-pressed',String(value==='edit'));

  if(value==='edit'){preview();$('note-editor').focus();}
}
async function loaded(paper){
  clearTimeout(draftTimer);
  clearTimeout(previewTimer);
  edit.loading=true;
  edit.base=paper.note_hash;edit.original=paper.note;edit.project=status?.project;
  $('note-editor').value=paper.note;setDirty(false);showMode(mode);$('recovery-banner').hidden=true;
  $('mode-edit').disabled=false;lastSignature='';
  try{
    if(paper.recovery){
      $('recovery-banner').hidden=false;
      $('restore-draft').onclick=()=>{
        $('note-editor').value=paper.recovery.text;edit.base=paper.recovery.base_hash;
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
  if(!edit.dirty||edit.saving||edit.importing||!state.paper)return;
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
        try{await loadPaper(state.paper.id);banner('已载入磁盘版本。');}finally{edit.ignoreGuard=false;}
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
  }catch(error){notice(error);banner('保存未完成，编辑内容仍在当前窗口中。请检查项目盘后重试。','warning');}
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
  if(document.body.inert||pollBusy||edit.loading||edit.saving||edit.importing)return;pollBusy=true;
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
  const tools=el('div',{class:'editor-tools'});tools.append(el('span',{},'Markdown 源文'),el('button',{id:'insert-image'},'插入图片'),el('input',{id:'image-file',type:'file',accept:'image/png,image/jpeg,image/webp',hidden:''}),el('button',{id:'toggle-preview','aria-pressed':'true'},'收起预览'));
  const split=el('div',{id:'editor-split',class:'has-preview'});split.append(el('textarea',{id:'note-editor','aria-label':'Markdown 编辑器',spellcheck:'false'}),el('div',{id:'edit-preview',class:'note-section','aria-label':'Markdown 实时预览'}));panel.append(tools,split);document.querySelector('.note-pane').insertBefore(panel,document.querySelector('.note-pane footer'));
  const welcome=el('div',{id:'welcome-panel',hidden:''});welcome.append(el('h2',{},'打开你的文献阅读项目'),el('p',{},'选择已有项目文件夹，直接阅读和维护其中的 Markdown 笔记。'),el('button',{id:'welcome-open'},'选择项目文件夹'));$('note-scroll').prepend(welcome);
  document.body.insertAdjacentHTML('beforeend',`<dialog id="conflict-dialog"><h2>原文件已在外部修改</h2><p>没有覆盖磁盘内容。请对照两份内容，再选择如何继续。</p><div class="conflict-columns"><label>当前磁盘版本<textarea id="disk-version" readonly></textarea></label><label>你的未保存内容<textarea id="my-version" readonly></textarea></label></div><div class="dialog-actions"><button id="reload-disk">使用磁盘版本</button><button id="merge-continue">继续编辑并手动合并</button><button id="close-conflict">返回</button></div></dialog><dialog id="validation-dialog"><h2>文件已保存，以下记录需要核对</h2><pre id="validation-errors"></pre><button id="close-validation">返回笔记</button></dialog><dialog id="image-dialog"><button id="close-image">关闭图片</button><img id="evidence-image" alt="原文证据图片"></dialog>`);
}
function bindEditor(){
  buildUi();
  window.readerDesktop={beforeNavigate,loaded,persistUi,snapshot,
    readPaper: id=>call('read_note',edit.project,id),
    listPapers: async()=>(await call('list_papers',edit.project)).papers
  };
  $('choose-project').onclick=chooseProject;$('welcome-open').onclick=chooseProject;
  $('recent-projects').onchange=async event=>{if(!event.target.value||!(await beforeNavigate()))return;try{await adoptProject(await call('reopen_project',event.target.value));}catch(error){notice(error);}};
  $('mode-edit').onclick=()=>showMode('edit');$('mode-read').onclick=()=>showMode('read');
  $('save-note').onclick=save;
  bindImages();
  $('note-editor').oninput=()=>{setDirty($('note-editor').value!==edit.original);syncDraft();clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(!$('edit-preview').hidden)preview();},250);};
  $('toggle-preview').onclick=()=>{const visible=$('edit-preview').hidden;$('edit-preview').hidden=!visible;$('editor-split').classList.toggle('has-preview',visible);$('toggle-preview').setAttribute('aria-pressed',String(visible));$('toggle-preview').textContent=visible?'收起预览':'显示预览';if(visible)preview();};
  $('refresh').onclick=async()=>{if(!(await beforeNavigate()))return;edit.ignoreGuard=true;try{await refresh();}catch(error){notice(error);}finally{edit.ignoreGuard=false;}};
  $('open-note').onclick=()=>call('open_note',edit.project,state.paper.id).then(()=>notice('已交给本地 Markdown 编辑器。')).catch(notice);
  $('zotero-link').onclick=event=>{event.preventDefault();call('open_zotero',edit.project,state.paper.id).then(()=>notice('已请求 Zotero 打开。')).catch(notice);};
  $('close-conflict').onclick=()=>$('conflict-dialog').close();$('close-validation').onclick=()=>$('validation-dialog').close();$('close-image').onclick=()=>$('image-dialog').close();
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}});
  document.addEventListener('click',event=>{
    const link=event.target.closest('a');if(!link)return;
    const href=link.href;if(!href)return;event.preventDefault();
    if(href.startsWith(location.origin+'/paper/')&&/\.(png|jpe?g|webp)$/i.test(href)){$('evidence-image').src=href;$('image-dialog').showModal();}
    else call('open_link',href).catch(notice);
  });
}
async function init(){
  if(api||!window.pywebview?.api)return;
  api=window.pywebview.api;bindEditor();
  try{await adoptProject(await call('get_status'));}catch(error){notice(error);}
  setInterval(poll,2000);
}
window.addEventListener('pywebviewready',()=>init().catch(notice));
if(window.pywebview?.api)init().catch(notice);

function bindImages() {
  const editor = $('note-editor'), picker = $('image-file');
  async function insert(file) {
    if (!file || edit.importing || edit.saving || !state.paper) return;
    if (file.size > 8 * 1024 * 1024) { notice('每张图片最大为 8 MB。'); return; }
    const paper = state.paper, start = editor.selectionStart, end = editor.selectionEnd;
    edit.importing = true;
    editor.readOnly = true;
    try {
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('无法读取图片文件。'));
        reader.readAsDataURL(file);
      });
      const saved = await call('add_image', edit.project, paper.id, url.split(',')[1]);
      if (paper !== state.paper) return;
      const alt = (file.name || '图片').replace(/\.[^.]*$/, '').replace(/[\[\]\\\r\n]/g, ' ');
      const markdown = '![' + alt + '](' + saved.path + ')';
      editor.readOnly = false;
      editor.focus();
      editor.setSelectionRange(start, end);
      if (!document.execCommand('insertText', false, markdown)) {
        editor.setRangeText(markdown, start, end, 'end');
        editor.dispatchEvent(new Event('input'));
      }
      notice('图片已存入本篇 images 文件夹。保存笔记即可保留引用。');
    } catch (error) { notice(error); }
    finally { edit.importing = false; editor.readOnly = false; picker.value = ''; }
  }
  $('insert-image').onclick = () => picker.click();
  picker.onchange = () => insert(picker.files[0]);
  editor.addEventListener('paste', event => {
    const image = [...event.clipboardData.items].find(item => item.type.startsWith('image/'));
    if (image) { event.preventDefault(); insert(image.getAsFile()); }
  });
  editor.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  editor.addEventListener('drop', event => {
    if (event.dataTransfer.files.length) { event.preventDefault(); insert(event.dataTransfer.files[0]); }
  });
}
