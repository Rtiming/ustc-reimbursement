/* 展示和复制同一冻结清单；搜索不让隐藏单据进入本次复制范围。 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const report = JSON.parse(byId('report').textContent);
  const rows = byId('rows');
  const chosen = new Set(report.rows.filter(row => row.category !== 'history').map(row => row.serial));
  let visible = [];

  function element(tag, content, className) {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  }

  function block(row) {
    return `报销单：${row.serial}\n事项：${row.summary}\n金额：${row.amount_yuan}元\n` +
      `状态：${row.status}（${row.category_label}）\n财务反馈原文：\n` +
      `${row.reason_available ? row.reason_text : '系统未返回理由，待核实'}\n采集时间：${row.observed_at}`;
  }

  const selected = () => visible.filter(row => chosen.has(row.serial));

  function updateSelection() {
    const count = selected().length;
    const hidden = chosen.size - count;
    byId('copy').textContent = `复制当前所选（${count}张）`;
    byId('copy').disabled = !count;
    byId('download').disabled = !count;
    byId('select').disabled = !visible.length;
    byId('select').textContent = visible.length && count === visible.length ? '取消当前选择' : '选择当前结果';
    byId('selection-scope').textContent = `当前显示${visible.length}张，已选${count}张。` +
      (hidden ? `另有${hidden}张被筛选隐藏，不会进入本次复制或下载。` : '复制和下载仅包含当前结果内的已选单据。');
  }

  async function copy(content) {
    try {
      await navigator.clipboard.writeText(content);
      byId('status').textContent = '已复制完整原文，可直接粘贴。';
    } catch {
      byId('manual-text').value = content;
      byId('manual').showModal();
      byId('manual-text').focus();
      byId('manual-text').select();
    }
  }

  function render() {
    rows.replaceChildren();
    const query = byId('query').value.trim().toLowerCase();
    visible = report.rows.filter(row => [row.serial, row.summary, row.reason_text, row.status].join('\n').toLowerCase().includes(query));
    for (const row of visible) {
      const article = document.createElement('article');
      const line = element('div', '', 'row-actions');
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = chosen.has(row.serial);
      check.setAttribute('aria-label', `选择报销单${row.serial}`);
      check.onchange = () => {
        check.checked ? chosen.add(row.serial) : chosen.delete(row.serial);
        byId('status').textContent = '';
        updateSelection();
      };
      line.append(check, element('h2', `${row.serial} · ${row.summary}`));
      article.append(line, element('span', row.category_label, 'badge'));
      const meta = element('p', '', 'meta');
      meta.append(element('span', `金额 ${row.amount_yuan}元`), element('span', row.status));
      // 只为阅读在编号前换行，复制始终取未整理的reason_text。
      const display = row.reason_available ? row.reason_text.replace(/(?<![0-9])([1-9][0-9]?、)/g, '\n$1').trimStart() : '系统未返回理由，待核实';
      article.append(meta);
      if (row.reason_text.length > 1200) {
        const details = document.createElement('details');
        details.append(element('summary', `展开完整原文（${[...row.reason_text].length}字符）`), element('pre', display));
        article.append(details);
      } else {
        article.append(element('pre', display));
      }
      const single = element('button', '复制本单原文');
      single.onclick = () => copy(block(row));
      article.append(single);
      rows.append(article);
    }
    if (!visible.length) rows.append(element('p', '当前筛选没有反馈记录。'));
    updateSelection();
  }

  byId('summary').textContent = `本次核对${report.total_bills}张本人单据；财务挂单${report.counts.finance_returned}张，审批退回${report.counts.approval_returned}张，状态待核${report.counts.state_unverified}张。`;
  byId('observed').textContent = '采集时间：' + new Date(report.observed_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) +
    '（北京时间）' + (report.omitted_history_count ? ` · 已分离${report.omitted_history_count}条历史意见` : '');
  byId('query').oninput = () => { byId('status').textContent = ''; render(); };
  byId('select').onclick = () => {
    const all = visible.every(row => chosen.has(row.serial));
    for (const row of visible) all ? chosen.delete(row.serial) : chosen.add(row.serial);
    byId('status').textContent = '';
    render();
  };
  byId('copy').onclick = () => copy(selected().map(block).join('\n\n'));
  byId('download').onclick = () => {
    const link = document.createElement('a');
    const url = URL.createObjectURL(new Blob([selected().map(block).join('\n\n')], {type: 'text/plain;charset=utf-8'}));
    link.href = url;
    link.download = '财务反馈原文.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  byId('close').onclick = () => byId('manual').close();
  render();
})();
