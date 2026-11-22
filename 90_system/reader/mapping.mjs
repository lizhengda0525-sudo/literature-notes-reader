export function splitNote(markdown) {
  const sections = [];
  let current = { title: "版本与阅读范围", parent: "", level: 2, lines: [], intro: true };
  let parent = "";
  function commit() {
    const body = current.lines.join("\n").trim();
    if (!body && current.intro) return;
    sections.push({ id: "note-" + sections.length, ...current, body });
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
