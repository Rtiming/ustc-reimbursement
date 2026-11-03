# 学生一般薪酬办理

本入口处理原件已核实的学生其他劳务，使用财务原生“薪酬发放”。不向普通发票池制造零金额票据。先按适用规范核对审批范围、签字名单、金额和活动时间；预算冲突的事项保持待确认。

案件以material_context.labor_material_review.labor_review登记逐项核对JSON的绝对路径和SHA，remuneration_document_id选定唯一事项。该记录须有同case_id、documents[].id、summary、business_date、claimed_amount、signed_total、budget_amount、invoice_count=0、amount_status=materials_matched_pending_native_validation、recipients（姓名、学号、金额）和sources（原件路径、SHA）。核对记录保留私人材料区，不进入发行包。界面执行模式见[执行模式](execution-modes.md)。

所有浏览器操作经access，带本案case_id、当前财务tab_id、expected_userid及唯一request_id。只读inspect的execute=false；其他动作execute=true。动作顺序：

1. remuneration-entry从已核首页进入薪酬清单，remuneration-student-category选择学生一般薪酬，remuneration-activity-setup从原件绑定性质和所得期间。
2. remuneration-open-person打开单笔录入；remuneration-lookup-person带名单student_id，通过原生blur查询姓名、性质、银行卡和联系方式。独立invoice_verification(surface)核对；不会编造缺失资料。
3. remuneration-add-person带student_id及查询来源job_id/artifact_id，验证原件、人员、金额、银行卡、现有清单及原生按钮定义后添加。工作事由采用原明白纸标题去掉“报销”，须不超过20字；不自动截断。添加前写入pending_write，超时不重发。
4. 看到同笔“成功添加某人”后，remuneration-confirm-added只确认原添加成功提示。随后remuneration-close-person关闭已经清空的编辑窗口，原生清单才刷新。独立surface完整核对，再record_result(kind=ack-write,outcome=saved)。不能以成功提示或未刷新空清单代替保存回读。
5. 全部人员完成后，remuneration-create-claim引用完整清单job_id/artifact_id；原生提示正确人数和总额后，remuneration-confirm-create接续原pending一次，不重新点击建单。read_system核对薪酬业务单、单号、性质、所得期间、每人金额及总额。record_result(kind=bind-serial)识别原薪酬创建意图并绑定system_mode=remuneration。
6. 已绑定单可用remuneration-basic-open、remuneration-teacher-lookup及remuneration-funding-open核实字段，经现有basic/funding分阶段保存和独立回读。正常薪酬转卡通过工资清单发放，不重复填普通转卡。remuneration-payment-inspect核对正常转卡N；remuneration-payments-open只读额外支付，全部为零/空时remuneration-payment-close返回，不保存。提交计划以remuneration_zero_payment_receipt记录此回执路径和SHA，按submission options/next核对同单全部薪酬行、期间、预算、证明人和备注，选择不委托打印，再沿原承诺、单位审批人及本人审核流程提交；不能套普通发票张数核验。

原生明确拒绝事由超过20字时，可依据同笔编辑字段、明确提示和未变清单登记not_applied；remuneration-clear-length-rejection仅清理该拒绝现场，保留原回执。其他未知结果不按此路径解除pending。

本入口的Mac真实办理与离线测试分别记录；Windows端须另行实际验收。验证码由本人完成，其他角色审批和实际付款仍以各自回执为准。

经费查询可用remuneration-funding-search，query为明确查询词。常用列表无本项时，经remuneration-funding-other-open查看原生常用他人项目；把当前唯一匹配的项目code、owner_name和来源登记到本案funding，再remuneration-funding-other-select回填。原生回填可能异步展开为多位负责人，应等待联动后按完整姓名匹配，不把回填更新当作项目丢失。remuneration-funding-other-add记录原添加意图；独立清单核对项目、名称、负责人后ack-write，随后经原funding阶段保存。历史卡号仅作查找候选，不能替代当前原生核对。

当前模式为system_mode=remuneration，创建来源按SHA绑定；普通stage仅开放basic和funding，其他环节使用上述薪酬入口，避免重复支付。短信验证仍由本人完成。
