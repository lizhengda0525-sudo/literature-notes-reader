export function evidenceIds(text, paperId) {
  const ids = new Set();
  const pattern = /P\d{4,}:E(\d{3,})(?:[–—-]E(\d{3,}))?/g;
  for (const match of text.matchAll(pattern)) {
    if (!match[0].startsWith(paperId + ":")) continue;
    const start = Number(match[1]), end = Number(match[2] || start);
    if (end < start || end - start > 1000) continue;
    for (let n = start; n <= end; n++) ids.add(paperId + ":E" + String(n).padStart(3, "0"));
  }
  return [...ids];
}

export function splitNote(markdown, paperId, evidence) {
  const byId = new Map(evidence.map(item => [item.id, item]));
  const sections = [];
  let current = { title: "版本与阅读范围", parent: "", level: 2, lines: [], intro: true };
  let parent = "";
  function commit() {
    const body = current.lines.join("\n").trim();
    if (!body && current.intro) return;
    const refs = new Map();
    for (const id of evidenceIds(body, paperId)) {
      const item = byId.get(id);
      if (item) refs.set(item.version + ":" + item.page, { version: item.version, page: item.page });
    }
    sections.push({ id: "note-" + sections.length, ...current, body, refs: [...refs.values()] });
  }
  let fence = "";
  for (const line of markdown.split(/\r?\n/)) {
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1].startsWith(fence) && !marker[2].trim()) fence = "";
      current.lines.push(line);
      continue;
    }
    const heading = !fence && /^(#{2,3}) (.+)$/.exec(line);
    if (heading) {
      commit();
      const level = heading[1].length;
      if (level === 2) parent = heading[2];
      current = { title: heading[2], parent: level === 3 ? parent : "", level, lines: [] };
    } else if (fence || !current.intro || current.lines.length || !line.startsWith("# ")) {
      current.lines.push(line);
    }
  }
  commit();
  return sections;
}

export function sectionMatches(section, version, page, mode, query = "") {
  const relevant = section.refs.some(ref => ref.version === version && ref.page === page);
  const matches = (section.title + "\n" + section.body).toLocaleLowerCase()
    .includes(query.trim().toLocaleLowerCase());
  return matches && (mode === "all" || relevant);
}
