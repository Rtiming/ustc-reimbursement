"use strict";
(() => {
  const $ = (s) => document.querySelector(s),
    esc = (v) =>
      String(v ?? "").replace(
        /[&<>"']/g,
        (c) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          })[c],
      );
  const offline = window.REVIEW_EXPORT || null;
  const drafts = {},
    pendingRequests = {};
  let advanceAfterReply = true;
  const viewId = crypto.randomUUID();
  let viewTimer, closingPage = false;
  function sendView(action, keepalive=false) {
    if(offline) return Promise.resolve();
    return request("/api/views", {method:"POST",keepalive,body:JSON.stringify({view_id:viewId,action})});
  }
  function trackView() {
    if(offline || viewTimer) return;
    sendView("ping").catch(()=>{});
    viewTimer=setInterval(()=>sendView("ping").catch(()=>{}),20000);
  }

  let draftDB, draftScope, storageError = null, storageWrites = 0;
  let storageQueue = Promise.resolve();
  async function openDraftStore() {
    if (offline || draftScope) return;
    draftScope = JSON.stringify([data.batch_id, data.roots]);
    try {
      draftDB = await new Promise((resolve, reject) => {
        const req = indexedDB.open("ustc-material-review-drafts", 1);
        req.onupgradeneeded = () => req.result.createObjectStore("workspaces");
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
      const saved = await new Promise((resolve, reject) => {
        const req = draftDB.transaction("workspaces").objectStore("workspaces").get(draftScope);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
      if (saved) {
        advanceAfterReply = saved.advanceAfterReply !== false;
        Object.assign(drafts, saved.drafts);
        Object.assign(pendingRequests, saved.pendingRequests);
        if (saved.version !== version) {
          for (const draft of Object.values(drafts)) {
            draft.checks = []; draft.evidence = [];
            if (draft.action === "review") draft.action = "reply";
          }
        }
      }
    } catch (e) {
      storageError = e;
      notice("本浏览器草稿存储不可用，请保留页面，暂勿关闭。" + e.message, true);
    }
  }
  function persistDrafts() {
    if (offline || !draftScope) return Promise.resolve();
    const snapshot = structuredClone({ version, drafts, pendingRequests, advanceAfterReply });
    storageWrites++;
    storageQueue = storageQueue.catch(() => {}).then(() => new Promise((resolve, reject) => {
      if (!draftDB) return reject(storageError || Error("草稿存储未就绪"));
      const tx = draftDB.transaction("workspaces", "readwrite");
      tx.objectStore("workspaces").put(snapshot, draftScope);
      tx.oncomplete = resolve;
      tx.onabort = tx.onerror = () => reject(tx.error || Error("草稿保存失败"));
    })).then(() => { storageError = null; }, (e) => {
      storageError = e;
      notice("本浏览器草稿保存失败，请保留页面。" + e.message, true);
      throw e;
    }).finally(() => { storageWrites--; });
    // Input handlers are deliberately non-blocking; saveRecord explicitly awaits this queue.
    storageQueue.catch(() => {});
    return storageQueue;
  }
  let data,
    version,
    record,
    assets,
    workflow = { available: false },
    preflights = {},
    feedback = { rows: [], counts: {}, open_count: 0 },
    ui = { item: null, view: "inbox", issue: null, asset: null, page: 1 },
    busy = false;
  const statusLabel = {
    pending: "待回复 / 待核实",
    replied: "已回复/补件待复核",
    reviewed: "材料已复核",
    waiting_materials: "答复已处理，等待材料",
    before_submit: "提交前补齐",
    hold: "按安排暂缓",
  };
  const actionLabel = {
    note: "经办备注",
    reply: "收到答复",
    attachment: "收到补件",
    review: "材料复核记录",
    reopen: "重新核查",
    defer: "登记后续待办",
    link: "关联证据",
    answers_imported: "Excel答复读回",
  };
  function notice(text, error = false) {
    $("#notice").textContent = text;
    $("#notice").className = "show" + (error ? " error" : "");
  }
  async function request(path, options = {}) {
    if (offline) {
      if (path === "/api/data") return offline;
      throw Error("此材料包为只读快照；补件请交经办人或使用经办接续包。");
    }
    const r = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        "X-Review-Action": "1",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    const j = await r.json();
    if (!r.ok) {
      const e = Error(j.error || "读取失败");
      e.confirmedRejection = true;
      throw e;
    }
    return j;
  }
  function badge(text, tone = "") {
    return '<span class="badge ' + tone + '">' + esc(text) + "</span>";
  }
  function amount(v) {
    return v == null || v === "" ? "待确认" : esc(v);
  }
  function originalUrl(a) {
    return offline
      ? assets[a].path.split("/").map(encodeURIComponent).join("/")
      : "/api/original/" + a;
  }
  function url(a, page = 1, box = null, highlight = null) {
    if (offline)
      return (
        offline.previews[a + ":" + page + (box ? ":" + box.join(",") : highlight ? ":marked:"+highlight.join(",") : "")] ||
        offline.previews[a + ":" + page] ||
        "data:,"
      );
    return (
      "/api/preview/" +
      a +
      "?page=" +
      page +
      (box ? "&box=" + box.join(",") : highlight ? "&highlight="+highlight.join(",") : "")
    );
  }
  function allItemAssets(item) {
    return [
      ...new Set([
        ...item.asset_ids,
        ...paperContexts(item).map(r=>r.asset_id),
        ...Object.values(record.attachments)
          .filter((a) => a.item_ids.includes(item.id))
          .map((a) => a.id),
      ]),
    ];
  }
  function intakeSubject(item) {
    return {
      id: "item:" + item.id,
      item_id: item.id,
      requirements: [],
      targets: [],
      intake: true,
    };
  }
  function activeIssue() {
    const item = data.items[ui.item];
    return data.issues[ui.issue] && data.issues[ui.issue].item_id === item.id
      ? data.issues[ui.issue]
      : data.issues[item.issue_ids.find((id) => record.issues[id].status !== "reviewed") || item.issue_ids[0]];
  }
  function linksFor(issue) {
    return issue.feedback?.evidence ?? [...issue.targets, ...record.issues[issue.id].links];
  }
  function evidenceFor(issue) {
    const item=data.items[issue.item_id];
    let refs=[...linksFor(issue),...(issue.refs||[]),...paperContexts(item).map(r=>({...r,label:r.statement}))];
    if(!refs.length) refs=allItemAssets(item).map(id=>assets[id]).filter(a=>a.kind==="发票").slice(0,2).map(a=>({asset_id:a.id,page:1,label:"已有票据对照，缺项见右侧问题"}));
    const boxed=new Set(refs.filter(r=>r.box).map(r=>r.asset_id+":"+r.page)), seen=new Set();
    return refs.filter(r=>{
      if(!r.box&&boxed.has(r.asset_id+":"+r.page)) return false;
      const key=r.asset_id+":"+r.page+":"+(r.box||[]).join(",");
      if(seen.has(key)) return false;
      seen.add(key);return true;
    });
  }
  const paperLabels={same_scope:'同一费用范围',different_scope:'同活动不同费用，不能替代',unverified:'已收明白纸，费用范围待核',paper_only:'已收明白纸，仍缺费用或领取明细'};
  function paperContexts(item) {
    const explicit=item.paper_relations||[];
    return [...explicit,...(item.paper_ids||[]).filter(id=>!explicit.some(r=>r.asset_id===id)).map(asset_id=>({asset_id,page:1,source_item_id:item.id,relation:'unverified',statement:'已登记原纸；文件归属不代表费用范围已经核清。'}))];
  }
  function paperSummary(item) {
    const rows=paperContexts(item);
    return rows.length?rows.map(r=>'原第'+r.source_item_id+'项明白纸：'+paperLabels[r.relation]+'。'+r.statement).join(' '):'本批未定位到同费用范围明白纸；请提供文件或已交位置。';
  }
  function updateHash() {
    const p = new URLSearchParams({ item: ui.item, view: ui.view });
    if (ui.asset) {
      p.set("asset", ui.asset);
      p.set("page", ui.page);
    }
    if (ui.issue) p.set("issue", ui.issue);
    history.replaceState(null, "", "#" + p);
  }
  function parseHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    if (data.items[p.get("item")]) ui.item = p.get("item");
    if (["materials", "review", "history", "workflow", "inbox"].includes(p.get("view")))
      ui.view = p.get("view");
    ui.issue = p.get("issue");
    ui.asset = p.get("asset");
    ui.page = Number(p.get("page")) || 1;
  }
  async function refresh() {
    const r = await request("/api/data");
    data = r.data;
    version = r.version;
    record = r.state;
    await openDraftStore();
    workflow = r.workflow || { available: false };
    feedback = r.feedback || { rows: [], counts: {}, open_count: 0 };
    assets = { ...data.assets, ...record.attachments };
    if (!data.items[ui.item]) ui.item = Object.keys(data.items)[0];
    $("#app-title").textContent = data.title;
    $("#summary").textContent =
      `${data.counts.items}个事项 · ${offline ? data.counts.issues : feedback.open_count}条${offline ? "材料说明" : "未关闭问题"} · ${Object.keys(assets).length}份材料 · 记录${record.revision}条`;
    const selected = $("#queue").value;
    $("#queue").innerHTML =
      '<option value="">全部事项</option><option value="__issues">有未关闭问题</option>' +
      Object.entries(statusLabel).map(([key,label]) => '<option value="feedback:'+key+'">'+esc(label)+'</option>').join('') +
      (workflow.available ? Object.entries(routeLabels).map(([key,label]) => '<option value="route:'+key+'">'+esc(label)+'</option>').join('') : '') +
      '<optgroup label="原材料分类">' + [...new Set(Object.values(data.items).map((i) => i.queue))]
        .filter(Boolean)
        .map((q) => '<option value="'+esc(q)+'">原分类：' + esc(q) + "</option>")
        .join("") + '</optgroup>';
    $("#queue").value = selected;
    render();
  }
  const routeLabels = {resume:"已有草稿", delivery:"已预约待投递", applicant:"原单申请人环节", approval:"已提交，待后续审批", blocked:"待核做单条件", hold:"按安排暂缓", candidate:"可检查做单条件", draft_candidate:"已授权仅做草稿", existing:"其他已有单据"};
  function nextIssue(after) {
    const ids=[...document.querySelectorAll("#items [data-item]")].flatMap(b => data.items[b.dataset.item].issue_ids);
    const at=ids.indexOf(after);
    const next=[...ids.slice(at+1),...ids.slice(0,at < 0 ? 0 : at)].find(id=>record.issues[id].status==="pending");
    if(next) {ui.item=data.issues[next].item_id;ui.issue=next;ui.view="review";ui.asset=null;ui.page=1;render();}
    return next;
  }
  function inboxView() {
    const selected=new Set([...document.querySelectorAll("#items [data-item]")].map(b=>b.dataset.item));
    const filter=$("#queue").value;
    const order={replied:0,pending:1,waiting_materials:2,before_submit:3,hold:4,reviewed:5};
    const rows=feedback.rows.filter(r=>selected.has(r.item_id) && (!filter.startsWith("feedback:") || r.status===filter.slice(9))).sort((a,b)=>(order[a.status]-order[b.status]) || a.item_id.localeCompare(b.item_id,undefined,{numeric:true}));
    const counts=feedback.counts;
    return '<section class="panel"><h2>反馈总览</h2><p class="muted">先处理新回复，再按缺口补件。已复核问题保留历史；已有单据继续原单。点击问题可直接查看对应材料。</p><div class="inbox-stats">'+Object.entries(statusLabel).map(([key,label])=>'<button data-filter="feedback:'+key+'">'+esc(label)+'<strong>'+(counts[key]||0)+'</strong></button>').join('')+'</div><div class="toolbar"><button data-filter="">全部事项</button>'+Object.entries(workflow.counts||{}).map(([key,n])=>'<button data-filter="route:'+key+'">'+esc(routeLabels[key]||key)+' '+n+'</button>').join('')+'</div><p class="muted">新回复或补件会重新进入待复核。登记后续待办不会将缺项标成通过。系统观察：'+esc(workflow.observed_at||"未连接")+'</p><div class="table-wrap"><table class="feedback-table"><thead><tr><th>事项 / 问题</th><th>处理进度</th><th>下一步</th><th>原单</th></tr></thead><tbody>'+rows.map(r=>'<tr><td><button data-open-issue="'+esc(r.issue_id)+'">'+esc(r.item_id+' · '+r.item_title)+'</button><p>'+esc(r.title)+'</p></td><td>'+badge(statusLabel[r.status]||r.status,r.status==='reviewed'?'green':'')+'</td><td>'+esc(r.next_action)+'</td><td>'+esc(r.bill_number||'尚未登记')+'</td></tr>').join('')+'</tbody></table></div>'+(!rows.length?'<p class="empty">此范围没有问题记录；可从左侧事项进入材料或办理去向。</p>':'')+'</section>';
  }
  function navigation() {
    const needle = $("#search").value.trim().toLowerCase(),
      queue = $("#queue").value;
    const list = Object.values(data.items).filter((i) => {
      const issueOpen = i.issue_ids.some(
        (id) => record.issues[id].status !== "reviewed",
      );
      if (queue === "__issues" && !issueOpen) return false;
      if (queue.startsWith("feedback:") && !i.issue_ids.some(id => record.issues[id].status === queue.slice(9))) return false;
      if (queue.startsWith("route:") && workflow.items?.[i.id]?.route !== queue.slice(6)) return false;
      if (queue && queue !== "__issues" && !queue.includes(":") && i.queue !== queue) return false;
      return (
        !needle ||
        [
          i.id,
          i.title,
          i.club,
          i.bill_number,
          ...allItemAssets(i).flatMap((id) => [
            assets[id].label,
            ...assets[id].invoice_numbers,
          ]),
        ]
          .join(" ")
          .toLowerCase()
          .includes(needle)
      );
    });
    $("#list-count").textContent =
      `显示${list.length} / ${data.counts.items}项`;
    $("#items").innerHTML = list
      .map(
        (i) =>
          '<button class="item-button" data-item="' +
          i.id +
          '" aria-current="' +
          (ui.item === i.id) +
          '"><span class="seq">' +
          esc(i.id) +
          "</span>" +
          esc(i.title) +
          "<small>" +
          esc(workflow.items?.[i.id]?.label || i.queue || i.conclusion) +
          (i.issue_ids.length ? " · 未关闭" + i.issue_ids.filter(id => record.issues[id].status !== "reviewed").length + "/" + i.issue_ids.length : "") +
          "</small></button>",
      )
      .join("");
  }
  function chooseAsset(item) {
    const allowed = allItemAssets(item);
    if (!allowed.includes(ui.asset)) {
      const issue = activeIssue();
      ui.asset =
        (ui.view === "review" &&
          issue &&
          (linksFor(issue)[0] || issue.refs[0])?.asset_id) ||
        item.paper_ids[0] ||
        allowed[0] ||
        null;
      ui.page = 1;
    }
    if (ui.asset)
      ui.page = Math.min(Math.max(1, ui.page), assets[ui.asset].pages);
  }
  function viewer(item) {
    if (!ui.asset)
      return '<div class="empty amber">本事项未定位到可预览原件。请先登记原件位置或补件。</div>';
    const a = assets[ui.asset];
    const all = allItemAssets(item);
    return (
      '<div class="asset-nav"><select id="asset-select" aria-label="选择材料">' +
      all
        .map(
          (id) =>
            '<option value="' +
            id +
            '" ' +
            (id === ui.asset ? "selected" : "") +
            ">" +
            esc(assets[id].kind + "｜" + assets[id].label) +
            "</option>",
        )
        .join("") +
      '</select></div><figure class="viewer"><img id="material-image" src="' +
      url(a.id, ui.page) +
      '" alt="' +
      esc(a.label) +
      '"><div class="viewer-error" hidden>此页预览不可用，请下载原件查看，或核对原件是否移动/变化。</div><figcaption>' +
      esc(a.label) +
      " · 第" +
      ui.page +
      " / " +
      a.pages +
      '页</figcaption></figure><div class="toolbar"><label>页码 <input id="page-input" class="page-control" type="number" min="1" max="' +
      a.pages +
      '" value="' +
      ui.page +
      '"></label><button data-zoom>放大整页</button><a href="' +
      esc(originalUrl(a.id)) +
      '" download>下载原件</a><button data-copy>复制当前位置</button></div><details class="source"><summary>原件来源与关联</summary><p>' +
      esc(a.path) +
      "<br><code>SHA256 " +
      a.sha256 +
      "</code></p><p>" +
      a.origins.map(esc).join("<br>") +
      '</p></details><div class="reverse-links"><span>关联事项：</span>' +
      a.item_ids
        .map(
          (id) =>
            '<a href="#item=' +
            id +
            "&asset=" +
            a.id +
            "&page=" +
            ui.page +
            '&view=materials">' +
            esc(id + " " + data.items[id].title) +
            "</a>",
        )
        .join("") +
      "</div>"
    );
  }
  function materialsView(item) {
    const fees = item.expense_ids.map((id) => data.expenses[id]);
    const paper = paperContexts(item).length
      ? '<div class="paper-grid">' +
        paperContexts(item).map(r=>r.asset_id)
          .map(
            (id) =>
              '<button data-asset="' +
              id +
              '"><img loading="lazy" src="' +
              url(id) +
              '" alt="明白纸原图"><small>查看原始明白纸</small></button>',
          )
          .join("") +
        "</div>"
      : '<div class="empty amber">本批尚未定位同范围明白纸；不代表原件不存在。</div>';
    return (
      '<div class="two-col"><section class="panel"><h3>明白纸 / 原始清单</h3>' +
      '<p>'+esc(paperSummary(item))+'</p>'+paper +
      '<h3 class="section-gap">费用与发票对应（' +
      fees.length +
      '行）</h3><p class="muted">费用行来自已核输入及有依据的更正。收款安排与实际付款凭证分别记录。</p><div class="table-wrap"><table><thead><tr><th>费用 / 票号</th><th>票面额</th><th>材料</th></tr></thead><tbody>' +
      fees
        .map(
          (f) =>
            '<tr><td class="fee-title">' +
            (f.refs.length
              ? '<button data-asset="' +
                f.refs[0].asset_id +
                '" data-page="' +
                f.refs[0].page +
                '">' +
                esc(f.label) +
                "</button>"
              : esc(f.label)) +
            '<span class="invoice-number">' +
            f.invoice_numbers.map(esc).join("<br>") +
            '</span>' +
            (f.review_note ? '<p>'+esc(f.review_note)+'</p>' : '') +
            (f.candidate_amount ? '<p>文件名候选金额（待确认）：'+amount(f.candidate_amount)+'</p>' : '') +
            '<details class="muted"><summary>关联依据与收款安排</summary>' +
            esc(f.association) +
            "<br>" +
            f.payments
              .map((p) =>
                esc(
                  [p.name, p.amount, p.allocation_basis]
                    .filter(Boolean)
                    .join("｜"),
                ),
              )
              .join("<br>") +
            '</details></td><td class="num">' +
            amount(f.face_amount ?? f.display_amount) +
            "</td><td>" +
            f.refs
              .map(
                (r) =>
                  '<button data-asset="' +
                  r.asset_id +
                  '" data-page="' +
                  r.page +
                  '">第' +
                  r.page +
                  "页</button>",
              )
              .join("") +
            "</td></tr>",
        )
        .join("") +
      "</tbody></table></div>" +
      (!fees.length
        ? '<p class="muted">费用/票据尚未建立逐行关联，保留未知项。</p>'
        : "") +
      '<details class="section-gap"><summary>全部分类材料（' +
      allItemAssets(item).length +
      '份）</summary><div class="file-list">' +
      allItemAssets(item)
        .map(
          (id) =>
            '<button data-asset="' +
            id +
            '">' +
            esc(assets[id].label) +
            "<small>" +
            esc(assets[id].kind) +
            " · " +
            assets[id].pages +
            "页</small></button>",
        )
        .join("") +
      "</div></details>" +
      reviewForm(intakeSubject(item)) +
      timeline(intakeSubject(item)) +
      '</section><section class="panel">' +
      viewer(item) +
      "</section></div>"
    );
  }
  function reviewForm(issue) {
    if (offline)
      return (
        '<p class="empty section-gap">这是材料浏览包。请按' +
        esc(issue.id) +
        "提供答复，并将补充文件交经办人登记。</p>"
      );
    const rs = record.issues[issue.id],
      draft = drafts[issue.id] || {};
    const options = [
      ...linksFor(issue),
      ...(ui.asset
        ? [{ asset_id: ui.asset, page: ui.page, label: "当前打开材料" }]
        : []),
    ].filter(
      (r, i, arr) =>
        arr.findIndex((x) => x.asset_id === r.asset_id && x.page === r.page) ===
        i,
    );
    return (
      '<form id="record-form" class="record-form" data-issue="' +
      esc(issue.id) +
      '"><h3>' +
      (issue.intake ? "向本事项补充材料" : "登记回复、补件与复核") +
      '</h3><div class="form-row"><label>记录人 / 复核人（选填）<input id="actor" maxlength="100" value="' +
      esc(draft.actor || "") +
      '"></label><label>记录类型<select id="action"><option value="reply">收到答复</option><option value="note">经办备注</option>' +
      (issue.intake
        ? ""
        : '<option value="review">登记材料复核</option><option value="reopen">重新核查</option><option value="defer">登记后续待办</option>') +
      '<option value="link">关联当前材料为证据</option></select></label></div><label>答复原文 / 处理说明<textarea id="reply-text" required maxlength="20000" placeholder="保留对方原话、材料位置和本次判断依据">' +
      esc(draft.text || "") +
      '</textarea></label><label id="defer-options" hidden>后续阶段<select id="defer-stage"><option value="waiting_materials">答复已处理，等待材料</option><option value="before_submit">提交前补齐</option><option value="hold">按明确安排暂缓</option></select></label><fieldset id="review-checks" class="checks" hidden><legend>逐项复核完成条件</legend>' +
      issue.requirements
        .map(
          (x, i) =>
            '<label><input type="checkbox" name="condition" value="' +
            i +
            '"><span>' +
            esc(x) +
            "</span></label>",
        )
        .join("") +
      '<p class="muted">选取支持本次复核的证据：</p>' +
      options
        .map(
          (r, i) =>
            '<label><input type="checkbox" name="evidence" value="' +
            i +
            '" data-asset="' +
            r.asset_id +
            '" data-page="' +
            r.page +
            '"><span>' +
            esc(r.label || assets[r.asset_id].label) +
            "（第" +
            r.page +
            "页）</span></label>",
        )
        .join("") +
      '<p class="muted">仅登记材料复核意见；财务状态由正式业务接口回读。</p></fieldset><label>补件类别<select id="file-category"><option>待分类</option><option>发票</option><option>付款凭证</option><option>订单</option><option>明白纸</option><option>审批表</option><option>明细清单</option><option>其他</option></select></label><label id="file-drop" class="file-drop">选择或拖入补充文件（可多选，每份不超过20MB）<input id="attachment" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.xls,.xlsx,.txt,.zip"></label>' +
      (draft.files?.length
        ? '<p class="muted">已选' +
          draft.files.length +
          "份：" +
          draft.files
            .map(
              (f, i) =>
                esc(f.name) +
                ' <button type="button" data-remove-file="' +
                i +
                '">移除</button>',
            )
            .join("<br>") +
          "</p>"
        : "") +
      '<label class="advance-option"><input type="checkbox" id="advance-after-reply" '+(advanceAfterReply?'checked':'')+'>保存回复后自动到下一条待回复问题</label><div class="toolbar"><button class="primary" type="submit">保存本次记录</button><button type="button" data-clear-draft>清除本项未发送草稿</button><button type="button" data-next-issue>下一条待回复</button></div><p class="muted">未发送的文字和补件自动保存在本浏览器；可关闭自动跳转以继续补件。清除草稿不删除已保存记录或原件。当前记录版本：' +
      record.revision +
      "</p></form>"
    );
  }
  function timeline(issue) {
    if (offline) return "";
    const rs = record.issues[issue.id];
    return (
      '<section class="timeline"><h3>回复与处理历史</h3>' +
      (rs.stale_events.length
        ? '<p class="empty amber">问题定义更新前的记录保留在下方，旧复核结论未自动沿用。</p>'
        : "") +
      rs.events
        .slice()
        .reverse()
        .map(
          (e) =>
            '<article class="event"><small>' +
            esc(actionLabel[e.action] || e.action) +
            " · " +
            esc(e.actor) +
            " · " +
            esc(new Date(e.recorded_at).toLocaleString()) +
            (e.current_definition ? "" : " · 旧问题版本") +
            "</small><p>" +
            esc(e.text) +
            "</p>" +
            (e.attachment
              ? '<button data-asset="' +
                e.attachment.id +
                '">查看补件：' +
                esc(e.attachment.label) +
                "</button>"
              : "") +
            "</article>",
        )
        .join("") +
      (!rs.events.length
        ? '<p class="muted">暂无新增回复或补件记录。</p>'
        : "") +
      "</section>"
    );
  }
  function reviewView(item) {
    const issue = activeIssue();
    if (!issue)
      return (
        '<div class="empty">当前快照没有待处理的材料问题。实际投递、接收和付款请按对应业务回执跟进。</div>' +
        materialsView(item)
      );
    ui.issue = issue.id;
    const targets = evidenceFor(issue),
      rs = record.issues[issue.id];
    const grouped = new Map();
    for (const r of targets) {
      const key=r.asset_id+":"+r.page;
      if (!grouped.has(key)) grouped.set(key,[]);
      grouped.get(key).push(r);
    }
    const focus = targets.length ? '<div class="focus-pair">'+Array.from(grouped.values()).map(refs=>{
      const r=refs[0], a=assets[r.asset_id], regions=refs.filter(ref=>ref.box);
      const relation=paperContexts(item).find(p=>p.asset_id===r.asset_id&&p.page===r.page);
      const marks=regions.map((ref,n)=>{
        let [x,y,w,h]=ref.box;
        if(a.rotation===90) [x,y,w,h]=[y,1-x-w,h,w];
        else if(a.rotation===180) [x,y,w,h]=[1-x-w,1-y-h,w,h];
        else if(a.rotation===270) [x,y,w,h]=[1-y-h,x,h,w];
        return '<rect x="'+x*100+'" y="'+y*100+'" width="'+w*100+'" height="'+h*100+'" fill="none" stroke="#b92525" stroke-width=".3"/><text x="'+(x*100+1)+'" y="'+(y*100+3)+'" fill="#b92525" font-size="3">'+(n+1)+'</text>';
      }).join('');
      const notes=regions.map((ref,n)=>'<p>'+(n+1)+'. '+esc(ref.label||'圈注位置')+' <button data-focus-asset="'+r.asset_id+'" data-focus-page="'+r.page+'" data-focus-box="'+ref.box.join(',')+'" aria-label="放大圈注：'+esc(ref.label)+'">按需放大</button></p>').join('');
      return '<figure>'+(relation?'<p>'+esc('原第'+relation.source_item_id+'项 · '+paperLabels[relation.relation]+'：'+relation.statement)+'</p>':'')+'<strong>'+esc(a.label)+' · 第'+r.page+'页</strong><div style="position:relative;margin-top:8px"><img loading="lazy" style="max-height:none" src="'+url(r.asset_id,r.page)+'" alt="对应材料原页：'+esc(a.label)+'"><svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">'+marks+'</svg></div>'+notes+'</figure>';
    }).join('')+'</div>' : '';
    const groupIds = Object.values(data.items)
      .filter((i) => i.club === item.club && i.issue_ids.length)
      .map((i) => i.id);
    const exportQuery = groupIds
      .map((id) => "item=" + encodeURIComponent(id))
      .join("&");
    return (
      '<div class="issue-picker">' +
      item.issue_ids
        .map(
          (id) =>
            '<button data-issue="' +
            id +
            '" class="' +
            (issue.id === id ? "active" : "") +
            '">' +
            esc(id + " · " + statusLabel[record.issues[id].status]) +
            "</button>",
        )
        .join("") +
      '</div><div class="review-col"><section class="panel"><p class="muted" style="margin-bottom:12px">' +
      esc(issue.location_note) +
      '</p><h3>'+esc(issue.feedback?.project||item.title)+'</h3><p>'+esc(paperSummary(item))+'</p>'+ (issue.feedback?.context ? '<p>'+esc(issue.feedback.context)+'</p>' : '') +
      '<p>当前安排：'+esc(['waiting_materials','before_submit','hold','replied','reviewed'].includes(rs.status)?(rs.next_action||(rs.status==='reviewed'?'材料已复核，无需重复答复。':'已有答复或后续安排，无需重复答复原问题。')):(issue.feedback?.request||issue.request))+'</p>'+ (rs.status==='pending'&&issue.feedback?.response ? '<p>请回复：'+esc(issue.feedback.response)+'</p>' : '') +
      focus +
      (focus ? '<details><summary>查看其他材料与下载原件</summary>'+viewer(item)+'</details>' : viewer(item)) +
      '</section><section class="issue-panel">' +
      badge(
        statusLabel[rs.status],
        rs.status === "reviewed" ? "green" : issue.tone,
      ) +
      '<h3 class="issue-title">' +
      esc(rs.status==='pending'?(issue.feedback?.request||issue.title):statusLabel[rs.status]) +
      '</h3><p class="muted">' +
      esc(issue.id) +
      " · " +
      esc(issue.queue) +
      "</p>" + (rs.next_action ? '<div class="draft"><h4>当前后续待办</h4><p>'+esc(rs.next_action)+'</p></div>' : "") + '<details><summary>原问题依据与复核条件（留档）</summary><dl><dt>原问题描述</dt><dd>' + esc(issue.title) + "</dd><dt>原问题依据</dt><dd>" +
      esc(issue.known) +
      "</dd><dt>处理责任</dt><dd>" +
      esc(issue.owner) +
      "</dd><dt>原始答复要求</dt><dd>" +
      esc(issue.request) +
      "</dd><dt>完成条件</dt><dd>" +
      issue.requirements.map(esc).join("<br>") +
      "</dd></dl></details>" +
      reviewForm(issue) +
      timeline(issue) +
      '<details class="section-gap"><summary>本社团反馈草稿与汇总</summary><p class="muted">只导出本社团可对外反馈且尚未登记复核的问题；暂缓、内部核实及本人环节保留在工作台。</p><div class="draft">' +
      esc(issue.request) +
      '</div><a href="/api/export?' +
      exportQuery +
      '">下载本社团反馈包（未发送）</a></details></section></div>'
    );
  }
  function historyView(item) {
    const rows = item.history;
    return (
      "<h3>已解决、更正与历史答复</h3>" +
      rows
        .map(
          (r) =>
            '<article class="history-card">' +
            badge("保留更正记录", "green") +
            "<p>" +
            esc(r["事项"]) +
            '</p><p class="muted">旧记录：' +
            esc(r["旧问题"]) +
            "</p><p>" +
            esc(r["当前处理"]) +
            '</p><p class="muted">依据：' +
            esc(r["依据"]) +
            "</p></article>",
        )
        .join("") +
      (item.prior_reply
        ? '<article class="history-card"><h3>此前已采纳的答复</h3><p>' +
          esc(JSON.stringify(item.prior_reply.raw_answers || {})) +
          "</p><p>" +
          esc(item.prior_reply.review) +
          "</p></article>"
        : "") +
      (!rows.length && !item.prior_reply
        ? '<p class="empty">此事项未登记单独的历史更正记录。</p>'
        : "")
    );
  }
  function workflowView(item) {
    if (!workflow.available) return '<p class="empty">'+esc(workflow.reason||'此副本尚未接入正式办理状态。请在本机连接正式工具后导入完整回执。')+'</p>';
    const row=workflow.items[item.id], c=workflow.counts;
    const summary=`${c.delivery||0}项待投递 · ${c.approval||0}项已提交待审批 · ${c.resume||0}项已建草稿 · ${c.applicant||0}项原单申请人环节 · ${c.draft_candidate||0}项已授权仅做草稿 · ${(c.blocked||0)+(c.hold||0)}项待核/暂缓 · ${c.candidate||0}项可检查做单条件`;
    const saved=preflights[item.id];
    const receipt=saved?.dataset_version===version && saved.event_revision===record.revision && saved.workflow_version===workflow.version ? saved : null;
    return '<section class="panel"><h3>本批办理去向</h3><p>'+esc(summary)+'</p><p class="muted">系统完整回读：'+esc(new Date(workflow.observed_at).toLocaleString())+'。这是带时间的快照；真正办理仍由正式程序重新核对。</p><article class="history-card">'+badge(row.label,row.can_prepare?'green':'amber')+'<h3 class="section-gap">'+esc(row.next_action)+'</h3><p>原单号：'+esc(row.serial||'本案尚未登记')+' · 申请 / 已建单额：'+amount(row.amount)+'</p>'+
      (row.open_issues.length?'<p class="muted">尚有以下材料事项：</p>'+row.open_issues.map(id=>'<p><button data-workflow-issue="'+esc(id)+'">'+esc(id+'｜'+data.issues[id].title)+'</button></p>').join(''):'<p>当前问题清单无未处理项；申请额、收款、经费、票据占用和审批条件仍须由正式程序检查。</p>')+
      '<p class="section-gap">核对完成 → 正式做单预检 → 新建或续办原单 → 保存回读 → 提交条件检查 → 原单申请人及审批环节 → 投递。</p>'+
      (row.can_inspect_formal?'<button class="primary section-gap" data-workflow-inspect>进入正式做单预检</button>':'<p class="empty section-gap">'+(row.case_id?'此副本未连接正式做单程序。':'本事项尚未登记正式案件。先补齐材料、核定金额和收款，才能生成做单计划。')+'</p>')+
      '<p class="muted section-gap">此入口调用正式程序的本地预检；不会新建、保存或提交财务单据。本人确认及短信环节保留给本人。</p>'+
      (receipt?'<div class="draft"><strong>正式程序预检已返回</strong><p>案件：'+esc(receipt.case_id)+' · 原单：'+esc(receipt.serial)+'</p><p>本地参数：'+esc(receipt.parameter_errors.length?receipt.parameter_errors.join('；'):'未报告参数错误')+'；输入检查：'+(receipt.input_validation?.ok===true?'通过':receipt.input_validation?.ok===false?'未通过':'未报告')+'</p><p>材料待处理：'+receipt.material_issues.length+'项；未确认写入：'+(receipt.pending_write?'有，先恢复原任务':'未报告')+'；在途任务：'+receipt.active_jobs.length+'项</p><p>'+esc(receipt.note)+'</p></div>':'')+'</article></section>';
  }
  function stashForm() {
    const form = $("#record-form");
    if (!form) return;
    const iid = form.dataset.issue;
    drafts[iid] = {
      actor: $("#actor").value,
      text: $("#reply-text").value,
      action: $("#action").value,
      stage: $("#defer-stage").value,
      category: $("#file-category").value,
      checks: [...form.querySelectorAll("[name=condition]")].map(
        (e) => e.checked,
      ),
      evidence: [...form.querySelectorAll("[name=evidence]:checked")].map(
        (e) => e.dataset.asset + ":" + e.dataset.page,
      ),
      files: $("#attachment").files.length
        ? [...$("#attachment").files]
        : drafts[iid]?.files || [],
    };
    persistDrafts();
  }
  function restoreForm() {
    const form = $("#record-form");
    if (!form) return;
    const d = drafts[form.dataset.issue];
    if (!d) return;
    $("#action").value = d.action;
    $("#defer-stage").value = d.stage || "waiting_materials";
    $("#defer-options").hidden = $("#action").value !== "defer";
    $("#file-category").value = d.category || "待分类";
    $("#review-checks").hidden = d.action !== "review";
    form
      .querySelectorAll("[name=condition]")
      .forEach((e, i) => (e.checked = !!d.checks?.[i]));
    form
      .querySelectorAll("[name=evidence]")
      .forEach(
        (e) =>
          (e.checked = (d.evidence || []).includes(
            e.dataset.asset + ":" + e.dataset.page,
          )),
      );
  }
  function render() {
    stashForm();
    const item = data.items[ui.item];
    if (ui.view === "inbox" && !offline) { navigation(); $("#main").innerHTML = inboxView(); updateHash(); return; }
    chooseAsset(item);
    navigation();
    $("#main").innerHTML =
      '<div class="item-heading"><div><h2>' +
      esc(item.id + " · " + item.title) +
      '</h2><details class="finance"><summary>此前核对结论</summary>' +
      esc(item.conclusion) +
      "</details></div>" +
      badge(workflow.items?.[item.id]?.label || item.queue) +
      '</div><div class="finance finance-system">单号：' +
      esc(item.bill_number || "尚未登记") +
      " · 系统观察：" +
      esc(workflow.items?.[item.id]?.status_name || item.finance_status) +
      "<br>状态采集时间：" +
      esc(workflow.available ? workflow.observed_at : item.finance_observed_at || data.system_snapshot_at) +
      '（历史快照）</div><div class="amounts"><div><small>票面合计</small><strong>' +
      amount(item.face_total) +
      "</strong></div><div><small>纸面登记</small><strong>" +
      amount(item.paper_total) +
      '</strong></div><div class="claim-amount"><small>申请 / 已建单额</small><strong>' +
      amount(item.claim_total) +
      '</strong></div></div><div class="tabs"><button data-view="materials" class="' +
      (ui.view === "materials" ? "active" : "") +
      '">明白纸、清单与材料</button><button data-view="review" class="' +
      (ui.view === "review" ? "active" : "") +
      '">问题与反馈' +
      (item.issue_ids.length ? "（未关闭" + item.issue_ids.filter(id => record.issues[id].status !== "reviewed").length + "/" + item.issue_ids.length + "）" : "") +
      '</button><button data-view="history" class="' +
      (ui.view === "history" ? "active" : "") +
      '">历史更正</button>' +
      (offline ? "" : '<button data-view="workflow" class="'+(ui.view==="workflow"?"active":"")+'">办理去向与做单准备</button>') + '</div>' +
      (ui.view === "materials"
        ? materialsView(item)
        : ui.view === "history"
          ? historyView(item)
          : ui.view === "workflow" && !offline
            ? workflowView(item)
            : reviewView(item));
    updateHash();
    restoreForm();
    if (offline) {
      $(".finance-system").remove();
      $(".claim-amount").remove();
      document
        .querySelectorAll('a[href^="/api/export"]')
        .forEach((a) => a.closest("details").remove());
    }
    const img = $("#material-image");
    if (img)
      img.addEventListener("error", () => {
        img.hidden = true;
        $(".viewer-error").hidden = false;
      });
  }
  async function saveRecord(event) {
    event.preventDefault();
    if (busy) return;
    const subjectId = $("#record-form").dataset.issue,
      issue = data.issues[subjectId] || intakeSubject(data.items[ui.item]),
      actor = $("#actor").value.trim(),
      text = $("#reply-text").value.trim(),
      action = $("#action").value;
    const files = $("#attachment").files.length
      ? [...$("#attachment").files]
      : drafts[issue.id]?.files || [];
    if (!text) {
      notice("请填写说明；记录人可以留空。", true);
      return;
    }
    busy = true;
    const submit = $("#record-form button[type=submit]");
    submit.disabled = true;
    for (const control of $("#record-form").elements) control.disabled = true;
    let completed = 0;
    try {
      if (files.length > 20) throw Error("每次最多选择20份补件。");
      if (files.some((f) => f.size > 20 * 1024 * 1024))
        throw Error("单份补件不能超过20MB。");
      if (files.length && !["reply", "note"].includes(action))
        throw Error("补件请使用“收到答复”或“经办备注”；复核另行登记。");
      const shouldAdvance = $("#advance-after-reply").checked;
      const jobs = files.length ? files : [null];
      let revision = record.revision;
      for (const file of jobs) {
        let req = pendingRequests[issue.id];
        if (req) {
          if (
            req.text !== text ||
            req.actor !== actor ||
            (file && req.file?.name !== file.name)
          )
            throw Error(
              "上次保存结果尚未确认。请保留原说明和文件，重试原记录后再编辑。",
            );
        } else {
          req = {
            version,
            revision,
            request_id: crypto.randomUUID(),
            issue_id: issue.id,
            actor,
            text,
            action,
          };
          if (file) {
            const encoded = await new Promise((resolve, reject) => {
              const r = new FileReader();
              r.onload = () => resolve(String(r.result).split(",")[1]);
              r.onerror = reject;
              r.readAsDataURL(file);
            });
            req.action = "attachment";
            req.file = {
              name: file.name,
              base64: encoded,
              category: $("#file-category").value,
            };
          } else if (action === "defer") {
            req.stage = $("#defer-stage").value;
          } else if (action === "review") {
            req.checks = [...document.querySelectorAll("[name=condition]")].map(
              (e) => e.checked,
            );
            req.evidence = [
              ...document.querySelectorAll("[name=evidence]:checked"),
            ].map((e) => ({
              asset_id: e.dataset.asset,
              page: Number(e.dataset.page),
            }));
          } else if (action === "link") {
            if (!ui.asset) throw Error("请先选择对应材料。");
            req.target = {
              asset_id: ui.asset,
              page: ui.page,
              label: "人工关联：" + assets[ui.asset].label,
            };
          }
          pendingRequests[issue.id] = req;
        }
        // Persist the original request (including attachments) before dispatch, so reload retries
        // the same idempotency key even when the HTTP response is lost.
        await persistDrafts();
        let response;
        try {
          response = await request("/api/events", {
            method: "POST",
            body: JSON.stringify(req),
          });
        } catch (e) {
          if (e.confirmedRejection) {
            delete pendingRequests[issue.id];
            await persistDrafts();
          }
          throw e;
        }
        delete pendingRequests[issue.id];
        revision = response.revision;
        completed++;
      }
      delete drafts[issue.id];
      await persistDrafts();
      $("#record-form").remove();
      await refresh();
      let next = null;
      if (action === "reply" && !issue.intake && shouldAdvance) next = nextIssue(issue.id);
      notice(
        (files.length
          ? "已保存并读回" +
              files.length +
              "份补件，均关联到" +
              (issue.intake ? "当前事项" : "当前问题") +
              "，等待复核。"
          : "已保存并读回本次记录。") +
          (next ? "已跳到下一条待回复问题。" : action === "reply" && !issue.intake && shouldAdvance ? "当前筛选范围内没有其他待回复问题。" : ""),
      );
    } catch (e) {
      if (completed && files.length) {
        stashForm();
        drafts[issue.id].files = files.slice(completed);
        $("#record-form").remove();
        await refresh();
      }
      notice(
        (completed ? "已保存" + completed + "份，其余未完成。" : "") +
          e.message,
        true,
      );
    } finally {
      busy = false;
      if (submit.isConnected)
        for (const control of $("#record-form").elements)
          control.disabled = false;
    }
  }
  document.addEventListener("click", async (event) => {
    const b = event.target.closest("button");
    if (!b) return;
    if (busy) {
      event.preventDefault();
      notice("正在保存，请等待本次记录读回。");
      return;
    }
    try {
      if (b.hasAttribute("data-clear-draft")) {
        const iid = $("#record-form").dataset.issue;
        if (pendingRequests[iid]) throw Error("上次保存结果尚未确认，请先重试原记录，再清除草稿。");
        delete drafts[iid];
        $("#record-form").remove();
        await persistDrafts();
        render();
        notice("本项未发送草稿已清除；已保存的回复和原件仍保留。");
      }
      if (b.dataset.removeFile !== undefined) {
        stashForm();
        const iid = $("#record-form").dataset.issue;
        drafts[iid].files.splice(Number(b.dataset.removeFile), 1);
        render();
      }
      if (b.dataset.workflowIssue) {ui.issue=b.dataset.workflowIssue;ui.view='review';ui.asset=null;render();}
      if (b.hasAttribute('data-workflow-inspect')) {
        b.disabled=true;b.textContent='正在调用正式程序预检…';
        try { const result=await request('/api/workflow/inspect',{method:'POST',body:JSON.stringify({item_id:ui.item,version,revision:record.revision})});preflights[result.receipt.item_id]=result.receipt;render();notice('正式做单预检已读回；未派发财务写入。'); }
        finally {if(b.isConnected){b.disabled=false;b.textContent='进入正式做单预检';}}
      }
      if (b.id === "inbox-open") {ui.view="inbox";render();}
      if (b.dataset.filter !== undefined) {$("#queue").value=b.dataset.filter;ui.view="inbox";render();}
      if (b.dataset.openIssue) {ui.item=data.issues[b.dataset.openIssue].item_id;ui.issue=b.dataset.openIssue;ui.asset=null;ui.view="review";render();}
      if (b.hasAttribute("data-next-issue")) {if (!nextIssue(ui.issue)) notice("当前筛选范围没有其他待回复问题。");}
      if (b.dataset.item) {
        if (ui.view === "inbox") ui.view = "review";
        ui.item = b.dataset.item;
        ui.asset = null;
        ui.issue = null;
        ui.page = 1;
        render();
      }
      if (b.dataset.view) {
        ui.view = b.dataset.view;
        render();
      }
      if (b.dataset.asset) {
        ui.asset = b.dataset.asset;
        ui.page = Number(b.dataset.page) || 1;
        render();
      }
      if (b.dataset.issue) {
        ui.issue = b.dataset.issue;
        ui.asset = null;
        render();
      }
      if (b.dataset.focusAsset) {
        $("#zoom-image").src=url(b.dataset.focusAsset,Number(b.dataset.focusPage),b.dataset.focusBox.split(",").map(Number));
        $("#zoom-title").textContent=b.getAttribute("aria-label");
        $("#image-dialog").showModal();
      }
      if (b.hasAttribute("data-zoom")) {
        $("#zoom-image").src = url(ui.asset, ui.page);
        $("#zoom-title").textContent =
          assets[ui.asset].label + " · 第" + ui.page + "页";
        $("#image-dialog").showModal();
      }
      if (b.hasAttribute("data-copy")) {
        await navigator.clipboard.writeText(offline ? "index.html" + location.hash : location.href);
        notice(
          offline
            ? "已复制包内位置；请连同完整材料包交接。"
            : "已复制当前位置；链接可在本机工作台运行时打开。",
        );
      }
    } catch (e) {
      notice(e.message, true);
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.id === "attachment") {
      stashForm();
      render();
    }
    if (event.target.id === "asset-select") {
      ui.asset = event.target.value;
      ui.page = 1;
      render();
    }
    if (event.target.id === "page-input") {
      ui.page = Number(event.target.value) || 1;
      render();
    }
    if (event.target.id === "action")
      $("#review-checks").hidden = event.target.value !== "review";
    if (event.target.closest("#record-form")) stashForm();
  });
  document.addEventListener("input", (event) => {
    if (event.target.closest("#record-form")) stashForm();
  });
  document.addEventListener("dragover", (e) => {
    if (e.target.closest("#file-drop")) {
      e.preventDefault();
      e.target.closest("#file-drop").classList.add("dragging");
    }
  });
  document.addEventListener("dragleave", (e) =>
    e.target.closest("#file-drop")?.classList.remove("dragging"),
  );
  document.addEventListener("drop", (e) => {
    if (!e.target.closest("#file-drop")) return;
    e.preventDefault();
    stashForm();
    const iid = $("#record-form").dataset.issue;
    drafts[iid].files = [...drafts[iid].files, ...e.dataTransfer.files];
    render();
  });
  document.addEventListener("submit", (e) => {
    if (e.target.id === "record-form") saveRecord(e);
  });
  $("#search").addEventListener("input", () => {if(data) {navigation();if(ui.view === "inbox") $("#main").innerHTML=inboxView();}});
  $("#queue").addEventListener("change", () => {if(data) {navigation();if(ui.view === "inbox") $("#main").innerHTML=inboxView();}});
  document.addEventListener("change", (e) => {
    if(e.target.id === "advance-after-reply") {advanceAfterReply=e.target.checked;persistDrafts();}
    if(e.target.id === "action") $("#defer-options").hidden=e.target.value!=="defer";
  });
  $("#finish-session").addEventListener("click", async () => {
    if(busy) {notice("正在保存，等本次结果读回后再结束使用。");return;}
    try {
      stashForm();await persistDrafts();
      closingPage=true;clearInterval(viewTimer);viewTimer=null;
      await sendView("close",true).catch(()=>{});
      window.close();
      // A browser may forbid a page from closing a user-created tab.
      document.querySelectorAll("button,input,select,textarea").forEach(e=>e.disabled=true);
      $("#main").innerHTML='<section class="panel"><h2>本页已结束使用</h2><p>未发送文字和补件已保存在本浏览器；已保存记录仍在原工作台。可以关闭此标签。</p><button id="resume-session">返回工作台</button></section>';
      $("#resume-session").addEventListener("click",()=>location.reload());
      notice("本页已断开；若浏览器未自动关闭，请关闭标签。");
    } catch(e) {closingPage=false;notice("草稿尚未保存，保留此页："+e.message,true);}
  });
  window.addEventListener("pagehide",()=>{clearInterval(viewTimer);viewTimer=null;sendView("close",true).catch(()=>{});});
  window.addEventListener("pageshow",()=>{if(data&&!closingPage)trackView();});
  $("#refresh").addEventListener("click", () =>
    refresh()
      .then(() => notice("已刷新本地记录。"))
      .catch((e) => notice(e.message, true)),
  );
  $("#close-dialog").addEventListener("click", () =>
    $("#image-dialog").close(),
  );
  window.addEventListener("hashchange", async () => {
    try {
      const params=new URLSearchParams(location.hash.slice(1)),token=params.get("access");
      if(token && !offline) {
        stashForm();
        await request("/api/session", {method:"POST",headers:{"X-Review-Token":token}});
        params.delete("access");params.set("view","inbox");
        history.replaceState(null,"","#"+params);
        await refresh();notice("已连接本机工作台；原有草稿和记录保留。");
      }
      if(data) {parseHash();render();}
    } catch(e) {notice(e.message,true);}
  });
  function exportCandidates() {
    const scope=$("#export-scope").value,club=$("#export-club").value,status=$("#export-status").value;
    return Object.values(data.issues).filter(q=>q.sendable &&
      record.issues[q.id].status!=="reviewed" &&
      (scope!=="item" || q.item_id===ui.item) &&
      (!club || data.items[q.item_id].club===club) &&
      (status==="all" || record.issues[q.id].status===status))
      .sort((a,b)=>a.item_id.localeCompare(b.item_id,undefined,{numeric:true}));
  }
  function exportSelection() {
    const mode=$("#export-mode").value;
    const issues=mode==="handoff"?[]:[...document.querySelectorAll("#export-issues input:checked")].map(e=>e.value);
    return {mode,issues,ids:[...new Set(issues.map(id=>data.issues[id].item_id))]};
  }
  function exportChoices() {
    $("#export-issues").innerHTML=exportCandidates().map(q=>'<label class="export-issue"><input type="checkbox" value="'+esc(q.id)+'" checked><span><strong>'+esc(q.item_id+' · '+data.items[q.item_id].club+' · '+statusLabel[record.issues[q.id].status])+'</strong><br>'+esc(record.issues[q.id].status==='pending'?(q.feedback?.request||q.request||q.title):(record.issues[q.id].next_action||q.title))+'<small>'+esc(q.id)+'</small></span></label>').join('') || '<p class="empty">此筛选范围没有可对外反馈的问题。</p>';
    exportSummary();
  }
  function exportSummary() {
    const { mode, ids, issues } = exportSelection();
    $("#export-scope").disabled = mode === "handoff";
    $("#export-filters").hidden=mode==="handoff";
    $("#export-download").disabled=mode!=="handoff"&&!issues.length;
    const selected=new Set(ids);
    const refs=issues.flatMap(id=>{
      const q=data.issues[id],item=data.items[q.item_id];
      let list=[...linksFor(q)];
      if(mode==="pdf"){
        list.push(...(q.refs||[]),...paperContexts(item));
        if(!list.length) list=Object.values(assets).filter(a=>a.item_ids.includes(q.item_id)&&a.kind==="发票").map(a=>({asset_id:a.id,page:1}));
      }
      return list;
    });
    const related=new Set(ids.flatMap(id=>(data.items[id].paper_relations||[]).map(r=>r.asset_id)));
    const candidates=(mode==="feedback"||mode==="pdf")?[...new Set(refs.map(r=>r.asset_id))].map(id=>assets[id]):
      Object.values(assets).filter(a=>a.item_ids.some(id=>selected.has(id))||related.has(a.id));
    const included=candidates.filter(a=>(a.item_ids.every(id=>selected.has(id))||((mode==="pdf"||mode==="materials")&&related.has(a.id)))&&a.kind!=="正式打印件"&&(mode!=="pdf"||[".pdf",".png",".jpg",".jpeg",".webp",".bmp",".tif",".tiff"].includes(a.suffix)));
    $("#export-description").textContent =
      mode === "handoff"
        ? "完整案件，共" +
          data.counts.items +
          "项。包含内部记录和全部材料，供指定经办人继续补件。交接后旧副本停止写入。"
        : (mode === "pdf" ? "按项目展示明白纸对应、费用清单、材料整体圈注，生成可直接打开的PDF。"
            : mode === "materials"
            ? "解压即可交互浏览；包含所选事项的分类原件、清单和问题定位。"
            : "只含尚待对外反馈的问题及明确证据。") +
          " 已选 "+issues.length+" 条问题 / "+ids.length+" 个事项 / "+included.length+" 份材料；"+
          (candidates.length-included.length)+" 份跨范围共享材料或正式打印件不附。未勾选的问题不写入问题清单。"+
          (mode==="materials"?" 所选事项的其他已有材料一并附入，方便对照。":mode==="pdf"?" 明确标注的关联明白纸也会附入，并说明原属事项及范围区别；每份材料只展示一次，直接在整体图上圈出位置。":" PDF仅附问题对应页。");
  }
  $("#export-open").addEventListener("click", () => {
    if (busy) return;
    $("#export-status").value="pending";
    $("#export-scope").value="all";
    $("#export-club").innerHTML='<option value="">全部社团</option>'+[...new Set(Object.values(data.items).map(i=>i.club))].sort().map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('');
    exportChoices();
    $("#export-dialog").showModal();
  });
  $("#export-mode").addEventListener("change", exportSummary);
  $("#export-scope").addEventListener("change", exportChoices);
  $("#export-club").addEventListener("change", exportChoices);
  $("#export-status").addEventListener("change", exportChoices);
  $("#export-issues").addEventListener("change", exportSummary);
  $("#export-select-all").addEventListener("click",()=>{document.querySelectorAll("#export-issues input").forEach(e=>e.checked=true);exportSummary();});
  $("#export-select-none").addEventListener("click",()=>{document.querySelectorAll("#export-issues input").forEach(e=>e.checked=false);exportSummary();});
  $("#export-close").addEventListener("click", () =>
    $("#export-dialog").close(),
  );
  $("#export-download").addEventListener("click", async () => {
    if (busy) return;
    const { mode, ids, issues } = exportSelection(),
      button = $("#export-download");
    if(mode!=="handoff"&&!issues.length)return;
    const query = new URLSearchParams({ mode, version, revision:String(record.revision) });
    ids.forEach((i) => query.append("item", i));
    issues.forEach((i) => query.append("issue", i));
    button.disabled = true;
    button.textContent = "正在整理与校验…";
    try {
      const r = await fetch("/api/export?" + query, {
        credentials: "same-origin",
      });
      if (!r.ok) throw Error((await r.json()).error || "导出失败");
      const blob = await r.blob(),
        url = URL.createObjectURL(blob),
        a = document.createElement("a");
      const raw = r.headers.get("Content-Disposition") || "";
      a.download = raw.includes("filename*=UTF-8''")
        ? decodeURIComponent(raw.split("filename*=UTF-8''")[1])
        : (mode==="pdf"?"问题反馈.pdf":"材料包.zip");
      a.href = url;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      notice(mode==="pdf"?"PDF已下载，可直接打开查看；未发送给任何人。":"已整理并下载，未发送给任何人。解压后按使用说明打开。");
      $("#export-dialog").close();
    } catch (e) {
      notice(e.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "整理并下载";
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if(closingPage)return;
    if (
      !offline &&
      (storageWrites || storageError || Object.keys(pendingRequests).length)
    ) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  (async () => {
    try {
      if (offline) {
        $(".local-label").textContent = "离线材料包 · 只读";
        $("#refresh").hidden = true;
        $("#inbox-open").hidden = true;
        ui.view = "materials";
        $("#export-open").hidden = true;
        notice(
          "材料快照：" +
            new Date(offline.created_at).toLocaleString() +
            "。" +
            (offline.omitted.length
              ? "有" +
                offline.omitted.length +
                "份材料未包含，详见manifest.json。"
              : "补件请交经办人登记。"),
        );
      }
      const params = new URLSearchParams(location.hash.slice(1)),
        token = params.get("access");
      if (token && !offline) {
        await request("/api/session", {
          method: "POST",
          headers: { "X-Review-Token": token },
        });
        params.delete("access");
        history.replaceState(null, "", "#" + params);
      }
      const initialHash = location.hash;
      await refresh();
      history.replaceState(null, "", initialHash || (offline ? (data.counts.issues ? "#view=review" : "#view=materials") : "#view=inbox"));
      parseHash();
      render();
      trackView();
    } catch (e) {
      notice(e.message, true);
    }
  })();
})();
