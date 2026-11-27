import {marked} from "./vendor/marked/marked.esm.js";
import {splitNote} from "./mapping.mjs";

const $ = id => document.getElementById(id);
const state = {papers: [], paper: null, request: 0, mode: "read", view: "reading", ui: {positions: {}}};
const labels = {queued: "待阅读", skimmed: "已速读", deep_read: "已精读", skipped: "已跳过"};
const verification = {reviewed: "用户已核对", partial: "部分核对", unreviewed: "用户待核对"};
const scroll = $("note-scroll");
let noticeTimer, positionTimer, scrollFrame, catalogRequest = 0;
let sectionNodes = [], activeSection = null;
let drawer = null, anchorFrame, panelTimer;
const systemDark = matchMedia("(prefers-color-scheme: dark)");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
function notice(value) {
  $("notice").textContent = value?.message || String(value);
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6500);
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
function floating(side) {
  if (state.ui.focus || state.mode === "edit") return true;
  if (side === "outline") return innerWidth < 1060;
  return innerWidth <= 720 || (innerWidth < 1160 && document.body.classList.contains("has-reference"));
}

function updatePanels() {
  const body = document.body, reading = state.view === "reading";
  const compare = body.classList.contains("has-reference");
  for (const side of ["library", "outline"]) {
    const allowed = reading && (side === "library" || (state.mode === "read" && !!state.paper));
    const floats = floating(side), displaced = side === "outline" && compare;
    if (drawer === side && (!allowed || !floats || displaced)) drawer = null;
    const layout = !allowed || displaced ? "closed" : floats ? (drawer === side ? "drawer" : "closed")
      : state.ui[side + "_visible"] === false ? "closed" : "docked";
    body.dataset[side + "Layout"] = layout;
    const panel = $(side === "library" ? "library" : "outline-pane"), toggle = $(side + "-toggle");
    panel.inert = layout === "closed";
    panel.setAttribute("aria-hidden", String(panel.inert));
    toggle.setAttribute("aria-expanded", String(!panel.inert));
    const label = (panel.inert ? "展开" : "收起") + (side === "library" ? "论文库" : "本篇目录");
    toggle.title = label;
    toggle.setAttribute("aria-label", label);
    toggle.disabled = !allowed;
  }
  $("panel-backdrop").hidden = !drawer;
  scheduleLocation();
}

function closeDrawer(restoreFocus = false) {
  if (!drawer) return false;
  const side = drawer;
  drawer = null;
  updatePanels();
  if (restoreFocus) $(side + "-toggle").focus({preventScroll:true});
  return true;
}

function setPanel(side, open) {
  if (side === "outline" && open && document.body.classList.contains("has-reference")) $("close-reference").click();
  cancelAnimationFrame(anchorFrame);
  clearTimeout(panelTimer);
  $("reading-view").classList.add("adjusting-panels");
  panelTimer = setTimeout(() => $("reading-view").classList.remove("adjusting-panels"), 280);
  const top = scroll.getBoundingClientRect().top;
  const anchor = scroll.scrollTop > 12 && state.mode === "read" &&
    [...$("note-content").querySelectorAll("h2,h3,p,li,tr")].find(node => {
      const rect = node.getBoundingClientRect();
      return rect.height && rect.bottom > top + 16;
    });
  const offset = anchor && anchor.getBoundingClientRect().top;
  if (floating(side)) drawer = open ? side : null;
  else {
    drawer = null;
    state.ui[side + "_visible"] = open;
    window.readerDesktop?.persistUi();
  }
  updatePanels();
  const until = performance.now() + (reducedMotion.matches ? 0 : 300);
  function holdPosition() {
    if (anchor?.isConnected) scroll.scrollTop += anchor.getBoundingClientRect().top - offset;
    if (performance.now() < until) anchorFrame = requestAnimationFrame(holdPosition);
  }
  if (anchor) anchorFrame = requestAnimationFrame(holdPosition);
}

function revealLibrary() {
  setPanel("library", true);
  $("library-search").focus({preventScroll:true});
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
        closeDrawer(true);
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
  if (!state.paper || state.mode !== "read" || state.view !== "reading") return;
  const id = state.paper.id;
  state.ui.positions ??= {};
  state.ui.positions[id] = scroll.scrollTop;
  window.readerDesktop?.persistUi();
}
function positionFor(id) {
  return Number(state.ui.positions?.[id]) || 0;
}
function trackLocation() {
  const reading = state.mode === "read" && state.view === "reading" && !!state.paper;
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
  scroll.hidden = mode === "edit";
  $("mode-read").classList.toggle("active", mode === "read");
  $("mode-read").setAttribute("aria-pressed", String(mode === "read"));
  $("outline").hidden = mode !== "read";
  $("view-status").textContent = ({read: "精读笔记", edit: "编辑 Markdown"})[mode];
  if (previous !== mode && state.paper) scroll.scrollTop = mode === "read" ? positionFor(state.paper.id) : 0;
  updatePanels();
  trackLocation();
}
function imageUrl(relative, paperId) {
  let path;
  try { path = decodeURIComponent(relative).replace(/^\.\//, ""); }
  catch { return null; }
  if (!/^images\/[^/\\:?#\x00-\x1f]+\.(?:png|jpe?g|webp)$/i.test(path)) return null;
  return "/paper/" + paperId + "/images/" + encodeURIComponent(path.slice(7));
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
  for (const img of body.querySelectorAll("img")) {
    const relative = img.getAttribute("src") || "";
    const src = imageUrl(relative, paper.id);
    if (src) {
      img.src = src;
      img.loading = "lazy";
      if (!img.closest("a")) {
        const zoom = document.createElement("a");
        zoom.setAttribute("href", relative);
        zoom.title = "放大原图";
        img.replaceWith(zoom);
        zoom.append(img);
      }
    } else { img.removeAttribute("src"); img.alt = "图片不在本篇档案中"; }
  }
  for (const link of body.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (/^#res-[a-z0-9][a-z0-9-]*$/.test(href)) {
      link.classList.add("resource-link");
      link.title = "在旁边对照阅读";
      continue;
    }
    const image = imageUrl(href, paper.id);
    if (image) link.href = image;
    else if (paper.versions.some(v => v.file === href)) {
      link.replaceWith(document.createTextNode(link.textContent + "（在 Zotero 阅读）"));
      continue;
    } else if (!/^https?:/i.test(href)) { link.removeAttribute("href"); continue; }
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  return body;
}
function renderNotes() {
  const paper = state.paper;
  const sections = splitNote(paper.note);
  $("note-content").replaceChildren();
  $("outline").replaceChildren();
  sectionNodes = [];
  activeSection = null;
  for (const section of sections) {
    const title = ({"摘要中译":"摘要", "结论中译":"结论"})[section.title] || section.title;
    const card = document.createElement("section");
    card.id = section.id;
    card.className = "note-section";
    if (section.intro) card.classList.add("note-intro");
    if (title === "摘要") card.classList.add("note-summary");
    if (!section.body) card.classList.add("section-heading");
    const body = prepareBody(section, paper);
    if (section.intro) {
      const details = document.createElement("details"), summary = document.createElement("summary");
      summary.textContent = title;
      details.append(summary, body);
      card.append(details);
    } else {
      const heading = document.createElement(section.level === 3 ? "h3" : "h2");
      heading.textContent = title;
      card.append(heading, body);
    }
    const button = document.createElement("button");
    button.className = "outline-link" + (section.level === 3 ? " sub" : "");
    button.textContent = title;
    button.onclick = () => { closeDrawer(true); goToSection(card.id); };
    $("outline").append(button);
    $("note-content").append(card);
    sectionNodes.push({card, button});
  }
}
function renderHeading(paper) {
  $("paper-id").textContent = paper.id;
  const edition = paper.versions.find(v => v.id === paper.current_version);
  $("paper-edition").textContent = [edition?.label || paper.current_version, labels[paper.reading_status] || paper.reading_status, verification[paper.verification_status] || paper.verification_status].join("  /  ");
  $("paper-title").textContent = paper.title;
  $("paper-byline").textContent = paper.authors.join(" · ");
  $("paper-journal").textContent = [paper.year, paper.journal].filter(Boolean).join("  /  ");
  const hasMetadataSection = /^## 论文信息[ \t]*$/m.test(paper.note);
  $("paper-byline").hidden = hasMetadataSection;
  $("paper-journal").hidden = hasMetadataSection;
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
  $("open-note").disabled = false;
}
async function loadPaper(id) {
  if (window.readerDesktop && !await window.readerDesktop.beforeNavigate()) return false;
  rememberPosition();
  clearTimeout(positionTimer);
  const request = ++state.request;
  const paper = await window.readerDesktop.readPaper(id);
  if (request !== state.request) return false;
  const position = positionFor(id);
  state.paper = paper;
  state.ui.last_paper = paper.id;
  $("reading-page").hidden = false;
  $("empty-reading").hidden = true;
  renderHeading(paper);
  renderNotes();
  renderLibrary();
  setMode(state.mode);
  scroll.scrollTop = state.mode === "read" ? position : 0;
  trackLocation();
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
  $("zotero-link").disabled = true;
  $("zotero-link").textContent = "打开 Zotero";
  $("open-note").disabled = true;
  $("note-path").textContent = "";
  $("outline").replaceChildren();
  if ($("mode-edit")) $("mode-edit").disabled = true;
  if ($("editor-panel")) $("editor-panel").hidden = true;
  if ($("recovery-banner")) $("recovery-banner").hidden = true;
  setMode("read");
  window.readerDesktop?.paperChanged?.();
  document.title = "精读笔记库";
}
async function refresh() {
  const request = ++catalogRequest;
  rememberPosition();
  const catalog = await window.readerDesktop.listPapers();
  if (request !== catalogRequest) return;
  const papers = Array.isArray(catalog) ? catalog : catalog.papers;
  state.questionTitles = catalog.question_titles || {};
  state.papers = papers;
  window.readerDesktop?.catalogChanged?.();
  for (const [id, field, title] of [["rq-filter", "research_questions", "全部问题"], ["tag-filter", "tags", "全部标签"]]) {
    const select = $(id), previous = select.value;
    const values = [...new Set(papers.flatMap(p => p[field]))].sort();
    const label = value => field === "research_questions" && state.questionTitles[value]
      ? value + " · " + state.questionTitles[value] : value;
    select.replaceChildren(new Option(title, ""), ...values.map(v => new Option(label(v), v)));
    select.value = values.includes(previous) ? previous : "";
  }
  renderLibrary();
  const target = papers.find(p => p.id === (state.paper?.id || state.ui.last_paper)) || papers[0];
  if (target) await loadPaper(target.id);
  else clearPaper();
}

function settings() {
  const preference = ["system", "light", "topaz"].includes(state.ui.theme) ? state.ui.theme : "topaz";
  const theme = preference === "system" ? (systemDark.matches ? "topaz" : "light") : preference;
  document.documentElement.dataset.theme = theme;
  window.readerDesktop?.titleTheme?.(theme);
  $("topaz-theme").disabled = theme !== "topaz";
  $("theme-setting").value = preference;
  const tint = Math.max(0, Math.min(100, Number(state.ui.glass_tint ?? 55)));
  document.documentElement.style.setProperty("--glass-opacity", (0.38 + tint * 0.0062).toFixed(2));
  $("glass-tint").value = tint;
  $("tint-value").value = tint + "%";
  const size = Math.max(13, Math.min(24, Number(state.ui.font_size) || 16));
  document.documentElement.style.setProperty("--note-font", size + "px");
  document.body.classList.toggle("reading-wide", state.ui.reading_width === "wide");
  document.body.classList.toggle("focus-mode", !!state.ui.focus);
  $("font-size").value = size;
  $("font-value").value = size + " px";
  $("width-setting").value = state.ui.reading_width || "normal";
  $("focus-reading").setAttribute("aria-pressed", String(!!state.ui.focus));
  updatePanels();
}
function saveSettings() {
  settings();
  window.readerDesktop?.persistUi();
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
for (const side of ["library", "outline"]) {
  $(side + "-toggle").onclick = event => {
    const open = event.currentTarget.getAttribute("aria-expanded") !== "true";
    setPanel(side, open);
    if (open && drawer) {
      const target = side === "library" ? $("library-search") : $("outline").querySelector("[aria-current],button");
      target?.focus({preventScroll:true});
    }
  };
}
$("panel-backdrop").onclick = () => closeDrawer(true);
$("refresh").onclick = () => refresh().catch(notice);
$("mode-read").onclick = () => setMode("read");
$("show-details").onclick = event => {
  const panel = $("paper-meta");
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
};
$("focus-reading").onclick = () => { drawer = null; state.ui.focus = !state.ui.focus; saveSettings(); };
$("reading-settings").onclick = () => $("settings-dialog").showModal();
$("close-settings").onclick = () => $("settings-dialog").close();
$("theme-setting").onchange = event => { state.ui.theme = event.target.value; saveSettings(); };
$("glass-tint").oninput = event => { state.ui.glass_tint = Number(event.target.value); saveSettings(); };
$("font-size").oninput = event => { state.ui.font_size = Number(event.target.value); saveSettings(); };
$("width-setting").onchange = event => { state.ui.reading_width = event.target.value; saveSettings(); };
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("dialog[open]") && closeDrawer(true)) {
    event.preventDefault();
    return;
  }
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || document.querySelector("dialog[open]") || event.target.closest("input,textarea,select,[contenteditable=true]")) return;
  if (state.view === "resources") { event.preventDefault(); $("resource-search").focus(); return; }
  if (state.view !== "reading") return;
  event.preventDefault();
  revealLibrary();
});
scroll.addEventListener("scroll", () => {
  scheduleLocation();
  clearTimeout(positionTimer);
  positionTimer = setTimeout(rememberPosition, 120);
}, {passive:true});
for (const event of ["wheel","touchstart","pointerdown","keydown"]) scroll.addEventListener(event, () => cancelAnimationFrame(anchorFrame), {passive:true});
addEventListener("resize", updatePanels);
systemDark.addEventListener("change", settings);
addEventListener("beforeunload", rememberPosition);

export {state, loadPaper, refresh, setMode, renderMarkdown, renderNotes, imageUrl, notice, rememberPosition, settings, renderLibrary, closeDrawer, revealLibrary, updatePanels, labels};
