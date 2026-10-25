"""Missing requirements in the complete native reimbursement preview."""


def material_gaps(frame):
    rows=[]
    for table in frame.get('tables',[]):
        for row in table:
            if not isinstance(row,list) or any(not isinstance(cell,str) for cell in row):continue
            explicit_error=any('*错误*' in cell or '尚未绑定' in cell for cell in row)
            empty_required_supplement=(len(row)==4 and bool(row[0].strip()) and bool(row[1].strip())
                                       and not row[2].strip() and row[3].strip()=='请填写说明并上传附件')
            required_asset_register=(len(row)==4 and row[0].startswith('固定资产登记单')
                                     and row[1].replace(' ','')=='共0笔' and '绑定固定资产登记单' in row[2])
            if explicit_error or empty_required_supplement or required_asset_register:rows.append(row)
    return rows
