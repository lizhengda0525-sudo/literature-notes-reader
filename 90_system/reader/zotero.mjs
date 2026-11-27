import {state, notice} from "./reader.mjs";

const $ = id => document.getElementById(id);
const messages = {
  choose: "找到可能的匹配。请核对书库中的条目。",
  choose_pdf: "这篇论文有多个 PDF。请选择阅读时要打开的附件。",
  missing: "原关联条目已移出书库。可以重新查找。",
  missing_pdf: "原关联 PDF 已移出条目。请选择新的打开目标。"
};

export function initZotero(call, project, acceptSignature) {
  const dialog = $("zotero-dialog");
  let session, searchSerial = 0, autoSerial = 0, selection, response, saving = false;

  function paint(paper, status = "") {
    const needsChoice = Object.hasOwn(messages, status);
    const uri = paper.zotero.pdf || paper.zotero.item;
    const button = $("zotero-link");
    paper.linkStatus = status;
    button.disabled = false;
    button.textContent = needsChoice ? "核对 Zotero 关联" : paper.zotero.pdf ? "Zotero 打开原文 ↗" :
      uri ? "Zotero 定位条目 ↗" : "关联 Zotero";
    button.title = needsChoice || !uri ? "在本机书库中查找条目和 PDF" : "使用本机 Zotero 打开";
    $("manage-zotero").hidden = !uri || needsChoice;
    $("zotero-status").textContent = messages[status] || "";
    $("zotero-status").hidden = !needsChoice;
  }

  async function resolve(paper) {
    if (!paper || dialog.open) return;
    const serial = ++autoSerial, root = project();
    try {
      const data = await call("resolve_zotero", root, paper.id);
      if (serial !== autoSerial || paper !== state.paper || root !== project()) return;
      acceptSignature(data);
      paper.zotero = data.zotero;
      paint(paper, data.status);
    } catch {
      // Existing deep links remain usable when the library is temporarily offline.
      // Opening the picker provides the detailed error and a retry action.
    }
  }

  function selectItem(item) {
    selection = item;
    const file = $("zotero-file"), pdfs = item.pdfs;
    const isCurrent = item.ref === response.current;
    const options = pdfs.map(pdf => new Option(pdf.title, pdf.key));
    if (pdfs.length > 1) options.unshift(new Option("请选择 PDF 或条目", ""));
    options.push(new Option("在 Zotero 定位书目条目", "item"));
    file.replaceChildren(...options);
    if (isCurrent && pdfs.some(pdf => pdf.key === response.attachment)) file.value = response.attachment;
    else if (isCurrent && response.attachment_mode === "item") file.value = "item";
    $("zotero-file-row").hidden = !pdfs.length;
    $("zotero-bind").disabled = !file.value || saving;
  }

  function draw(data) {
    response = data;
    selection = null;
    const list = $("zotero-results");
    list.querySelectorAll("label").forEach(label => label.remove());
    $("zotero-file-row").hidden = true;
    $("zotero-bind").disabled = true;
    list.hidden = !data.total;
    $("zotero-feedback").textContent = !data.total ? "没有匹配条目。可用题名片段、作者或 DOI 查找，刚导入后可再次查找。" :
      data.total > data.candidates.length ? "找到 " + data.total + " 项。请缩小搜索范围，当前显示前 " + data.candidates.length + " 项。" :
      "找到 " + data.total + " 项。请核对条目和打开目标。";
    for (const item of data.candidates) {
      const row = document.createElement("label"), radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "zotero-item";
      radio.value = item.ref;
      const copy = document.createElement("span"), title = document.createElement("strong"), info = document.createElement("small");
      title.textContent = item.title;
      info.textContent = [item.library, item.year, item.authors.slice(0, 3).join(" · "), item.doi,
        item.standalone ? "独立 PDF" : item.pdfs.length + " 个 PDF", item.reason,
        data.current === item.ref ? "当前关联" : ""].filter(Boolean).join(" · ");
      copy.append(title, info);
      row.append(radio, copy);
      list.append(row);
      radio.onchange = () => selectItem(item);
      if (item.ref === data.current || data.candidates.length === 1) {
        radio.checked = true;
        selectItem(item);
      }
    }
  }

  async function search() {
    const context = session, serial = ++searchSerial;
    if (!context || saving) return;
    selection = null;
    $("zotero-feedback").textContent = "正在读取本机书库…";
    $("zotero-results").hidden = true;
    $("zotero-file-row").hidden = true;
    $("zotero-bind").disabled = true;
    try {
      const data = await call("search_zotero", context.root, context.paper.id, $("zotero-search").value);
      if (context === session && serial === searchSerial) draw(data);
    } catch (error) {
      if (context === session && serial === searchSerial) $("zotero-feedback").textContent = error.message;
    }
  }

  function open() {
    if (!state.paper) return;
    ++autoSerial;
    session = {paper: state.paper, root: project()};
    $("zotero-paper").textContent = state.paper.id + " · " + state.paper.title;
    $("zotero-search").value = "";
    dialog.showModal();
    search();
  }

  $("manage-zotero").onclick = open;
  $("zotero-link").onclick = () => {
    const paper = state.paper;
    if (!paper) return;
    if (messages[paper.linkStatus] || !(paper.zotero.pdf || paper.zotero.item)) { open(); return; }
    call("open_zotero", project(), paper.id).then(() => notice("已请求 Zotero 打开。")).catch(notice);
  };
  $("zotero-search-form").onsubmit = event => { event.preventDefault(); search(); };
  $("zotero-file").onchange = () => { $("zotero-bind").disabled = !$("zotero-file").value || saving; };
  $("zotero-bind").onclick = async () => {
    const context = session, item = selection, target = $("zotero-file").value;
    if (!context || !item || !target || saving) return;
    saving = true;
    $("zotero-bind").disabled = true;
    const mode = target === "item" ? (item.pdfs.length ? "item" : "auto") : "manual";
    try {
      const data = await call("bind_zotero", context.root, context.paper.id, item.ref,
        mode === "manual" ? target : null, mode, response.revision);
      if (context !== session || state.paper !== context.paper || project() !== context.root) return;
      acceptSignature(data);
      state.paper.zotero = data.zotero;
      paint(state.paper, "linked");
      dialog.close();
      notice("已关联本篇论文。");
    } catch (error) {
      if (context === session) $("zotero-feedback").textContent = error.message;
    } finally {
      saving = false;
      if (context === session) $("zotero-bind").disabled = !selection || !$("zotero-file").value;
    }
  };
  dialog.addEventListener("close", () => { session = null; ++searchSerial; });
  const recheck = () => { if (!document.hidden && state.view === "reading" && !document.body.inert) resolve(state.paper); };
  window.addEventListener("focus", recheck);
  setInterval(recheck, 20000);
  return {
    paperChanged(paper) {
      if (session && (session.paper.id !== paper?.id || session.root !== project())) dialog.close();
      else if (session) session.paper = paper;
      if (paper) { paint(paper); resolve(paper); }
    }
  };
}
