import {marked} from "./vendor/marked/marked.esm.js";
import {splitNote} from "./mapping.mjs";
import {bindEvidence, linkEvidence, versionName} from "./evidence.mjs";

const $ = id => document.getElementById(id);
const state = {papers: [], paper: null, request: 0, mode: "read", desktop: false, ui: {positions: {}}};
const labels = {queued: "待阅读", skimmed: "已速读", deep_read: "已精读", skipped: "已跳过"};
const verification = {reviewed: "用户已核对", partial: "部分核对", unreviewed: "用户待核对"};
const scroll = $("note-scroll");
let noticeTimer, positionTimer, scrollFrame, catalogRequest = 0;
let sectionNodes = [], activeSection = null;
let storagePrefix = "note-";

function readLocal(key) {
  try { return localStorage.getItem(storagePrefix + key); } catch { return null; }
}
function writeLocal(key, value) {
  try { localStorage.setItem(storagePrefix + key, value); } catch {}
}
function notice(value) {
  $("notice").textContent = value?.message || String(value);
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6500);
}
async function getJson(url) {
  const response = await fetch(url, {cache: "no-store"});
  if (!response.ok) throw new Error("无法读取本地笔记，请检查项目目录后刷新。");
  return response.json();
}
function renderMarkdown(source) {
  const math = [];
  const prefix = "LITMATH" + crypto.randomUUID().replaceAll("-", "");
  const encoded = source.replace(/\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g, (_, display, inline) => {
    math.push({expression: display ?? inline, display: display !== undefined});
    return prefix + (math.length - 1) + "END";
  });
  const template = document.createElement("template");
  template.innerHTML = DOMPurify.sanitize(marked.parse(encoded, {gfm: true}));
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.data.includes(prefix)) nodes.push(walker.currentNode);
  const pattern = new RegExp(prefix + "(\\d+)END", "g");
  for (const node of nodes) {
    const fragment = document.createDocumentFragment();
    let end = 0;
    for (const match of node.data.matchAll(pattern)) {
      const item = math[Number(match[1])];
      const formula = document.createElement("span");
      katex.render(item.expression, formula, {displayMode: item.display, throwOnError: false, trust: false, strict: "ignore"});
      fragment.append(node.data.slice(end, match.index), formula);
      end = match.index + match[0].length;
    }
    fragment.append(node.data.slice(end));
    node.replaceWith(fragment);
  }
  return template.innerHTML;
}
function mobileLibrary(open) {
  document.body.classList.toggle("library-open", open);
  $("library-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("library-search").focus();
}
function renderLibrary() {
  const query = $("library-search").value.trim().toLocaleLowerCase();
  const status = $("status-filter").value, rq = $("rq-filter").value, tag = $("tag-filter").value;
  const shown = state.papers.filter(paper => {
    if ((status && paper.reading_status !== status) || (rq && !paper.research_questions.includes(rq)) || (tag && !paper.tags.includes(tag))) return false;
    return !query || [paper.id, paper.title, ...paper.authors, ...paper.tags, ...paper.research_questions, paper.note].join("\n").toLocaleLowerCase().includes(query);
  });
  const list = $("paper-list");
  list.replaceChildren();
  for (const paper of shown) {
    const selected = paper.id === state.paper?.id;
    const button = document.createElement("button");
    button.className = "paper-card" + (selected ? " active" : "");
    button.setAttribute("aria-current", selected ? "page" : "false");
    const caption = document.createElement("span");
    caption.className = "paper-caption";
    const statusLabel = document.createElement("span");
    statusLabel.textContent = labels[paper.reading_status] || paper.reading_status;
    caption.append(paper.id, statusLabel);
    const title = document.createElement("strong");
    title.textContent = paper.title;
    const description = document.createElement("span");
    description.className = "paper-labels";
    description.textContent = [paper.authors[0], paper.year].filter(Boolean).join(" · ");
    button.append(caption, title, description);
    button.onclick = async () => {
      try {
        if (!selected && !await loadPaper(paper.id)) return;
        mobileLibrary(false);
      } catch (error) { notice(error); }
    };
    list.append(button);
  }
  const count = [status, rq, tag].filter(Boolean).length;
  $("filter-count").textContent = count ? String(count) : "";
  $("library-count").textContent = (shown.length === state.papers.length ? shown.length : shown.length + " / " + state.papers.length) + " 篇";
  $("no-papers").hidden = !!shown.length;
  $("empty-message").textContent = state.papers.length ? "没有匹配的论文。" : "尚未登记论文。";
  $("reset-filters").hidden = !state.papers.length;
}
function rememberPosition() {
  if (!state.paper || state.mode !== "read") return;
  const id = state.paper.id;
  state.ui.positions ??= {};
  state.ui.positions[id] = scroll.scrollTop;
  if (state.desktop) window.readerDesktop?.persistUi();
  else writeLocal("position:" + id, String(scroll.scrollTop));
}
function positionFor(id) {
  if (state.desktop) return Number(state.ui.positions?.[id]) || 0;
  return Number(readLocal("position:" + id) || state.ui.positions?.[id]) || 0;
}
function trackLocation() {
  const reading = state.mode === "read" && !!state.paper;
  const extent = scroll.scrollHeight - scroll.clientHeight;
  const percent = reading && extent > 0 ? Math.min(100, Math.round(100 * scroll.scrollTop / extent)) : 0;
  $("progress-fill").style.transform = "scaleX(" + percent / 100 + ")";
  $("scroll-percent").textContent = reading ? percent + "%" : "";
  if (!reading) return;
  const top = scroll.getBoundingClientRect().top + 32;
  let active = null;
  for (const entry of sectionNodes) {
    if (entry.card.getBoundingClientRect().top > top) break;
    active = entry;
  }
  if (extent > 0 && scroll.scrollTop >= extent - 1) active = sectionNodes.at(-1);
  if (active === activeSection) return;
  activeSection?.button.removeAttribute("aria-current");
  active?.button.setAttribute("aria-current", "location");
  activeSection = active;
  $("outline-select").value = active?.card.id || "";
  if (active && $("outline").clientHeight) {
    const nav = $("outline");
    const item = active.button.getBoundingClientRect(), box = nav.getBoundingClientRect();
    if (item.bottom > box.bottom) nav.scrollTop += item.bottom - box.bottom;
    else if (item.top < box.top) nav.scrollTop += item.top - box.top;
  }
}
function goToSection(id) {
  const entry = sectionNodes.find(item => item.card.id === id);
  if (!entry) return;
  scroll.scrollTo({top: scroll.scrollTop + entry.card.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 12});
  trackLocation();
}
function setMode(mode) {
  const previous = state.mode;
  if (previous !== mode) rememberPosition();
  state.mode = mode;
  $("note-content").hidden = mode !== "read";
  $("markdown-source").hidden = mode !== "source";
  scroll.hidden = mode === "edit";
  for (const [id, value] of [["mode-read", "read"], ["mode-source", "source"]]) {
    $(id).classList.toggle("active", mode === value);
    $(id).setAttribute("aria-pressed", String(mode === value));
  }
  $("outline").hidden = mode !== "read";
  $("outline-select").disabled = mode !== "read" || !state.paper;
  $("view-status").textContent = ({read: "精读笔记", source: "Markdown 源文", edit: "编辑 Markdown"})[mode];
  if (previous !== mode && state.paper) scroll.scrollTop = mode === "read" ? positionFor(state.paper.id) : 0;
  trackLocation();
}
function prepareBody(section, paper) {
  const body = document.createElement("div");
  body.innerHTML = renderMarkdown(section.body);
  for (const table of body.querySelectorAll("table")) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.replaceWith(wrap);
    wrap.append(table);
  }
  const imagePath = /^images\/[\w.-]+\.(png|jpe?g|webp)$/i;
  const fileUrl = relative => "/paper/" + paper.id + "/" + relative.split("/").map(encodeURIComponent).join("/");
  for (const img of body.querySelectorAll("img")) {
    const src = img.getAttribute("src") || "";
    if (imagePath.test(src)) { img.src = fileUrl(src); img.loading = "lazy"; }
    else { img.removeAttribute("src"); img.alt = "图片不在本篇档案中"; }
  }
  for (const link of body.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (imagePath.test(href)) link.href = fileUrl(href);
    else if (paper.versions.some(v => v.file === href)) {
      link.replaceWith(document.createTextNode(link.textContent + "（在 Zotero 阅读）"));
      continue;
    } else if (!/^https?:/i.test(href)) { link.removeAttribute("href"); continue; }
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  if (section.title !== "原文证据") linkEvidence(body, paper);
  return body;
}
function renderNotes() {
  const paper = state.paper;
  const sections = splitNote(paper.note, paper.id, paper.evidence);
  $("note-content").replaceChildren();
  $("outline").replaceChildren();
  $("outline-select").replaceChildren(new Option("本篇目录", ""));
  sectionNodes = [];
  activeSection = null;
  for (const section of sections) {
    const card = document.createElement("section");
    card.id = section.id;
    card.className = "note-section";
    if (section.intro) card.classList.add("note-intro");
    if (section.title === "一句话定位") card.classList.add("note-summary");
    if (!section.body) card.classList.add("section-heading");
    const body = prepareBody(section, paper);
    if (section.intro || section.title === "原文证据") {
      const details = document.createElement("details"), summary = document.createElement("summary");
      summary.textContent = section.intro ? section.title : "原文证据表 · " + paper.evidence.length + " 条";
      details.append(summary, body);
      card.append(details);
    } else {
      const heading = document.createElement(section.level === 3 ? "h3" : "h2");
      heading.textContent = section.title;
      card.append(heading, body);
    }
    const button = document.createElement("button");
    button.className = "outline-link" + (section.level === 3 ? " sub" : "");
    button.textContent = section.title;
    button.onclick = () => goToSection(card.id);
    $("outline").append(button);
    $("outline-select").append(new Option(section.title, section.id));
    $("note-content").append(card);
    sectionNodes.push({card, button});
  }
  bindEvidence(paper);
}
function renderHeading(paper) {
  $("paper-id").textContent = paper.id;
  $("paper-edition").textContent = [versionName(paper, paper.current_version), labels[paper.reading_status] || paper.reading_status, verification[paper.verification_status] || paper.verification_status].join("  /  ");
  $("paper-title").textContent = paper.title;
  $("paper-byline").textContent = paper.authors.join(" · ");
  $("paper-journal").textContent = [paper.year, paper.journal].filter(Boolean).join("  /  ");
  const meta = $("paper-meta");
  meta.replaceChildren();
  for (const [label, values] of [["研究问题", paper.research_questions], ["方法标签", paper.tags], ["原文版本", paper.versions.map(v => v.label + " (" + v.id + ") · " + v.pages + " 页")]]) {
    if (!values.length) continue;
    const line = document.createElement("p");
    line.textContent = label + "　" + values.join(" / ");
    meta.append(line);
  }
  meta.hidden = true;
  $("show-details").setAttribute("aria-expanded", "false");
  $("note-path").textContent = "20_papers/" + paper.id + "/close-reading.md";
  $("note-path").title = paper.note_path;
  const uri = paper.zotero.pdf || paper.zotero.item;
  const link = $("zotero-link");
  link.hidden = !uri;
  if (uri) link.href = uri;
  else link.removeAttribute("href");
  link.textContent = paper.zotero.pdf ? "Zotero 打开原文 ↗" : "Zotero 定位条目 ↗";
  $("zotero-status").hidden = !!uri;
  $("zotero-status").textContent = "此篇尚未关联 Zotero 条目，可在 Zotero 中按题名查找。";
  $("open-note").disabled = false;
}
async function loadPaper(id) {
  if (window.readerDesktop && !await window.readerDesktop.beforeNavigate()) return false;
  rememberPosition();
  clearTimeout(positionTimer);
  const request = ++state.request;
  const paper = await getJson("/api/papers/" + encodeURIComponent(id));
  if (request !== state.request) return false;
  const position = positionFor(id);
  state.paper = paper;
  state.ui.last_paper = paper.id;
  $("reading-page").hidden = false;
  $("empty-reading").hidden = true;
  renderHeading(paper);
  $("markdown-source").textContent = paper.note;
  renderNotes();
  renderLibrary();
  setMode(state.mode);
  scroll.scrollTop = state.mode === "read" ? position : 0;
  trackLocation();
  const url = new URL(location.href);
  url.searchParams.set("paper", paper.id);
  history.replaceState(null, "", url);
  if (!state.desktop) writeLocal("last-paper", paper.id);
  document.title = paper.title + " · 精读笔记库";
  await window.readerDesktop?.loaded(paper);
  return true;
}
function clearPaper() {
  ++state.request;
  state.paper = null;
  sectionNodes = [];
  activeSection = null;
  $("reading-page").hidden = true;
  const welcome = $("welcome-panel");
  $("empty-reading").hidden = Boolean(welcome && !welcome.hidden);
  $("page-map").hidden = true;
  $("open-note").disabled = true;
  $("note-path").textContent = "";
  $("outline").replaceChildren();
  $("outline-select").replaceChildren(new Option("本篇目录", ""));
  if ($("mode-edit")) $("mode-edit").disabled = true;
  if ($("editor-panel")) $("editor-panel").hidden = true;
  if ($("recovery-banner")) $("recovery-banner").hidden = true;
  setMode("read");
  document.title = "精读笔记库";
}
async function refresh() {
  const request = ++catalogRequest;
  rememberPosition();
  const papers = await getJson("/api/papers");
  if (request !== catalogRequest) return;
  state.papers = papers;
  for (const [id, field, title] of [["rq-filter", "research_questions", "全部问题"], ["tag-filter", "tags", "全部标签"]]) {
    const select = $(id), previous = select.value;
    const values = [...new Set(papers.flatMap(p => p[field]))].sort();
    select.replaceChildren(new Option(title, ""), ...values.map(v => new Option(v, v)));
    select.value = values.includes(previous) ? previous : "";
  }
  renderLibrary();
  const requested = new URLSearchParams(location.search).get("paper");
  const selected = state.paper?.id || (state.desktop ? state.ui.last_paper : requested || readLocal("last-paper"));
  const target = papers.find(p => p.id === selected) || papers[0];
  if (target) await loadPaper(target.id);
  else clearPaper();
}

function settings() {
  const size = Math.max(13, Math.min(24, Number(state.ui.font_size) || 16));
  document.documentElement.style.setProperty("--note-font", size + "px");
  document.body.classList.toggle("reading-wide", state.ui.reading_width === "wide");
  document.body.classList.toggle("focus-mode", !!state.ui.focus);
  $("font-size").value = size;
  $("font-value").value = size + " px";
  $("width-setting").value = state.ui.reading_width || "normal";
  $("focus-reading").setAttribute("aria-pressed", String(!!state.ui.focus));
  scheduleLocation();
}
function saveSettings() {
  settings();
  if (state.desktop) window.readerDesktop?.persistUi();
  else writeLocal("preferences", JSON.stringify({font_size: state.ui.font_size, focus: state.ui.focus, reading_width: state.ui.reading_width}));
}
function scheduleLocation() {
  cancelAnimationFrame(scrollFrame);
  scrollFrame = requestAnimationFrame(trackLocation);
}
for (const id of ["library-search", "status-filter", "rq-filter", "tag-filter"]) {
  $(id).addEventListener(id === "library-search" ? "input" : "change", renderLibrary);
}
$("reset-filters").onclick = () => {
  for (const id of ["library-search", "status-filter", "rq-filter", "tag-filter"]) $(id).value = "";
  renderLibrary();
  $("library-search").focus();
};
$("library-toggle").onclick = () => mobileLibrary(true);
$("library-back").onclick = () => { mobileLibrary(false); $("library-toggle").focus(); };
$("refresh").onclick = () => refresh().catch(notice);
$("mode-read").onclick = () => setMode("read");
$("mode-source").onclick = () => setMode("source");
$("outline-select").onchange = event => goToSection(event.target.value);
$("show-details").onclick = event => {
  const panel = $("paper-meta");
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
};
$("focus-reading").onclick = () => { state.ui.focus = !state.ui.focus; mobileLibrary(false); saveSettings(); };
$("reading-settings").onclick = () => $("settings-dialog").showModal();
$("close-settings").onclick = () => $("settings-dialog").close();
$("font-size").oninput = event => { state.ui.font_size = Number(event.target.value); saveSettings(); };
$("width-setting").onchange = event => { state.ui.reading_width = event.target.value; saveSettings(); };
$("open-note").onclick = async () => {
  if (!state.paper) return;
  try {
    const response = await fetch("/api/papers/" + state.paper.id + "/open-note", {method:"POST", headers:{"X-Reader-Action":"open-note"}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    notice("已交给本地 Markdown 编辑器。保存后可刷新笔记库。");
  } catch (error) { notice(error); }
};
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && document.body.classList.contains("library-open")) $("library-back").click();
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || document.querySelector("dialog[open]") || event.target.closest("input,textarea,select,[contenteditable=true]")) return;
  event.preventDefault();
  if (state.ui.focus) { state.ui.focus = false; saveSettings(); }
  if (matchMedia("(max-width:720px)").matches) mobileLibrary(true);
  $("library-search").focus();
});
scroll.addEventListener("scroll", () => {
  scheduleLocation();
  clearTimeout(positionTimer);
  positionTimer = setTimeout(rememberPosition, 120);
}, {passive:true});
addEventListener("resize", scheduleLocation);
addEventListener("beforeunload", rememberPosition);

async function start() {
  if (location.search.includes("desktop=1")) return;
  const browserIdentity = await getJson("/api/identity");
  if (state.desktop) return;
  storagePrefix = "reader-" + browserIdentity.workspace + ":";
  try { Object.assign(state.ui, JSON.parse(readLocal("preferences") || "{}")); } catch {}
  settings();
  await refresh();
}
start().catch(notice);

export {state, loadPaper, refresh, setMode, renderMarkdown, renderNotes, notice, rememberPosition, settings};
