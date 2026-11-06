import os
import re
import json
import subprocess
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
import requests
from datetime import datetime
import glob as glob_module

app = Flask(__name__, template_folder='.')


def cors_response(data):
    """Wrap jsonify response with CORS headers"""
    response = make_response(data)
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Access-Control-Allow-Methods', 'POST')
    return response


@app.after_request
def add_cors_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
    return response


YOUR_API_KEY = "******"
BASE_URL = "******"
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct"

MODEL_API_KEY = YOUR_API_KEY
MODEL_ENDPOINT = f"{BASE_URL}/chat/completions"

PROBAS_FILE = r"******"


DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "pubmed_search",
            "description": "检索经过同行评审的权威生物医学文献，返回标题、作者、期刊、发表年份和摘要，是临床医生获取循证证据的首选数据库",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Medical topic or condition"},
                    "limit": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "openalex_search",
            "description": "检索覆盖全学科的开放学术知识图谱，返回标题、作者、DOI和摘要，适合跨学科研究和学术影响力分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Research topic"},
                    "limit": {"type": "integer", "default": 5},
                    "year_from": {"type": "integer", "description": "From year"},
                    "year_to": {"type": "integer", "description": "To year"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "europepmc_search",
            "description": "检索生物医学文献及其开放获取全文，返回标题、PMCID和摘要，特别适合需要免费获取完整论文的场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Biomedical topic"},
                    "limit": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drug_label_search",
            "description": "Search FDA drug labels to check medication indications, warnings, and contraindications. Use when patient is taking a medication you want to verify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "drug_name": {"type": "string", "description": "Drug name (e.g., norepinephrine, vasopressin, hydrocortisone)"}
                },
                "required": ["drug_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_small_model_prediction",
            "description": "获取小模型预测概率作为参考（基于机器学习）。重要：以患者临床信息为准，大模型应有自己的判断，不应完全依赖小模型结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "患者ID"}
                },
                "required": ["patient_id"]
            }
        }
    },
]


def pubmed_search(query, limit=5):
    try:
        import xml.etree.ElementTree as ET

        # 1. 搜索获取PMID列表
        url = "******"
        params = {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json"}
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"status": "success", "query": query, "count": 0, "results": [], "references": []}

        # 2. 获取完整文献记录（含摘要）- 使用efetch
        fetch_url = "******"
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
            "rettype": "abstract"
        }
        resp2 = requests.get(fetch_url, params=fetch_params, timeout=30)

        # 3. 解析XML
        results = []
        references = []
        try:
            root = ET.fromstring(resp2.text)
            for article in root.findall(".//PubmedArticle"):
                pmid_elem = article.find(".//PMID")
                title_elem = article.find(".//ArticleTitle")
                source_elem = article.find(".//Journal/Title")
                pubdate_elem = article.find(".//PubDate")

                # 获取作者
                author_names = []
                for author in article.findall(".//Author")[:3]:
                    lastname = author.findtext("LastName", "")
                    foreiname = author.findtext("ForeName", "")
                    if lastname:
                        author_names.append(f"{foreiname} {lastname}".strip() if foreiname else lastname)

                # 获取摘要 - 处理多段落
                abstract_parts = []
                for abs_elem in article.findall(".//AbstractText"):
                    label = abs_elem.get("Label", "")  # 如 "METHODS", "RESULTS"
                    text = abs_elem.text or ""
                    if text:
                        if label:
                            abstract_parts.append(f"[{label}] {text}")
                        else:
                            abstract_parts.append(text)
                abstract = " ".join(abstract_parts)

                # 获取发表年份
                year = "n.d."
                if pubdate_elem is not None:
                    year = pubdate_elem.findtext("Year", "n.d.")

                pmid = pmid_elem.text if pmid_elem is not None else ""
                title = title_elem.text if title_elem is not None else ""
                source = source_elem.text if source_elem is not None else ""

                if pmid and title:
                    results.append({
                        "pmid": pmid,
                        "title": title,
                        "authors": author_names,
                        "source": source,
                        "pubdate": year,
                        "abstract": abstract[:1000] if abstract else ""  # 截断长摘要
                    })
                    author_str = ", ".join(author_names[:2]) if author_names else "Unknown"
                    references.append({
                        "source": f"PMID: {pmid}",
                        "detail": f"{title} ({author_str} et al., {year}) - {source}"
                    })
        except ET.ParseError as e:
            print(f"[WARN] XML parse error: {e}")

        return {"status": "success", "query": query, "count": len(results), "results": results, "references": references}
    except Exception as e:
        return {"status": "error", "message": str(e), "references": []}


def openalex_search(query, limit=5, year_from=None, year_to=None):
    try:
        filters = []
        if year_from:
            filters.append(f"publication_year:>={year_from}")
        if year_to:
            filters.append(f"publication_year:<={year_to}")

        url = "******"
        params = {"search": query, "per-page": limit,
                "filter": ",".join(filters) if filters else None}
        resp = requests.get(url, params=params, timeout=30)

        results = []
        references = []
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("results", []):
                authors = item.get("authorships", [])[:3]
                names = [a.get("author", {}).get("display_name", "") for a in authors]
                title = item.get("title", "")
                doi = item.get("doi", "")
                year = item.get("publication_year", "")
                results.append({"title": title, "doi": doi, "year": year, "authors": names})
                if doi:
                    references.append({"source": doi, "detail": f"OpenAlex: {title} ({year})"})
        return {"status": "success", "results": results, "references": references}
    except Exception as e:
        return {"status": "error", "message": str(e), "references": []}


def europepmc_search(query, limit=5):
    try:
        import xml.etree.ElementTree as ET

        # 1. 先搜索获取结果（包含摘要）
        url = "******"
        params = {
            "query": query,
            "resulttype": "fullText",  # 获取全文/摘要
            "format": "json",
            "pageSize": limit
        }
        resp = requests.get(url, params=params, timeout=30)

        results = []
        references = []
        if resp.status_code == 200:
            try:
                data = resp.json()
                for item in data.get("resultList", {}).get("result", []):
                    pmid = item.get("pmid", "")
                    pmcid = item.get("pmcid", "")
                    title = item.get("title", {}).get("fullText", "") if isinstance(item.get("title"), dict) else item.get("title", "")
                    source = item.get("pub", {}).get("containerTitle", "") if isinstance(item.get("pub"), dict) else item.get("pubTitle", "")

                    # 获取摘要 - EuropePMC从abstract字段获取
                    abstract = item.get("abstractInfo", {}).get("abstract", "") if isinstance(item.get("abstractInfo"), dict) else ""
                    if not abstract:
                        # 尝试其他方式获取摘要
                        abstract = item.get("abstract", "")

                    results.append({
                        "pmid": pmid,
                        "pmcid": pmcid,
                        "title": title,
                        "source": source,
                        "abstract": abstract[:1000] if abstract else ""
                    })
                    if pmid:
                        references.append({"source": f"PMID: {pmid}", "detail": f"EuropePMC: {title}"})
            except Exception as e:
                print(f"[WARN] EuropePMC JSON parse error: {e}, trying XML...")
                # JSON解析失败，尝试XML解析
                try:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//result")[:limit]:
                        pmid = item.findtext("pmid", "")
                        pmcid = item.findtext("pmcid", "")
                        title = item.findtext("title", "")
                        journal = item.findtext(".//journal/title", "")

                        # 尝试获取摘要
                        abstract_elems = item.findall(".//abstract")
                        abstract_parts = []
                        for abs_elem in abstract_elems:
                            label = abs_elem.get("label", "")
                            text = abs_elem.text or ""
                            if text:
                                if label:
                                    abstract_parts.append(f"[{label}] {text}")
                                else:
                                    abstract_parts.append(text)
                        abstract = " ".join(abstract_parts)

                        results.append({
                            "pmid": pmid,
                            "pmcid": pmcid,
                            "title": title,
                            "source": journal,
                            "abstract": abstract[:1000] if abstract else ""
                        })
                        if pmid:
                            references.append({"source": f"PMID: {pmid}", "detail": f"{title} ({journal})"})
                except ET.ParseError as xe:
                    print(f"[WARN] EuropePMC XML parse error: {xe}")

        return {"status": "success", "results": results, "references": references}
    except Exception as e:
        return {"status": "error", "message": str(e), "references": []}


# 中文字典：常见中文药名/商品名 → 英文名
CHINESE_DRUG_DICT = {
    # 血管活性药物
    "去甲肾上腺素": "norepinephrine",
    "去甲肾": "norepinephrine",
    "noradrenaline": "norepinephrine",
    "肾上腺素": "epinephrine",
    "adrenaline": "epinephrine",
    "多巴胺": "dopamine",
    "dobutamine": "dobutamine",
    "多巴酚丁胺": "dobutamine",
    "vasopressin": "vasopressin",
    "血管加压素": "vasopressin",
    "垂体后叶素": "vasopressin",
    "去氧肾上腺素": "phenylephrine",
    "苯肾上腺素": "phenylephrine",
    "phenylephrine": "phenylephrine",
    "间羟胺": "metaraminol",
    "阿拉明": "metaraminol",
    "metaraminol": "metaraminol",
    "硝普钠": "nitroprusside",
    "nitroprusside": "nitroprusside",
    "硝酸甘油": "nitroglycerin",
    "nitroglycerin": "nitroglycerin",
    "氨力农": "amrinone",
    "米力农": "milrinone",
    "milrinone": "milrinone",
    "amrinone": "amrinone",

    # 皮质激素类
    "氢化可的松": "hydrocortisone",
    "hydrocortisone": "hydrocortisone",
    "地塞米松": "dexamethasone",
    "dexamethasone": "dexamethasone",
    "甲泼尼龙": "methylprednisolone",
    "甲基强的松龙": "methylprednisolone",
    "methylprednisolone": "methylprednisolone",
    "泼尼松": "prednisone",
    "prednisone": "prednisone",
    "泼尼松龙": "prednisolone",
    "prednisolone": "prednisolone",

    # 抗生素类
    "青霉素": "penicillin",
    "penicillin": "penicillin",
    "阿莫西林": "amoxicillin",
    "amoxicillin": "amoxicillin",
    "头孢类": "cephalosporin",
    "头孢呋辛": "cefuroxime",
    "cefuroxime": "cefuroxime",
    "头孢曲松": "ceftriaxone",
    "ceftriaxone": "ceftriaxone",
    "头孢他啶": "ceftazidime",
    "ceftazidime": "ceftazidime",
    "万古霉素": "vancomycin",
    "vancomycin": "vancomycin",
    "替考拉宁": "teicoplanin",
    "美罗培南": "meropenem",
    "meropenem": "meropenem",
    "亚胺培南": "imipenem",
    "imipenem": "imipenem",

    # 镇静镇痛类
    "丙泊酚": "propofol",
    "propofol": "propofol",
    "芬太尼": "fentanyl",
    "fentanyl": "fentanyl",
    "吗啡": "morphine",
    "morphine": "morphine",
    "瑞芬太尼": "remifentanil",
    "remifentanil": "remifentanil",
    "右美托咪定": "dexmedetomidine",
    "dexmedetomidine": "dexmedetomidine",
    "咪达唑仑": "midazolam",
    "midazolam": "midazolam",
    "劳拉西泮": "lorazepam",
    "lorazepam": "lorazepam",

    # 肌松药
    "琥珀胆碱": "succinylcholine",
    "succinylcholine": "succinylcholine",
    "罗库溴铵": "rocuronium",
    "rocuronium": "rocuronium",
    "维库溴铵": "vecuronium",
    "vecuronium": "vecuronium",
    "阿曲库铵": "atracurium",
    "atracurium": "atracurium",

    # 其他常用药
    "胺碘酮": "amiodarone",
    "amiodarone": "amiodarone",
    "利多卡因": "lidocaine",
    "lidocaine": "lidocaine",
    "阿托品": "atropine",
    "atropine": "atropine",
    "肝素": "heparin",
    "heparin": "heparin",
    "胰岛素": "insulin",
    "insulin": "insulin",
    "速尿": "furosemide",
    "furosemide": "furosemide",
    "呋塞米": "furosemide",
}


def translate_drug_name(drug_name):
    """将中文药名翻译为英文，支持本地字典和大模型两种方式"""
    drug_lower = drug_name.lower().strip()

    # 1. 优先查本地字典
    if drug_lower in CHINESE_DRUG_DICT:
        english_name = CHINESE_DRUG_DICT[drug_lower]
        print(f"[TRANSLATE] Local dict: '{drug_name}' -> '{english_name}'")
        return english_name

    # 2. 直接返回原输入（假设已是英文）
    if drug_lower.isascii():
        return drug_name

    # 3. 本地字典没有，调用大模型翻译
    print(f"[TRANSLATE] Calling AI to translate: '{drug_name}'")
    try:
        translate_prompt = f"""Translate this Chinese drug name to English (generic name or brand name).
Just output the English name, nothing else.

Chinese: {drug_name}
English:"""

        resp = requests.post(
            MODEL_ENDPOINT,
            headers={"Authorization": "Bearer " + MODEL_API_KEY, "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": translate_prompt}], "temperature": 0.1, "max_tokens": 50},
            timeout=30
        )

        if resp.status_code == 200:
            data = resp.json()
            translated = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            # 清理可能的引号或解释性文字
            translated = translated.strip().split('\n')[0].strip('"\' ')
            if translated and len(translated) < 50:
                print(f"[TRANSLATE] AI translated: '{drug_name}' -> '{translated}'")
                return translated
    except Exception as e:
        print(f"[TRANSLATE] AI translation failed: {e}")

    return drug_name  # 都失败则返回原输入


def drug_label_search(drug_name):
    try:
        # 支持跨语言：先翻译为英文
        english_drug = translate_drug_name(drug_name)

        url = "******"

        # 请求头，避免FDA API拒绝
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TCF-Predictor/1.0)"}

        # 多种查询策略
        search_queries = [
            'openfda.brand_name:"' + english_drug + '"',
            'openfda.generic_name:"' + english_drug + '"',
            'openfda.substance_name:"' + english_drug + '"',
            'openfda.brand_name:' + english_drug,  # 不带引号，模糊匹配
            'openfda.generic_name:' + english_drug,
        ]

        results = []
        references = []

        for query in search_queries:
            try:
                params = {"search": query, "limit": 5}
                resp = requests.get(url, params=params, headers=headers, timeout=30)

                if resp.status_code != 200:
                    continue

                data = resp.json()
                items = data.get("results", [])
                if not items:
                    continue

                for item in items:
                    brand = item.get("openfda", {}).get("brand_name", [None])[0]
                    generic = item.get("openfda", {}).get("generic_name", [None])[0]
                    mfr = item.get("openfda", {}).get("manufacturer_name", [None])[0]
                    indications = item.get("indications_and_usage", [""])[0]
                    warnings = item.get("warnings", [""])[0]

                    # 去重：避免重复添加相同药品
                    if brand and not any(r.get("brand_name") == brand for r in results):
                        results.append({
                            "brand_name": brand,
                            "generic_name": generic,
                            "manufacturer": mfr,
                            "indications": indications,
                            "warnings": warnings
                        })
                        references.append({
                            "source": "FDA Drug Label",
                            "detail": f"{brand} ({generic}) - Indications"
                        })

                if results:  # 已有结果就退出
                    break
            except Exception as e:
                print(f"[WARN] FDA query '{query}' failed: {e}")
                continue

        return {"status": "success", "drug": drug_name, "translated": english_drug, "count": len(results), "results": results, "references": references}
    except Exception as e:
        return {"status": "error", "message": str(e), "references": []}


def execute_tool(function_name, arguments):
    print(f"[TOOL] Executing: {function_name}({arguments})")

    func_map = {
        "pubmed_search": lambda: pubmed_search(arguments.get("query"), arguments.get("limit", 5)),
        "openalex_search": lambda: openalex_search(arguments.get("query"), arguments.get("limit", 5),
                                            arguments.get("year_from"), arguments.get("year_to")),
        "europepmc_search": lambda: europepmc_search(arguments.get("query"), arguments.get("limit", 5)),
        "drug_label_search": lambda: drug_label_search(arguments.get("drug_name")),
        "get_small_model_prediction": lambda: get_small_model_probability_via_tool(arguments.get("patient_id")),
    }

    if function_name in func_map:
        try:
            result = func_map[function_name]()
            print(f"[TOOL] Result: {str(result)}...")
            return result
        except Exception as e:
            print(f"[TOOL] Error: {e}")
            return [{"status": "error", "message": str(e)}]
    else:
        return [{"status": "error", "message": "Unknown function: " + function_name}]


# ================================================
# QWEN API CALLER
# ================================================

def call_qwen(prompt, system_prompt="", tools=None, tool_calls=None):
    if not MODEL_API_KEY:
        return {"error": "No API Key"}

    headers = {"Authorization": "Bearer " + MODEL_API_KEY, "Content-Type": "application/json"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": MODEL_NAME, "messages": messages, "temperature": 0.1, "max_tokens": 16000}

    # 启用 tools
    if tools:
        payload["tools"] = tools

    print(f"[DEBUG] Final payload: {list(payload.keys())}")

    if tool_calls:
        print(f"[DEBUG] Tool calls: {json.dumps(tool_calls)[:300]}")
        assistant_msg = {"role": "assistant", "content": None}
        if isinstance(tool_calls, list):
            assistant_msg["tool_calls"] = tool_calls
        else:
            assistant_msg["tool_calls"] = [tool_calls]
        messages.append(assistant_msg)

        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id")
                messages.append({"role": "tool", "tool_call_id": tc_id,
                             "content": json.dumps(tc.get("result", ""))})

        payload["messages"] = messages

    try:
        resp = requests.post(MODEL_ENDPOINT, headers=headers, json=payload, timeout=600)
        print(f"[DEBUG] API response status: {resp.status_code}")
        print(f"[DEBUG] API response text: {resp.text[:1000]}")
        data = resp.json()
        # OpenAI 格式
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"]
        return {"error": data.get("message", str(data))}
    except requests.exceptions.ConnectionError as e:
        return {"error": "Network error: " + str(e)}
    except Exception as e:
        return {"error": str(e)}


def get_small_model_probability(patient_id):
    try:
        df = pd.read_excel(PROBAS_FILE)
        pid = str(patient_id).strip()
        match = df[df["ID"].astype(str).str.strip() == pid]
        if not match.empty:
            prob = float(match.iloc[0]["probas"]) * 100
            print(f"[DEBUG] Small model: ID={patient_id}, prob={prob:.1f}%")
            return prob
    except Exception as e:
        print(f"Small model error: {e}")
    return None


def get_small_model_probability_via_tool(patient_id):
    """供工具调用的小模型查询函数"""
    print(f"[DEBUG] get_small_model_prediction called with patient_id: {patient_id}, type: {type(patient_id)}")
    prob = get_small_model_probability(patient_id)
    if prob is not None:
        return {"status": "success", "patient_id": patient_id, "probability": f"{prob:.1f}%", "note": "小模型预测结果 (AUC~0.77)，仅供参考"}
    else:
        return {"status": "not_found", "patient_id": patient_id, "message": "未找到该患者ID的预测数据"}


# ================================================
# FLASK ROUTES
# ================================================

@app.route("/")
def index():
    return send_from_directory(".", "predictor.html")


@app.route("/predict", methods=["POST", "OPTIONS"])
def predict_route():
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response

    data = request.json
    patient_id = data.get("patient_id", "").strip()
    patient_info = data.get("patient_info", "").strip()

    print("\n" + "="*50)
    print(f"收到预测请求 | Patient ID: {patient_id}")
    print(f"临床信息长度: {len(patient_info)} 字符")
    print("="*50)

    if not patient_id:
        return jsonify({"error": "Patient ID is required"})
    if not patient_info:
        return jsonify({"error": "Patient Clinical Information is required"})

    try:
        patient_id = data.get("patient_id", "").strip()

        system_prompt_val = """You are an ICU doctor specializing in critical care medicine. A patient has been diagnosed with septic shock and admitted to ICU.

Your task is to predict the patient's 28-day all-cause mortality risk based on their clinical information.

你需要完成以下任务：
1. 分析患者情况：详细分析患者的病史、诊断、当前生命体征、实验室检查结果、治疗措施等
2. 时序观察：分析患者临床信息时，要注意各项指标随时间的变化趋势（如白细胞计数、降钙素原、血小板、血压等指标的动态变化），识别病情是在改善还是恶化
3. 多维度考虑：综合考虑以下多个维度：血流动力学状态（是否需要血管活性药物、血压维持情况）、感染严重程度（病原学、部位、降钙素原等）、器官功能障碍（肝肾功能、凝血功能、意识状态等）、治疗干预（血液净化、机械通气、ICU住院时间等）、基础疾病和年龄等因素
4. 做出决策：基于以上分析，给出28天死亡风险的预测概率
5. 提出临床建议：根据患者当前情况，提出可能的治疗建议和需要注意的临床问题

重要原则：你最终的预测应基于患者临床信息和你自己的专业判断。任何来自工具（小模型、文献检索、药品说明书等）的结果都仅作参考。你需要关注患者的具体临床细节来支撑预测，而不是简单给出"中立"的50%概率。

你可以根据患者的临床情况和诊断需求，自主决定是否调用以下工具来查证医学证据。

AVAILABLE TOOLS (建议调用以获取循证证据支撑预测，如果患者信息中涉及药品，必须使用drug_label_search工具):
- pubmed_search: 检索经过同行评审的权威生物医学文献，返回标题、作者、期刊和摘要
- openalex_search: 检索覆盖全学科的开放学术知识图谱，返回标题、作者、DOI和摘要
- europepmc_search: 检索生物医学文献及其开放获取全文，返回标题、PMCID和摘要
- drug_label_search: 搜索FDA药品说明书，注意患者信息中的药名可能是中文商品名/通用名，而FDA仅支持英文，需灵活匹配，了解药物适应症、警告和禁忌
- get_small_model_prediction: 获取小模型预测概率（仅作参考，基于患者信息做出自己的判断更重要）

重要提醒：以患者临床信息为准做出你的判断，必要时可参考小模型进行对比验证，但不应完全依赖小模型的具体数值。如果引用了参考文献，需要对文献的质量进行独立判断，不可盲目参考，基于文献的摘要和具体内容进行分析，不可盲目照搬或猜测。

Output JSON format:
{{"analysis": "详细分析患者的病史、诊断、各指标变化趋势、多维度风险评估、预后判断依据和临床建议", "used_tools": ["pubmed_search", "openalex_search"], "use_small_model": true/false, "final_probability": XX, "references": [{{"source": "PMID: 12345678", "detail": "Paper title"}}]}}"""

        user_prompt_val = f"""PatientID: {patient_id}

Patient clinical info:
{patient_info}

目标是：预测该患者的28天全因死亡率。

请根据患者临床信息，自主决定需要调用哪些工具来收集证据，以支持最终的预测决策。

- 如果患者临床信息中涉及药品，必须调用 drug_label_search 查询该药物的FDA说明书（适应症、警告、禁忌）。注意：患者信息中的药名可能是中文商品名/通用名，而FDA数据库仅支持英文，需灵活匹配
- 建议调用 pubmed_search / openalex_search / europepmc_search 检索相关医学文献，获取临床证据支撑
"""

        # 只有DEFINITIONS中的工具可用
        all_tools = DEFINITIONS
        print(f"[DEBUG] Total tools available: {len(all_tools)}")

        # Round 1: Send with tools
        print("\n>>> 阶段1: 发送患者信息给大模型，决定调用哪些工具...")
        result1 = call_qwen(user_prompt_val, system_prompt_val, tools=all_tools)

        if isinstance(result1, dict) and "error" in result1:
            return jsonify({"error": "LLM error: " + result1["error"]})

        # Debug: Log model's response
        print(f"[DEBUG] Model response keys: {result1.keys() if isinstance(result1, dict) else 'not dict'}")
        print(f"[DEBUG] Raw content: {str(result1.get('content', ''))[:500]}...")
        has_content = bool(result1.get("content"))
        tool_calls_list = result1.get("tool_calls", [])
        print(f"[DEBUG] Has content: {has_content}, tool_calls: {len(tool_calls_list)}")

        tool_calls = result1.get("tool_calls", [])
        all_tool_results = []
        processed_tool_calls = []

        if tool_calls:
            print(f"  [->] 大模型请求调用 {len(tool_calls)} 个工具: {[tc.get('function', {}).get('name') for tc in tool_calls]}")
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    func_name = func.get("name", "")
                    func_args = func.get("arguments", "{}")
                    if isinstance(func_args, str):
                        args_dict = json.loads(func_args)
                    else:
                        args_dict = func_args

                    print(f"  [->] 执行工具: {func_name} | 参数: {args_dict}")
                    exec_result = execute_tool(func_name, args_dict)
                    processed_tool_calls.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": func_name, "arguments": func_args},
                        "result": exec_result
                    })
                    all_tool_results.append((func_name, exec_result))

        # Round 2: Return tool results
        final_result = result1.get("content", "")
        if processed_tool_calls:
            print("\n>>> 阶段2: 返回工具结果，大模型给出最终决策...")

            # Build tool result messages properly
            tool_result_messages = []
            for tc_idx, tc_info in enumerate(processed_tool_calls):
                tc_id = tc_info.get("id", f"call_{tc_idx}")
                exec_res = tc_info.get("result", [])
                # Serialize result to string
                res_str = json.dumps(exec_res)
                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": res_str
                })

            # Format followup prompt
            tool_sum = []
            for func_name, exec_res in all_tool_results:
                res_str = json.dumps(exec_res)
                tool_sum.append(f"[{func_name}] {res_str}")
            tool_sum_text = "\n\n".join(tool_sum)

            followup = f"""PatientID: {patient_id}

Patient clinical info:
{patient_info}

Tool Results (第一轮收集的证据):
{tool_sum_text}

根据患者临床信息和上述工具搜索结果，给出28天死亡风险的最终预测概率 (0-100%)。你需要关注患者的具体临床细节来支撑预测，而不是简单给出"中立"的50%概率。

重要提醒：以患者临床信息为准做出你的判断，必要时可参考小模型进行对比验证，但不应完全依赖小模型的具体数值。

OUTPUT: JSON格式:
{{"analysis": "基于患者临床信息和工具搜索结果的综合分析，包括风险因素、文献证据支持、预后判断", "used_tools": ["tool1","tool2"], "use_small_model": true/false, "final_probability": XX, "references": [{{"source": "PubMed PMID or Guideline URL", "detail": "Title"}}]}}"""

            result2 = call_qwen(followup, system_prompt=system_prompt_val, tool_calls=processed_tool_calls)
            final_result = result2.get("content", "")

        # 判断大模型是否调用了小模型工具
        small_model_called = any(
            func_name == "get_small_model_prediction"
            for func_name, _ in all_tool_results
        )

        # Parse results
        try:
            json_match = re.search(r'\{[\s\S]*\}', final_result)
            if json_match:
                llm_result = json.loads(json_match.group())
            else:
                prob_match = re.search(r'(\d+(?:\.\d+)?)\s*%', final_result)
                final_prob = float(prob_match.group(1))
                llm_result = {"analysis": final_result, "used_tools": [], "use_small_model": small_model_called,
                             "final_probability": final_prob, "references": []}
        except Exception as e:
            print(f"[DEBUG] Parse error: {e}")
            prob_match = re.search(r'(\d+(?:\.\d+)?)\s*%', final_result)
            final_prob = float(prob_match.group(1)) if prob_match else 50.0
            llm_result = {"analysis": final_result[:300], "used_tools": [], "use_small_model": small_model_called,
                         "final_probability": final_prob, "references": []}

        final_probability = llm_result.get("final_probability")
        if final_probability is None:
            raise Exception("No probability in response")

        final_probability = float(final_probability)
        if final_probability < 0 or final_probability > 100:
            raise Exception("Invalid probability: " + str(final_probability))

        # Extract references from REAL tool results (not from LLM)
        real_references = []
        drug_results = []  # 新增：药品详细信息
        print(f"[DEBUG] Processing {len(all_tool_results)} tool results for references")
        for func_name, exec_res in all_tool_results:
            print(f"[DEBUG] Tool: {func_name}, result type: {type(exec_res)}, has refs: {'references' in exec_res if isinstance(exec_res, dict) else False}")
            if isinstance(exec_res, dict):
                # 提取药品信息
                if func_name == "drug_label_search":
                    drug_results.append(exec_res)
                # 提取参考文献
                refs = exec_res.get("references", [])
                if refs:
                    print(f"[DEBUG] Found {len(refs)} refs from {func_name}")
                    real_references.extend(refs)
            elif isinstance(exec_res, list):
                for item in exec_res:
                    if isinstance(item, dict):
                        # 提取药品信息
                        if func_name == "drug_label_search":
                            drug_results.append(item)
                        refs = item.get("references", [])
                        if refs:
                            real_references.extend(refs)

        # Deduplicate references
        seen = set()
        unique_refs = []
        for ref in real_references:
            key = ref.get("source", "")
            if key and key not in seen:
                seen.add(key)
                unique_refs.append(ref)

        print(f"[DEBUG] Final unique refs: {len(unique_refs)}, content: {unique_refs}")

        return jsonify({"probability": round(final_probability, 1),
                    "analysis": llm_result.get("analysis", final_result[:300]),
                    "used_tools": llm_result.get("used_tools", [n for n, _ in all_tool_results]),
                    "small_model_used": small_model_called,
                    "references": unique_refs,
                    "drug_results": drug_results})

        print("\n" + "="*50)
        print(f"[OK] 预测完成 | 28天死亡概率: {final_probability}%")
        print(f"  使用工具: {llm_result.get('used_tools', [])}")
        print(f"  参考文献: {len(unique_refs)} 篇")
        print("="*50)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e) + "\n\nDetails:\n" + traceback.format_exc()})


if __name__ == "__main__":
    print("\n" + "="*60)
    print("ICU Septic Shock 28-Day Predictor (Function Calling)")
    print("="*60)
    print(f"大模型: {MODEL_NAME}")
    base_model = MODEL_NAME.split("/")[-1].replace("-Instruct", "").replace("Qwen", "Qwen")
    print(f"底座: {base_model}")
    print("="*60)
    print("Access: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)