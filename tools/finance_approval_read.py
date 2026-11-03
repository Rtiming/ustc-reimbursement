"""从当前审批人的完整清单冻结原单来源；不借用申请人身份或建立审批授权。"""

import hashlib
import re


def source_context(service, request):
    case = request["case_id"]
    parameters = request["parameters"]
    job = parameters["source_job_id"]
    who = parameters["expected_userid"]
    result = service.result(case, job)
    artifacts = [a for a in result.get("artifacts", []) if a["name"] == "output.json"]
    if (
        result.get("status") != "completed"
        or result.get("exit_code") != 0
        or len(artifacts) != 1
    ):
        raise ValueError("审批来源须完整成功的原清单任务")
    path, envelope, original = service._typed_case_artifact(
        case,
        job,
        artifacts[0]["artifact_id"],
        "business",
        ["approvals-list", "approval-history"],
    )
    value = envelope.get("result", {})
    rows = value.get("rows")
    history = original["parameters"]["action"] == "approval-history"
    if (
        original.get("execute") is not False
        or original["parameters"].get("expected_userid") != who
        or envelope.get("phase") != "script_completed"
        or value.get("ok") is not True
        or value.get("complete") is not True
        or value.get("case_id") != case
        or value.get("expected_userid") != who
        or value.get("system_write") is not False
        or value.get("business_transport") != "native_http"
        or value.get("action") != original["parameters"]["action"]
        or value.get("account_scope")
        != ("current_approver_history" if history else "current_approver")
        or not isinstance(rows, list)
        or type(value.get("total_records")) is not int
        or value["total_records"] != len(rows)
    ):
        raise ValueError("审批来源须同案同账号的完整当前审批人清单")
    selected = [
        r for r in rows if r.get("t.serial_no") == parameters["reference_serial"]
    ]
    if not selected:
        raise ValueError("所选单号不在原审批清单中")
    workflows = {r.get("t.wfname") for r in selected}
    instances = {r.get("t.uni_key") for r in selected}
    if (
        len(workflows) != 1
        or len(instances) != 1
        or any(not re.fullmatch("[A-Za-z0-9_]{1,80}", v or "") for v in workflows)
        or any(not re.fullmatch("[0-9]{1,20}", v or "") for v in instances)
    ):
        raise ValueError("原审批记录的流程或实例不唯一，不能猜测归属")
    return {
        "serial": parameters["reference_serial"],
        "workflow": next(iter(workflows)),
        "instance": next(iter(instances)),
        "source_rows": selected,
        "source": {
            "case_id": case,
            "job_id": job,
            "artifact_id": artifacts[0]["artifact_id"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
