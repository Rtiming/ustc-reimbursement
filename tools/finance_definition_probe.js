/* 读取菜单/按钮定义中已经确认的窗口33、41、70，不打开或执行按钮。 */
(async () => {
  const output = document.getElementById("reimbursement-read-probe-result");
  const done = value => { if (output) output.textContent = JSON.stringify(value); return value; };
  try {
    if (location.origin !== "https://cwzh.ustc.edu.cn") throw new Error("Wrong origin");
    const jq = window.jQuery || window.$;
    if (!jq || !jq.page) throw new Error("Finance session/runtime is unavailable");
    const definitions = await Promise.all(["33", "41", "70"].map(winno => new Promise((resolve, reject) => {
      jq.ajax({type: "POST", url: "loadDefinition.action?type=W", timeout: 10000,
        data: {winno, projid: jq.getProjID()}, dataType: "json",
        success: data => resolve({winno, definition: data}), error: (_xhr, status) => reject(new Error(status))});
    })));
    return done({ok: true, mode: "read_only", capturedAt: new Date().toISOString(),
      endpoint: "/YBX/loadDefinition.action?type=W", definitions});
  } catch (e) { return done({ok: false, mode: "read_only", error: String(e.message)}); }
})()
