import {evidenceIds} from "./mapping.mjs";

const $ = id => document.getElementById(id);
const origins = {author: "作者陈述", analysis: "分析判断", idea: "候选想法"};

export function versionName(paper, id) {
  return paper.versions.find(item => item.id === id)?.label || id;
}

export function openEvidence(paper, rows, context = "本篇全部证据") {
  const list = $("evidence-rows");
  list.replaceChildren();
  $("evidence-paper").textContent = paper.id;
  $("evidence-context").textContent = context + " · " + rows.length + " 条";
  for (const row of rows) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "evidence-label";
    label.textContent = [row.id.split(":").pop(), versionName(paper, row.version), "PDF 第 " + row.page + " 页"].join("  /  ");
    const claim = document.createElement("p");
    claim.textContent = row.claim;
    const locator = document.createElement("p");
    locator.className = "evidence-location";
    const reviewer = {ai: "AI 核查", user: "用户核查"}[row.checked_by];
    locator.textContent = [origins[row.kind] || row.kind, reviewer, row.locator].filter(Boolean).join(" · ");
    item.append(label, claim);
    if (row.excerpt) {
      const quote = document.createElement("blockquote");
      quote.textContent = row.excerpt;
      item.append(quote);
    }
    item.append(locator);
    list.append(item);
  }
  $("evidence-dialog").showModal();
  list.scrollTop = 0;
}

export function linkEvidence(body, paper) {
  const byId = new Map(paper.evidence.map(row => [row.id, row]));
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.data.includes(paper.id + ":E") && !node.parentElement.closest("a, code, pre, button, .katex")) nodes.push(node);
  }
  for (const node of nodes) {
    const pattern = /P\d{4,}:E\d{3,}(?:[–—-]E\d{3,})?/g;
    const fragment = document.createDocumentFragment();
    let end = 0;
    for (const match of node.data.matchAll(pattern)) {
      const ids = evidenceIds(match[0], paper.id);
      if (!ids.length || !ids.every(id => byId.has(id))) continue;
      fragment.append(node.data.slice(end, match.index));
      const button = document.createElement("button");
      button.className = "cite";
      button.textContent = match[0].slice(paper.id.length + 1);
      button.title = "查看证据 " + match[0];
      button.onclick = () => openEvidence(paper, ids.map(id => byId.get(id)), "正文引用 " + button.textContent);
      fragment.append(button);
      end = match.index + match[0].length;
    }
    if (end) {
      fragment.append(node.data.slice(end));
      node.replaceWith(fragment);
    }
  }
}

function renderBars(paper) {
  const version = paper.versions.find(item => item.id === $("map-version").value);
  if (!version) return;
  const groups = Array.from({length: version.pages}, () => []);
  for (const row of paper.evidence) {
    if (row.version === version.id) groups[row.page - 1]?.push(row);
  }
  // Keep long documents legible by grouping adjacent physical pages.
  const step = Math.max(1, Math.ceil(groups.length / 24));
  const bins = [];
  for (let i = 0; i < groups.length; i += step) bins.push(groups.slice(i, i + step).flat());
  const max = Math.max(1, ...bins.map(rows => rows.length));
  const bars = $("map-bars");
  bars.replaceChildren();
  bins.forEach((rows, index) => {
    const first = index * step + 1;
    const last = Math.min(first + step - 1, groups.length);
    const pages = "PDF 第 " + first + (first === last ? "" : "–" + last) + " 页";
    const label = version.label + " · " + pages;
    const button = document.createElement("button");
    button.className = "map-bar";
    button.disabled = !rows.length;
    button.title = label + "，" + rows.length + " 条证据";
    button.setAttribute("aria-label", button.title);
    const bar = document.createElement("span");
    bar.style.height = Math.round(100 * rows.length / max) + "%";
    button.append(bar);
    button.onclick = () => openEvidence(paper, rows, label);
    bars.append(button);
  });
  $("map-last").textContent = "第 " + version.pages + " 页";
}

export function bindEvidence(paper) {
  $("show-evidence").textContent = "原文证据 · " + paper.evidence.length;
  $("show-evidence").disabled = !paper.evidence.length;
  $("show-evidence").onclick = () => openEvidence(paper, paper.evidence);
  $("page-map").hidden = !paper.evidence.length;
  const select = $("map-version");
  select.replaceChildren(...paper.versions.map(v => new Option(v.label, v.id)));
  select.value = paper.current_version;
  select.onchange = () => renderBars(paper);
  renderBars(paper);
}

$("close-evidence").onclick = () => $("evidence-dialog").close();
