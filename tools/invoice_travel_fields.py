"""从已取得的票面文字提取行程候选，不用开票日替代行程日，不猜到达日或舱位。"""
import re
from datetime import date

DATE=r'([0-9]{4})[-/年]([0-9]{1,2})[-/月]([0-9]{1,2})日?'
CITY=r'[\u4e00-\u9fffA-Za-z]{2,20}'
FLIGHT=r'[A-Z0-9]{2}[0-9]{3,4}'


def extract_travel(text):
    segments=[]
    # 单行携程备注按航班号截断城市，避免把下一字段“航班号”读入到达城市。
    inline=re.compile(DATE+rf'\s+({CITY})\s*[-→—]\s*({CITY})\s+({FLIGHT})\s+([\u4e00-\u9fff]{{2,8}})(?:\s+(经济舱|公务舱|头等舱|优选飞))?')
    for m in inline.finditer(text):
        try: day=date(*map(int,m.groups()[:3])).isoformat()
        except ValueError: continue
        start,end,flight,person,product=m.groups()[3:]
        segments.append({'departure_date':day,'departure_city':start,'arrival_city':end,'flight':flight,'traveler':person,
            'arrival_date':None,'seat_class':product if product in ['经济舱','公务舱','头等舱'] else None,
            'fare_product':product,'evidence_text':m.group(0),'offset':m.start()})
    if not segments:
        route=re.search(r'行程信息\s*[:：]\s*'+DATE+rf'\s+({CITY})\s*[-→—]\s*({CITY})(?=\s|$)',text)
        person=re.search(r'乘机人\s*[:：]\s*([^\s，,;；]+)',text)
        flight=re.search(r'航班号\s*[:：]\s*('+FLIGHT+r')(?![0-9])',text)
        cabin=re.search(r'舱位\s*[:：]\s*(经济舱|公务舱|头等舱)',text)
        if route:
            try: day=date(*map(int,route.groups()[:3])).isoformat()
            except ValueError: day=None
            if day:
                segments.append({'departure_date':day,'departure_city':route[4],'arrival_city':route[5],
                    'flight':flight[1] if flight else None,'traveler':person[1] if person else None,'arrival_date':None,
                    'seat_class':cabin[1] if cabin else None,'fare_product':None,
                    'evidence_text':text[route.start():max(x.end() for x in [route,person,flight,cabin] if x)],'offset':route.start()})
    required=['traveler','departure_date','arrival_date','seat_class']
    return {'schema':'ustc-invoice-travel-fields-v1','segments':segments,'status':'text_candidates' if segments else 'not_extracted',
            'missing_fields':sorted({k for segment in segments for k in required if not segment.get(k)}) if segments else required,
            'person_id_source':'本案名单另行匹配；票面姓名不自动生成学工号','financial_system_applied':False}
