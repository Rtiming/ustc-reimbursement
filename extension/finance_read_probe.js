/* 在财务页面的 MAIN world 执行；只读当前菜单和草稿摘要，不调用更新/审批函数。 */
(async () => {
  const output = document.getElementById("reimbursement-read-probe-result");
  const done = value => { if (output) output.textContent = JSON.stringify(value); return value; };
  try {
    if (location.origin !== "https://cwzh.ustc.edu.cn") throw new Error("Wrong origin");
    const jq = window.jQuery || window.$;
    if (!jq || !jq.page || !jq.UC) throw new Error("Finance session/runtime is unavailable");
    const page = jq.page.list[jq.page.idFunc.getCurrPageKey(jq.page.currPage)];
    const card = page && page.funcs && page.funcs.find(f => f.searchSql && f.funcno === "2");
    if (!card) throw new Error("Open the finance home page before running this read probe");
    const rows = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Menu query timed out")), 10000);
      // This is the read query used by the existing homepage, with its existing user context.
      jq.getDataBySqlId(card.searchSql, data => { clearTimeout(timer); resolve(data); });
    });
    const selected = Array.isArray(rows) ? rows.filter(r => /草稿|全部报销单|国内差旅/.test(JSON.stringify(r))) : [];
    // Menu definitions contain navigation IDs and account-local counts, no cookies or payment accounts.
    const result = {ok: true, mode: "read_only", capturedAt: new Date().toISOString(),
      projid: jq.getProjID(), currentPage: String(jq.page.currPage),
      endpoint: "/YBX/common_bindSQLDataBackend.action", sqlid: card.searchSql.split("|")[0].slice(1),
      rowCount: Array.isArray(rows) ? rows.length : null,
      rowKeys: Array.isArray(rows) && rows.length ? Object.keys(rows[0]) : [], selectedRows: selected};
    return done(result);
  } catch (e) { return done({ok: false, mode: "read_only", error: String(e.message)}); }
})()
