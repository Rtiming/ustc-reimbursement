"""审批批次的用户视图。程序任务与逐项财务回读分别显示。"""

STATES = {
    "planned": "等待核对并确认范围",
    "running": "逐项办理中",
    "verification_required": "等待本人短信验证",
    "ready_to_resume": "可以接续原批次",
    "paused": "后续项目已暂停",
    "unknown_result": "原审批结果待核实",
    "result_needs_review": "原页面已变化，待独立核对",
    "blocked_before_dispatch": "本项条件变化，尚未继续",
    "documents_pending": "审批已回读，原生文档整理待接续",
    "completed": "本批审批步骤均已回读",
    "not_started": "尚未办理",
    "awaiting_verification": "等待本人验证",
    "dispatch_intent": "原派发结果待回读",
    "verified": "本角色审批步骤已回读",
}


def project(value):
    state = value["state"]
    base = {"plan_id": value["plan_id"]}
    next_action = None
    actions = {
        "planned": ("run", "核对本批范围并确认", True),
        "verification_required": ("verification", "打开本人的学校验证页", True),
        "ready_to_resume": ("resume", "接续原批次", True),
        "paused": ("resume", "继续未完成项目", True),
        "unknown_result": ("recover", "恢复原审批结果", False),
        "documents_pending": ("finalize", "只整理已审批项目的原生文档", True),
    }
    if state in actions:
        action, label, execute = actions[state]
        next_action = {
            "kind": "call",
            "tool": "reimbursement_approver_batch",
            "arguments": {**base, "action": action, "execute": execute},
            "label": label,
            "ui_form": "approver_batch",
            "requires_user_confirmation": state == "planned",
        }
        if state == "planned":
            next_action["needed_inputs"] = [
                "用户确认所列单号、角色、金额和意见的authorization_note"
            ]
    return {
        "title": STATES.get(state, "查看原批次"),
        "summary": value.get("error") or value.get("note"),
        "system_result_verified": state == "completed",
        "whole_claim_completed": False,
        "rows": [
            {
                "单号": item["serial"],
                "摘要": item["summary"],
                "金额": item["amount"],
                "本次角色": item["role"],
                "进度": STATES.get(item["state"], item["state"]),
                "文档整理": "完成" if item.get("finalized") else "待完成",
            }
            for item in value["items"]
        ],
        "row_count": len(value["items"]),
        "next_action": next_action,
        "approval_batch": value,
    }
