window.WorkbenchConnection = async function(context) {
  const {api,perform,tool,content,button,table,notice,onResult,isCurrent}=context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const id=()=>crypto.randomUUID().replaceAll('-','');
  const status=await api('/api/read',{tool:tool.name,arguments:{action:'status'}});if(!isCurrent())return;
  content.append(make('p',status.note,'intro'));
  const login=make('a','打开学校登录入口','link');login.href='microsoft-edge:'+status.login_url;content.append(login,make('p','请在Edge完成登录并进入网上报销系统，再回这里点击“查找已登录页面”。','help'));
  if(!status.platform.host_installed)content.append(button('安装本机连接组件',async()=>{
    const result=await perform(tool,{action:'install_bridge',execute:true},true,{show:false,summary:[['安装范围','本项目连接组件和当前Windows用户登记'],['随后操作','本人在Edge扩展页加载报销扩展']]});
    if(result?.host_registered){notice('连接组件已安装。请在Edge扩展页开启开发人员模式，加载下方扩展文件夹。');content.append(make('p',result.extension_dir||result.extension_directory||result.package_dir||JSON.stringify(result),'help'));}
    else if(result?.error)throw Error(result.error);
  }));
  if(status.platform.host_installed&&!status.platform.bridge_connected){content.append(make('h2','首次连接：加载报销扩展'),make('p','1. 在Edge地址栏输入edge://extensions并打开。2. 开启“开发人员模式”，点击“加载解压缩的扩展”。3. 选择下面的文件夹，完成后回到工作台查找已登录页面。','help'));const location=make('textarea');location.readOnly=true;location.value=status.platform.extension_directory||'';content.append(location,button('复制扩展文件夹位置',async()=>{try{await navigator.clipboard.writeText(location.value);notice('已复制扩展文件夹位置。');}catch{location.select();notice('已选中文件夹位置，请按Ctrl+C复制。');}}));}
  content.append(make('p',status.platform.bridge_connected?'本机扩展已连接。':'等待本机报销扩展连接。首次安装请按安装报告加载本项目扩展。','status-tag'));
  const account=make('input');account.type='text';account.autocomplete='username';account.setAttribute('aria-label','本人学校登录学工号');account.placeholder='本人学校登录学工号';content.append(make('h2','核对本人账号'),account);
  const candidates=make('div');content.append(candidates);
  content.append(button('查找已登录页面',async()=>{
    const result=await api('/api/read',{tool:tool.name,arguments:{action:'discover'}});candidates.replaceChildren();
    if(!result.candidates.length){candidates.append(make('p','尚未找到网上报销页面。请先登录学校入口并打开网上报销系统。'));return;}
    candidates.append(table(['可连接页面',''],result.candidates.map((row,i)=>[row.label+' '+(i+1),button('核对这个页面',()=>verify(row),true)])));
  }));
  async function verify(row){
    const who=account.value.trim();if(!/^[A-Za-z0-9]{2,40}$/.test(who))throw Error('请填写本人登录学工号。');
    let job=await perform(tool,{action:'verify',expected_userid:who,tab_id:row.tab_id,request_id:'connection-'+id()},false,{show:false});
    if(!job?.job_id)throw Error(job?.error||'没有取得核验任务。');
    notice('正在只读核对学校后端和本人身份…');
    for(let n=0;n<45;n++){
      const result=await api('/api/read',{tool:'reimbursement_result',arguments:{case_id:job.case_id,job_id:job.job_id}});
      if(!['queued','running'].includes(result.status)){
        const accepted=await perform(tool,{action:'accept',expected_userid:who,source_job_id:job.job_id},false,{show:false});
        if(accepted?.connected){if(isCurrent()){notice('本人账号已核实，可以读取单据和审批清单。');connections.replaceChildren();showConnection(accepted);}return;}
        throw Error(accepted?.error||'尚未核实账号，请检查本人登录后继续原任务。');
      }
      await new Promise(r=>setTimeout(r,1000));
    }
    notice('原核验仍在处理，请到任务与进度查看；未重复发起。');
  }
  const connections=make('section');content.append(connections);
  function showConnection(row){
    connections.append(make('h2','本人财务收件箱 · '+row.expected_userid),make('p','读取范围是此登录人的学校清单；选择活动和具体单据后才进入办理。','help'));
    for(const [action,label]of [['bills','读取单据与挂单理由'],['approvals','读取我的待审批清单'],['history','查看我的审批处理记录']])connections.append(button(label,async()=>{
      const result=await perform(tool,{action,connection_id:row.connection_id,request_id:'inbox-'+action+'-'+id()},false,{show:false});
      if(!result?.job_id)throw Error(result?.error||'未取得清单任务');if(isCurrent())onResult(result,{tool:tool.name,arguments:{case_id:row.case_id,action}});
    },action==='bills'));
  }
  status.connections.forEach(showConnection);
};
