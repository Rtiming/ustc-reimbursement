/* 只读元数据：不取 Cookie/存储/字段值，不点击、不保存、不提交。 */
(() => {
  const safeUrl = value => {
    try {
      const u = new URL(value, location.href);
      return u.origin + u.pathname.replace(/;jsessionid=[^;/]+/gi, ";jsessionid=[REDACTED]") +
        (u.search ? "?" + [...u.searchParams.keys()].map(k => encodeURIComponent(k) + "=[REDACTED]").join("&") : "");
    } catch (_) { return "[invalid-url]"; }
  };
  const sourceHints = source => ({
    functionNames: [...source.matchAll(/function\s+([A-Za-z_$][\w$]*)\s*\(/g)].map(m => m[1]).slice(0, 250),
    urlLiterals: [...source.matchAll(/["']([^"'\s]{0,180}(?:\.do|\.jsp|\.action|\.json|\.ashx)(?:\?[^"'\s]*)?)["']/g)]
      .map(m => safeUrl(m[1])).filter((v, i, a) => a.indexOf(v) === i).slice(0, 250)
  });
  const pages = [];
  const visit = (win, framePath, depth) => {
    try {
      const d = win.document;
      const scripts = [...d.scripts];
      pages.push({framePath, url: safeUrl(win.location.href), title: d.title,
        scripts: scripts.filter(s => s.src).map(s => ({url: safeUrl(s.src), path: new URL(s.src).pathname})),
        inlineHints: sourceHints(scripts.filter(s => !s.src).map(s => s.textContent).join("\n")),
        forms: [...d.forms].map(f => ({id: f.id, name: f.name, action: safeUrl(f.action), method: f.method})),
        fields: [...d.querySelectorAll("input,select,textarea")].map(e => ({tag: e.tagName, type: e.type,
          id: e.id, name: e.name, required: e.required, optionCount: e.options ? e.options.length : undefined})),
        runtimeVisibilityNote: "AppleScript executes in an isolated world; application globals need MAIN-world inspection",
        resources: win.performance.getEntriesByType("resource")
          .filter(r => ["xmlhttprequest", "fetch", "script", "iframe"].includes(r.initiatorType))
          .map(r => ({url: safeUrl(r.name), type: r.initiatorType})),
        frameCount: win.frames.length});
      if (depth < 3) for (let i = 0; i < win.frames.length; i++) visit(win.frames[i], framePath + "/" + i, depth + 1);
    } catch (e) { pages.push({framePath, inaccessible: true, error: String(e.name)}); }
  };
  if (location.hostname !== "cwzh.ustc.edu.cn") throw new Error("Only the USTC finance origin is allowed");
  visit(window, "top", 0);
  return JSON.stringify({mode: "read_only", capturedAt: new Date().toISOString(), pages});
})()
