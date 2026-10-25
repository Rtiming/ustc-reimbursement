/* 打开原生发票上传页面，不选文件、不上传。 */
(()=>{
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_39 button[btnname="上传发票"]')].filter(e=>e.getClientRects().length);
 if(buttons.length!==1)throw new Error('上传发票入口不唯一');
 buttons[0].click();return{ok:true,action:'open_native_invoice_upload'};
})()
