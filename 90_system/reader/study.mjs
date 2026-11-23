import {state, loadPaper, imageUrl, notice, rememberPosition, renderLibrary, mobileLibrary, labels} from "./reader.mjs";

const $ = id => document.getElementById(id);
const types = {figure: "图", table: "表", equation: "公式"};
const scopes = {main: "正文", supplement: "补充材料"};

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content != null) element.textContent = content;
  if (className) element.className = className;
  return element;
}

function button(label, action, className) {
  const element = node("button", label, className);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}

function numberLabel(item) {
  return item.kind === "equation" ? "式 (" + item.number + ")" : types[item.kind] + " " + item.number;
}

function sourceLabel(source) {
  if (!source) return "";
  return [scopes[source.scope], source.label].filter((value, i, all) => value && all.indexOf(value) === i).join(" · ");
}

export function initStudyViews(showReadMode) {
  let activeKind = "", selected = null, returnFocus = null, viewSerial = 0, catalog = [];
  const tabs = [...document.querySelectorAll(".view-tabs button")];

  function showView(name) {
    ++viewSerial;
    const previous = state.view;
    if (previous === "reading") rememberPosition();
    state.view = name;
    document.body.dataset.view = name;
    mobileLibrary(false);
    $("workspace-menu").open = false;
    for (const tab of tabs) {
      const active = tab.dataset.view === name;
      $(tab.dataset.view + "-view").hidden = !active;
      if (active) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    if (name === "reading" && previous !== name && state.paper && state.mode === "read") {
      $("note-scroll").scrollTop = Number(state.ui.positions?.[state.paper.id]) || 0;
    }
    if (name === "resources") renderResources();
    if (name === "workbench") renderWorkbench();
  }

  function openLibrary(filters = {}) {
    activeKind = filters.kind || "";
    $("resource-paper").value = filters.paper || "";
    $("resource-scope").value = filters.scope || "";
    $("resource-search").value = "";
    showView("resources");
    $("resources-view").scrollTop = 0;
    $("resource-search").focus({preventScroll: true});
  }

  function openPaperList(field, value) {
    showView("reading");
    showReadMode();
    if (state.ui.focus) $("focus-reading").click();
    if (selected) closeReference(false);
    for (const id of ["library-search", "status-filter", "rq-filter", "tag-filter"]) {
      $(id).value = id === field ? value : "";
    }
    $("filter-panel").open = Boolean(field);
    renderLibrary();
    if (matchMedia("(max-width:720px)").matches) mobileLibrary(true);
    else (field ? $(field) : $("library-search")).focus();
  }

  function closeReference(restoreFocus = true) {
    selected = null;
    $("reference-pane").hidden = true;
    document.body.classList.remove("has-reference");
    $("compare-resources").setAttribute("aria-expanded", "false");
    if (restoreFocus) {
      const target = returnFocus?.isConnected && returnFocus.offsetParent ? returnFocus : $("compare-resources");
      target.focus({preventScroll: true});
    }
    returnFocus = null;
  }

  function drawReference(paper, id) {
    const items = paper.resources?.items || [], sources = paper.resources?.sources || [];
    const index = items.findIndex(item => item.id === id);
    const item = items[index];
    if (!item) { closeReference(false); return false; }
    const source = sources.find(value => value.id === item.source);
    selected = {paper: paper.id, id: item.id};
    const picker = $("reference-select"), body = $("reference-body");
    picker.replaceChildren(...items.map(entry => new Option(numberLabel(entry) + "　" + entry.title, entry.id)));
    picker.value = id;
    const figure = node("figure");
    const url = imageUrl(item.image, paper.id);
    if (url) {
      const zoom = node("a", null, "reference-image");
      zoom.href = url;
      zoom.title = "放大原图";
      zoom.setAttribute("aria-label", "放大" + numberLabel(item) + "原图");
      const img = document.createElement("img");
      img.alt = numberLabel(item) + "　" + item.title;
      img.src = url;
      img.addEventListener("error", () => zoom.replaceWith(node("p", "本地原图暂时无法读取。", "study-empty")), {once:true});
      zoom.append(img);
      figure.append(zoom);
    }
    const origin = node("p", sourceLabel(source) + " · PDF 第 " + item.page + " 页", "reference-origin");
    const kicker = node("span", numberLabel(item), "resource-number");
    body.replaceChildren(kicker, node("h3", item.title), origin, figure);
    if (item.description) body.append(node("p", item.description, "reference-description"));
    body.scrollTop = 0;
    $("reference-position").textContent = (index + 1) + " / " + items.length;
    $("reference-prev").disabled = index === 0;
    $("reference-next").disabled = index === items.length - 1;
    $("compare-resources").setAttribute("aria-expanded", "true");
    $("reference-pane").hidden = false;
    document.body.classList.add("has-reference");
    return true;
  }

  async function openReference(pid, id, trigger) {
    const ticket = ++viewSerial;
    if (state.paper?.id !== pid && !await loadPaper(pid)) return;
    if (ticket !== viewSerial || pid !== state.paper?.id) return;
    if (!state.paper.resources?.items?.some(item => item.id === id)) {
      notice("这项资料尚未收录到该论文的资料库。");
      return;
    }
    const anchor = trigger?.closest("#note-content a") || null;
    const oldTop = anchor?.getBoundingClientRect().top;
    if (state.view !== "reading") showView("reading");
    if (state.mode !== "read") showReadMode();
    returnFocus = trigger || $("compare-resources");
    drawReference(state.paper, id);
    if (anchor?.isConnected) $("note-scroll").scrollTop += anchor.getBoundingClientRect().top - oldTop;
    $("reference-select").focus({preventScroll:true});
  }

  function stepReference(delta) {
    const items = state.paper?.resources?.items || [];
    const index = items.findIndex(item => item.id === selected?.id);
    if (items[index + delta]) drawReference(state.paper, items[index + delta].id);
  }

  function renderCoverage(papers, scope) {
    const container = $("source-coverage"), records = [], gaps = [];
    container.replaceChildren();
    for (const paper of papers) {
      const resources = paper.resources || {};
      if (resources.error) {
        gaps.push(paper.id + " · " + resources.error);
        continue;
      }
      if (!resources.sources?.length) {
        gaps.push(paper.id + " · 尚未登记资料");
        continue;
      }
      for (const source of resources.sources) {
        if (scope && source.scope !== scope) continue;
        const status = {available: "已归档", unavailable: "待取得", none: "原文未提供"}[source.status] || "";
        const message = [paper.id, sourceLabel(source), status, source.note].filter(Boolean).join(" · ");
        (source.status === "unavailable" ? gaps : records).push(message);
      }
    }
    if (!records.length && !gaps.length) return;
    const details = node("details", null, "coverage-details");
    details.append(node("summary", gaps.length ? "收录范围 · " + gaps.length + " 项待补" : "查看收录范围"));
    const list = node("ul");
    for (const message of [...gaps, ...records]) list.append(node("li", message));
    details.append(list);
    container.append(details);
  }

  function renderResources() {
    const pid = $("resource-paper").value, scope = $("resource-scope").value;
    const words = $("resource-search").value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const papers = state.papers.filter(paper => !pid || paper.id === pid);
    const shown = catalog.filter(({paper, item, source, search}) =>
      (!pid || paper.id === pid) && (!scope || source?.scope === scope) &&
      (!activeKind || item.kind === activeKind) && words.every(word => search.includes(word)));
    for (const tab of $("resource-kinds").querySelectorAll("button")) {
      tab.setAttribute("aria-pressed", String(tab.dataset.kind === activeKind));
    }
    $("resource-total").textContent = (shown.length === catalog.length ? catalog.length : shown.length + " / " + catalog.length) + " 项资料";
    renderCoverage(papers, scope);
    const results = $("resource-results");
    results.replaceChildren();
    if (!shown.length) {
      results.append(node("p", catalog.length ? "没有符合当前筛选条件的资料。" : "项目中还没有已收录的图、表和公式。", "study-empty"));
      if (pid || scope || activeKind || words.length) results.append(button("清除筛选", () => openLibrary()));
      return;
    }
    let group, groupId;
    for (const {paper, item, source} of shown) {
      if (paper.id !== groupId) {
        groupId = paper.id;
        const section = node("section", null, "resource-group");
        const heading = node("h2");
        heading.append(node("span", paper.id, "paper-code"), document.createTextNode(paper.title));
        group = node("div", null, "resource-list");
        section.append(heading, group);
        results.append(section);
      }
      const row = button("", event => openReference(paper.id, item.id, event.currentTarget).catch(notice), "resource-row");
      row.dataset.resourceId = item.id;
      row.dataset.paperId = paper.id;
      row.title = "打开论文并在旁边对照";
      const info = node("span", null, "resource-row-title");
      info.append(node("strong", item.title), node("small", sourceLabel(source) + " · 第 " + item.page + " 页"));
      row.append(node("span", numberLabel(item), "resource-number"), info, node("span", "对照阅读 ↗", "row-action"));
      group.append(row);
    }
  }

  function histogram(field, filterId, title) {
    const counts = new Map();
    for (const paper of state.papers) {
      for (const value of new Set(paper[field] || [])) counts.set(value, (counts.get(value) || 0) + 1);
    }
    const panel = node("section", null, "workbench-panel"), list = node("div", null, "topic-list");
    panel.append(node("h2", title));
    if (!counts.size) { panel.append(node("p", "暂无记录。", "study-muted")); return panel; }
    for (const [value, n] of [...counts].sort((a,b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
      const action = button("", () => openPaperList(filterId, value), "topic-chip");
      const question = field === "research_questions" && state.questionTitles?.[value];
      action.append(document.createTextNode(question ? value + " · " + question : value), node("span", String(n)));
      action.title = "查看关联论文";
      list.append(action);
    }
    panel.append(list);
    return panel;
  }

  function renderWorkbench() {
    const container = $("workbench-content");
    container.replaceChildren();
    $("workbench-project").textContent = $("project-name").textContent;
    const paper = state.paper, papers = state.papers;
    if (paper) {
      const resume = button("", () => { showView("reading"); $("note-scroll").focus({preventScroll:true}); }, "resume-card");
      resume.id = "resume-reading";
      const copy = node("span", null, "resume-copy");
      copy.append(node("small", "上次阅读 · " + paper.id), node("strong", paper.title));
      resume.append(copy, node("span", "继续阅读 →", "resume-action"));
      container.append(resume);
    }
    const stats = node("div", null, "stats-grid");
    const metrics = [["论文", papers.length, () => openPaperList("", "")],
      ...Object.entries(types).map(([kind, label]) => [label, catalog.filter(entry => entry.item.kind === kind).length, () => openLibrary({kind})])];
    for (const [label, count, open] of metrics) {
      const metric = button("", open, "metric");
      metric.append(node("span", label), node("strong", String(count)));
      stats.append(metric);
    }
    const progress = node("section", null, "workbench-panel"), rows = node("div", null, "status-list");
    progress.append(node("h2", "阅读进度"));
    for (const [value, label] of Object.entries(labels)) {
      const count = papers.filter(entry => entry.reading_status === value).length;
      const row = button("", () => openPaperList("status-filter", value), "status-row");
      const meter = node("span", null, "status-track"), fill = node("span");
      fill.style.width = (papers.length ? count * 100 / papers.length : 0) + "%";
      meter.append(fill);
      row.append(node("span", label), meter, node("span", count + " 篇", "status-value"));
      rows.append(row);
    }
    progress.append(rows);
    const panels = node("div", null, "workbench-panels");
    panels.append(progress, histogram("tags", "tag-filter", "方法标签"), histogram("research_questions", "rq-filter", "研究问题"));
    container.append(stats, panels);
  }

  function catalogChanged() {
    catalog = [];
    for (const paper of state.papers) {
      const sources = new Map((paper.resources?.sources || []).map(value => [value.id, value]));
      for (const item of paper.resources?.items || []) {
        const source = sources.get(item.source);
        const search = [numberLabel(item), item.kind, item.number, item.title, item.description, sourceLabel(source),
          paper.id, paper.title, ...paper.authors, ...paper.tags].filter(Boolean).join(" ").toLocaleLowerCase();
        catalog.push({paper, item, source, search});
      }
    }
    const picker = $("resource-paper"), oldPaperId = picker.value;
    picker.replaceChildren(new Option("全部论文", ""), ...state.papers.map(p => new Option(p.id + " · " + p.title, p.id)));
    picker.value = state.papers.some(p => p.id === oldPaperId) ? oldPaperId : "";
    if (state.view === "resources") renderResources();
    if (state.view === "workbench") renderWorkbench();
  }

  function paperChanged() {
    const paper = state.paper;
    $("compare-resources").disabled = !paper;
    if (paper && selected?.paper === paper.id) drawReference(paper, selected.id);
    else closeReference(false);
    if (state.view === "workbench") renderWorkbench();
  }

  function projectChanged() {
    activeKind = "";
    for (const id of ["resource-paper", "resource-scope", "resource-search"]) $(id).value = "";
    closeReference(false);
    showView("reading");
  }

  for (const tab of tabs) tab.addEventListener("click", () => showView(tab.dataset.view));
  for (const id of ["resource-paper", "resource-scope"]) $(id).addEventListener("change", renderResources);
  $("resource-search").addEventListener("input", renderResources);
  $("resource-kinds").addEventListener("click", event => {
    const tab = event.target.closest("button");
    if (tab) { activeKind = tab.dataset.kind; renderResources(); }
  });
  $("reference-select").addEventListener("change", event => drawReference(state.paper, event.target.value));
  $("reference-prev").addEventListener("click", () => stepReference(-1));
  $("reference-next").addEventListener("click", () => stepReference(1));
  $("close-reference").addEventListener("click", () => closeReference());
  $("compare-resources").addEventListener("click", event => {
    if (selected) { closeReference(); return; }
    const paper = state.paper, first = paper?.resources?.items?.[0];
    if (first) openReference(paper.id, first.id, event.currentTarget).catch(notice);
    else if (paper) openLibrary({paper: paper.id});
  });
  $("reference-library").addEventListener("click", () => openLibrary({paper: state.paper.id}));
  document.addEventListener("keydown", event => {
    if (state.view !== "reading" || document.querySelector("dialog[open]") || $("workspace-menu").open) return;
    if (event.key === "Escape" && selected && !document.body.classList.contains("library-open")) closeReference();
  });
  return {openReference, projectChanged, paperChanged, catalogChanged};
}
