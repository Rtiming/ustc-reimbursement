---
name: ustc-reimbursement
description: 使用本地科大报销工具整理材料、选择通用与专项规则、生成离线计划、接续授权财务操作和导出。适用于已安装本工具的用户；其他学校网页须单独适配。
compatibility: Python 3.11及以上；可使用本地CLI/JSON或stdio MCP。网页办理需当前设备已验收的学校适配器和本人登录。
---

从工具包的`AGENTS.md`和`docs/用户指南.md`进入，不依赖原作者的个人目录、历史对话或其他技能。

未安装时从install.sh/install.cmd进入；已安装先运行--check。工具会管理本目录的Python与依赖，默认资料根及当前专项记录在`.local/installation.json`。已有库追加专项用standards_add_profile预览和原registry SHA，不手改registry。

- 有MCP时先`reimbursement_capabilities(intent)`；只有命令权限时使用`python assistant.py call --input 请求JSON绝对路径 --material-root 资料根绝对路径`。
- 以本用户的私人规范库及当前案件正本为事实来源。新库用`standards_init`；专项选择用`standards_read/select`，候选规则不可冒充已确认。
- 根据任务读取`guide(topic)`。复杂参数以MCP工具schema或包内`mcp/tool_catalog.json`为准，不从方法名猜参数。
- 扫描件使用`ocr_status → material_ocr → result`，引用本案已完成收件job；原运行未结束用material_ocr_resume。默认不启动本机OCR，明确选择--with-ocr后才安装并启用本机模型。回接文字是候选，不直接更新财务字段。
- 新建案件不会带入旧人员、经费或合同决定。输入不足只补真正缺项；授权、资料就绪、执行请求和独立回读分别留证。
- 未知写结果保留原request_id/job_id。本人验证码交用户；已完成批准不重复测速。
- 若任务只需材料、清单或规则，不启动浏览器。远程OCR、表格创作或凭据能力缺失时明确报告所缺接口。

本文件是工具的路由入口；规则正文与办理合同由用户指南、正式schema和私人规范库持有，不在技能中复制一套政策。
