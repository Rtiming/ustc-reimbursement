/* Fixed read-only backend check. No navigation, credentials, retries or business writes. */
async function reimbursementBackendHealth(expectedUser) {
  if (!/^[A-Za-z0-9]{2,40}$/.test(expectedUser)) throw Error('BACKEND_HEALTH_USER');
  const result = {ok:false, business_transport:'native_http', system_write:false,
    navigation_requested:false, retry_requested:false, identity_verified:false,
    backend_verified:false, observed_at:new Date().toISOString()};
  if (location.origin !== 'https://cwzh.ustc.edu.cn') return {...result,state:'wrong_origin'};
  if (location.pathname === '/WFManager/home2.jsp') {
    const raw = document.getElementById('YBX')?.getAttribute('url') || '';
    let offered = false;
    if (raw.startsWith('WINOPEN')) {
      try {
        const u = new URL(raw.slice(7),location.href);
        offered = u.origin === location.origin && u.pathname === '/YBX/main2.jsp' &&
          !u.username && !u.password && JSON.stringify([...u.searchParams.keys()].sort()) === JSON.stringify(['context','pId']) &&
          ['context','pId'].every(k=>u.searchParams.get(k));
      } catch (_) {}
    }
    return {...result,state:offered?'entry_available_not_opened':'entry_contract_unavailable',entry_available:!!offered};
  }
  if (location.pathname !== '/YBX/main.jsp') return {...result,state:'wrong_page'};
  const identity = () => window.jQuery?.getProjID?.() === 'WF_YBX' &&
    window.jQuery?.UC?.bindData?.('#0-userinfo.userid#') === expectedUser;
  if (!identity()) return {...result,state:'identity_mismatch'};
  result.cached_page_identity_verified = true;
  const requests=[];
  try {
    // 与正式业务复用学校jQuery请求和返回解包；原始fetch不经过原生协议，可能误判返回结构。
    const http=createReimbursementHttp(expectedUser,{prefix:'BACKEND_HEALTH',requests,timeout:5000,allowed:['definition']});
    const value=await http.post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'});
    result.http_status=200;
    if (!Array.isArray(value?.wins) || !value.wins.some(w=>String(w.winno)==='70') ||
        !Array.isArray(value?.funcs) || !value.funcs.some(f=>String(f.funcno)==='101' &&
          f.multifields==='t.serial_no' && f.colModel?.some(c=>c.name==='t.REQ_INFO')))
      return {...result,state:'backend_contract_changed',definition_shape:{
        keys:value && typeof value==='object'?Object.keys(value):[],
        wins:Array.isArray(value?.wins)?value.wins.map(w=>String(w.winno)):[],
        functions:Array.isArray(value?.funcs)?value.funcs.map(f=>String(f.funcno)):[]}};
    if (!identity()) return {...result,state:'identity_changed'};
    return {...result,ok:true,state:'native_backend_ready',identity_verified:true,backend_verified:true,
      current_serial_hint:window.jQuery.UC.bindData('#61-serialno#') || null};
  } catch (error) {
    if(!identity()) return {...result,state:'identity_changed'};
    const trace=requests.at(-1);result.http_status=trace?.http_status??null;
    if(result.http_status && result.http_status>=400) return {...result,state:'native_backend_unavailable',
      recovery_hint:[401,403,404].includes(result.http_status)?'fresh_interface_and_login_required':'backend_status_requires_diagnosis',
      server_outage_established:false};
    return {...result,state:String(error.message).includes(':timeout:')?'backend_timeout':'backend_unconfirmed'};
  }
}
