from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("IBKR_DB", ROOT / "data" / "ibkr.sqlite3"))
WEB_ROOT = ROOT / "web"
PORT = int(os.environ.get("PORT", "8000"))
FLEX_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FLEX_CONFIG_PATH = DB_PATH.parent / "flex_config.json"
FLEX_REPORTS_PATH = DB_PATH.parent / "flex_reports"
FLEX_PARSE_CACHE = {}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS imports (
          id INTEGER PRIMARY KEY, account TEXT NOT NULL, filename TEXT NOT NULL,
          sha256 TEXT NOT NULL UNIQUE, period_start TEXT, period_end TEXT,
          generated_at TEXT, imported_at TEXT NOT NULL, base_currency TEXT,
          row_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS records (
          id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
          section TEXT NOT NULL, discriminator TEXT NOT NULL DEFAULT '',
          payload TEXT NOT NULL, row_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS records_section ON records(section, import_id);
        CREATE INDEX IF NOT EXISTS imports_account_period ON imports(account, period_end);
        """)


def parse_period(value):
    if not value:
        return "", ""
    # Chinese IBKR report dates are usually "三月 18, 2025 - 三月 18, 2026".
    months = {"一月": 1, "二月": 2, "三月": 3, "四月": 4, "五月": 5, "六月": 6,
              "七月": 7, "八月": 8, "九月": 9, "十月": 10, "十一月": 11, "十二月": 12}
    found = re.findall(r"(一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月)\s+(\d{1,2}),\s*(\d{4})", value)
    if len(found) == 2:
        dates = [f"{y}-{months[m]:02d}-{int(d):02d}" for m, d, y in found]
        return dates[0], dates[1]
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", value)
    return (dates[0], dates[-1]) if dates else ("", "")


def parse_csv(content):
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("无法识别文件编码，请使用 IBKR 导出的 CSV 文件。")
    rows = list(csv.reader(io.StringIO(text)))
    metadata = {}
    data_records = []
    headers = {}
    for line in rows:
        if not line:
            continue
        section = line[0].strip()
        kind = line[1].strip() if len(line) > 1 else ""
        values = [v.strip() for v in line[2:]]
        if kind == "Header":
            headers[section] = values
        elif kind == "Data":
            header = headers.get(section, [])
            payload = {header[i]: values[i] if i < len(values) else "" for i in range(len(header)) if header[i]}
            # Retain positional values for multiple header variants and unfamiliar future columns.
            payload["_values"] = values
            if section == "Statement":
                if len(values) >= 2:
                    metadata[values[0]] = values[1]
            elif section == "账户信息" and len(values) >= 2:
                metadata[values[0]] = values[1]
            if any(v for v in values):
                data_records.append((section, values[0] if values else "", payload))
    account = metadata.get("账户", "").strip()
    if not account:
        raise ValueError("文件中未找到账户信息，请确认这是 IBKR 活动账单 CSV。")
    period_start, period_end = parse_period(metadata.get("Period", ""))
    generated = metadata.get("WhenGenerated", "")
    return {"account": account, "period_start": period_start, "period_end": period_end,
            "generated_at": generated, "base_currency": metadata.get("基础货币", "USD"),
            "records": data_records}


def parse_flex_csv(content):
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("无法识别 Flex CSV 编码。")
    rows = list(csv.reader(io.StringIO(text)))
    blocks = []
    header, block_rows = None, []
    header_starts = {"ClientAccountID", "Symbol", "FXCurrency", "ValueDate", "AssetClass"}
    for raw in rows:
        row = [cell.strip() for cell in raw]
        if not any(row):
            continue
        is_header = row[0] in header_starts and any(
            field in row for field in ("CurrencyPrimary", "ReportDate", "Date/Time", "ActivityDescription",
                                       "Quantity", "Total", "Name", "FromDate", "Date", "ValueDate"))
        if is_header:
            if header:
                blocks.append((header, block_rows))
            header, block_rows = row, []
        elif header:
            block_rows.append(row)
    if header:
        blocks.append((header, block_rows))
    if not blocks:
        raise ValueError("没有识别到 Flex Query CSV 区块标题。")

    def flex_date(value):
        value = str(value or "").strip()
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
        return value

    account = ""
    base_currency = "USD"
    start_date = end_date = ""
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    normalized = []
    event_groups = {"存款和取款": [], "股息": [], "代扣税": [], "利息": []}
    base_flow = base_dividends = base_interest = None
    nav_rows = []
    for block_header, block_rows in blocks:
        block_data = [dict(zip(block_header, row)) for row in block_rows if row and row[0] not in header_starts]
        if not block_data:
            continue
        fields = set(block_header)
        first = block_data[0]
        account = account or first.get("ClientAccountID", "")
        if "Name" in fields and "DateOpened" in fields:
            account = first.get("ClientAccountID", account)
            base_currency = first.get("CurrencyPrimary", base_currency)
            for row in block_data:
                normalized.append(("账户信息", row.get("ClientAccountID", ""),
                                   {"账户": row.get("ClientAccountID", ""), "基础货币": row.get("CurrencyPrimary", base_currency),
                                    "姓名": row.get("Name", "")}))
        elif {"ReportDate", "Cash", "Stock", "Total", "CurrencyPrimary"}.issubset(fields):
            category_names = (("Cash", "现金"), ("Stock", "股票"), ("Options", "期权"),
                              ("Bonds", "债券"), ("Funds", "基金"), ("Total", "总数"))
            for row in block_data:
                report_date = row.get("ReportDate", "")
                for source, category in category_names:
                    value = row.get(source, "")
                    if not value:
                        continue
                    item = {"资产类型": category, "之前合计": "", "当前多头": row.get(source + "Long", ""),
                            "当前空头": row.get(source + "Short", ""), "当前合计": value, "变更": value,
                            "报告日期": report_date, "货币": row.get("CurrencyPrimary", base_currency)}
                    item["_values"] = [category, "", item["当前多头"], item["当前空头"], value, value]
                    nav_rows.append(item)
                    normalized.append(("净资产值", report_date, item))
        elif {"FromDate", "ToDate", "StartingValue", "EndingValue", "DepositsWithdrawals"}.issubset(fields):
            row = first
            start_date = row.get("FromDate", start_date)
            end_date = row.get("ToDate", end_date)
            base_flow = n(row.get("DepositsWithdrawals", ""))
            base_dividends = n(row.get("Dividends", "")) if row.get("Dividends", "") else None
            base_interest = n(row.get("Interest", "")) if row.get("Interest", "") else None
            values = (("开始价值", "StartingValue"), ("按市值计价", "Mtm"),
                      ("已实现的损益", "Realized"), ("未实现损益变更", "ChangeInUnrealized"),
                      ("存款和取款", "DepositsWithdrawals"), ("佣金", "Commissions"),
                      ("股息", "Dividends"), ("利息", "Interest"), ("结束价值", "EndingValue"))
            for name, source in values:
                value = row.get(source, "")
                if value != "":
                    payload = {"域名称": name, "域值": value, "_values": [name, value]}
                    normalized.append(("净资产值变更", name, payload))
        elif {"Symbol", "AssetClass", "TotalRealizedPnl", "TotalUnrealizedPnl"}.issubset(fields):
            for row in block_data:
                payload = {"资产分类": row.get("AssetClass", "") or ("总数" if not row.get("Symbol") else ""), "代码": row.get("Symbol", ""),
                           "已实现的 总数": row.get("TotalRealizedPnl", ""),
                           "未实现的 总数": row.get("TotalUnrealizedPnl", ""),
                           "总数": row.get("TotalFifoPnl", "") or str(n(row.get("TotalRealizedPnl")) + n(row.get("TotalUnrealizedPnl")))}
                payload["_values"] = [payload["资产分类"], payload["代码"], "", "", "", "", "", payload["已实现的 总数"], "", "", "", "", payload["未实现的 总数"], payload["总数"]]
                normalized.append(("已实现和未实现的表现总结", payload["代码"], payload))
        elif {"ReportDate", "TransactionMtmPnl", "PriorOpenMtmPnl", "Total"}.issubset(fields):
            for row in block_data:
                payload = {"报告日期": row.get("ReportDate", ""), "资产分类": row.get("AssetClass", ""),
                           "代码": row.get("Symbol", ""), "按市值计算的损益": row.get("Total", ""),
                           "总数": row.get("Total", "")}
                payload["_values"] = [payload["资产分类"], payload["代码"], payload["按市值计算的损益"]]
                normalized.append(("按市值计算的表现总结", payload["报告日期"], payload))
        elif {"PositionValue", "CostBasisMoney", "FifoPnlUnrealized", "CurrencyPrimary"}.issubset(fields):
            for row in block_data:
                payload = {"DataDiscriminator": "Summary", "资产分类": row.get("AssetClass", ""),
                           "货币": row.get("CurrencyPrimary", ""), "代码": row.get("Symbol", ""),
                           "数量": row.get("Quantity", ""), "合约乘数": row.get("Multiplier", "1"),
                           "成本价格": row.get("CostBasisPrice", ""), "成本基础": row.get("CostBasisMoney", ""),
                           "收盘价格": row.get("MarkPrice", ""), "价值": row.get("PositionValue", ""),
                           "未实现的损益": row.get("FifoPnlUnrealized", ""),
                           "PercentOfNAV": row.get("PercentOfNAV", "")}
                payload["_values"] = ["Summary", payload["资产分类"], payload["货币"], payload["代码"], payload["数量"],
                                      payload["合约乘数"], payload["成本价格"], payload["成本基础"], payload["收盘价格"],
                                      payload["价值"], payload["未实现的损益"]]
                normalized.append(("未平仓持仓", payload["代码"], payload))
        elif {"Date/Time", "Amount", "Type", "Description", "SettleDate", "CurrencyPrimary"}.issubset(fields):
            for row in block_data:
                currency = row.get("CurrencyPrimary", "")
                date_value = flex_date(row.get("SettleDate", ""))
                description = row.get("Description", "")
                amount = row.get("Amount", "")
                activity_type = row.get("Type", "").strip().lower()
                if "dividend" in activity_type:
                    section = "股息"
                elif "interest" in activity_type:
                    section = "利息"
                elif "tax" in activity_type:
                    section = "代扣税"
                elif "deposit" in activity_type or "withdraw" in activity_type:
                    section = "存款和取款"
                else:
                    section = "Flex:CashTransactions"
                if section.startswith("Flex:"):
                    payload = dict(row)
                    payload["_values"] = [row.get(key, "") for key in block_header]
                    normalized.append((section, row.get("Type", ""), payload))
                    continue
                payload = {"货币": currency, "日期": date_value, "结算日期": date_value,
                           "描述": description, "金额": amount, "交易时间": row.get("Date/Time", "")}
                payload["_source"] = "CashTransactions"
                payload["_values"] = [currency, date_value, description, amount]
                event_groups[section].append(payload)
        elif {"Received", "Paid", "Total", "CurrencyPrimary", "ActivityDescription"}.issubset(fields):
            for row in block_data:
                currency = row.get("CurrencyPrimary", "")
                payload = {"货币": currency, "日期": flex_date(row.get("Date", "")), "描述": row.get("ActivityDescription", ""), "金额": row.get("Total", "")}
                payload["_values"] = [currency, payload["日期"], payload["描述"], payload["金额"]]
                event_groups["股息"].append(payload)
        elif {"TaxDescription", "TaxAmount", "CurrencyPrimary"}.issubset(fields):
            for row in block_data:
                currency = row.get("CurrencyPrimary", "")
                payload = {"货币": currency, "日期": flex_date(row.get("Date", "")), "描述": row.get("TaxDescription", ""), "金额": row.get("TaxAmount", "")}
                payload["_values"] = [currency, payload["日期"], payload["描述"], payload["金额"]]
                event_groups["代扣税"].append(payload)
        elif {"Date", "Description", "Amount", "CurrencyPrimary"}.issubset(fields):
            for row in block_data:
                currency = row.get("CurrencyPrimary", "")
                payload = {"货币": currency, "日期": flex_date(row.get("Date", "")), "描述": row.get("Description", ""), "金额": row.get("Amount", "")}
                payload["_values"] = [currency, payload["日期"], payload["描述"], payload["金额"]]
                event_groups["利息"].append(payload)
        elif {"TradeDate", "DateTime", "Buy/Sell", "Quantity", "TradePrice", "CurrencyPrimary"}.issubset(fields):
            for row in block_data:
                payload = {"DataDiscriminator": "Trade", "资产分类": row.get("AssetClass", ""),
                           "货币": row.get("CurrencyPrimary", ""), "代码": row.get("Symbol", ""),
                           "日期/时间": row.get("DateTime", row.get("TradeDate", "")), "数量": row.get("Quantity", ""),
                           "交易价格": row.get("TradePrice", ""), "收益": row.get("Proceeds", ""),
                           "已实现的损益": row.get("FifoPnlRealized", ""), "按市值计算的损益": "",
                           "买卖": row.get("Buy/Sell", "")}
                payload["_values"] = ["Trade", payload["资产分类"], payload["货币"], payload["代码"], payload["日期/时间"],
                                      payload["数量"], payload["交易价格"], "", payload["收益"], "", "", payload["已实现的损益"]]
                normalized.append(("交易", payload["代码"], payload))
        else:
            section = "Flex:" + ",".join(block_header[:6])
            for row in block_data:
                payload = dict(row)
                payload["_values"] = [row.get(key, "") for key in block_header]
                normalized.append((section, next(iter(row.values()), ""), payload))

    if not account:
        raise ValueError("Flex CSV 中未找到 ClientAccountID。")
    date_format = "%Y%m%d"
    try:
        period_start = datetime.strptime(start_date, date_format).strftime("%Y-%m-%d") if start_date else ""
        period_end = datetime.strptime(end_date, date_format).strftime("%Y-%m-%d") if end_date else ""
    except ValueError:
        period_start = period_end = ""
    if not period_start or not period_end:
        report_dates = [r.get("报告日期", "") for r in nav_rows if r.get("报告日期")]
        if report_dates:
            report_dates.sort()
            period_start = period_start or datetime.strptime(report_dates[0], date_format).strftime("%Y-%m-%d")
            period_end = period_end or datetime.strptime(report_dates[-1], date_format).strftime("%Y-%m-%d")

    fx_activity = [payload for name, _, payload in normalized
                   if name.startswith("Flex:") and {"DateTime", "FXCurrency", "ActivityDescription",
                                                      "Quantity", "Proceeds", "FunctionalCurrency"}.issubset(payload)]
    derive_flex_cashflow_base_amounts(event_groups["存款和取款"], fx_activity, base_currency)
    cash_activity = [payload for name, _, payload in normalized
                     if name.startswith("Flex:") and {"Date", "ActivityDescription", "Amount", "CurrencyPrimary"}.issubset(payload)]
    derive_flex_interest_base_amounts(event_groups["利息"], cash_activity, base_currency, base_interest)
    for section, events in event_groups.items():
        by_currency = {}
        for event in events:
            by_currency.setdefault(event["货币"], []).append(event)
        foreign = [currency for currency in by_currency if currency and currency != base_currency]
        native_base = sum(n(event["金额"]) for event in by_currency.get(base_currency, []))
        for currency, currency_events in by_currency.items():
            normalized.extend((section, event["货币"], event) for event in currency_events)
            subtotal = sum(n(event["金额"]) for event in currency_events)
            normalized.append((section, "总数", {"货币": "总数", "结算日期": "", "日期": "", "描述": "", "金额": str(subtotal),
                                                   "_values": ["总数", "", "", str(subtotal)]}))
            converted = None
            event_base_total = sum(n(event["基础货币金额"]) for event in currency_events
                                   if event.get("基础货币金额") not in (None, ""))
            event_base_complete = all(event.get("基础货币金额") not in (None, "") for event in currency_events)
            if event_base_complete:
                converted = event_base_total
            elif currency == base_currency:
                converted = subtotal
            elif len(foreign) == 1:
                base_total = {"存款和取款": base_flow, "股息": base_dividends, "利息": base_interest}.get(section)
                if base_total is not None:
                    converted = base_total - native_base
            if converted is not None:
                normalized.append((section, f"总数 {base_currency}", {"货币": f"总数 {base_currency}", "结算日期": "", "日期": "", "描述": "", "金额": str(converted),
                                                       "_values": [f"总数 {base_currency}", "", "", str(converted)]}))
    if not period_start:
        period_start = period_end
    if not period_end:
        period_end = period_start
    return {"account": account, "period_start": period_start, "period_end": period_end,
            "generated_at": generated, "base_currency": base_currency, "records": normalized}


def import_file(filename, content):
    digest = hashlib.sha256(content).hexdigest()
    try:
        parsed = parse_csv(content)
    except ValueError as statement_error:
        try:
            parsed = parse_flex_csv(content)
        except ValueError as flex_error:
            raise flex_error from statement_error
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as db:
        prior = db.execute("SELECT id, filename, period_start, period_end FROM imports WHERE sha256=?", (digest,)).fetchone()
        if prior:
            return {"id": prior["id"], "duplicate": True, "filename": prior["filename"],
                    "period_start": prior["period_start"], "period_end": prior["period_end"], "rows": 0}
        cur = db.execute("INSERT INTO imports(account,filename,sha256,period_start,period_end,generated_at,imported_at,base_currency,row_count) VALUES(?,?,?,?,?,?,?,?,?)",
                         (parsed["account"], filename, digest, parsed["period_start"], parsed["period_end"],
                          parsed["generated_at"], now, parsed["base_currency"], len(parsed["records"])))
        import_id = cur.lastrowid
        db.executemany("INSERT INTO records(import_id,section,discriminator,payload,row_hash) VALUES(?,?,?,?,?)",
                       [(import_id, section, discriminator, json.dumps(payload, ensure_ascii=False),
                         hashlib.sha256(json.dumps([section, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest())
                        for section, discriminator, payload in parsed["records"]])
    return {"id": import_id, "duplicate": False, "filename": filename, "period_start": parsed["period_start"],
            "period_end": parsed["period_end"], "rows": len(parsed["records"])}


def read_flex_config():
    try:
        config = json.loads(FLEX_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    return {"token": str(config.get("token", "")), "query_id": str(config.get("query_id", ""))}


def save_flex_config(token, query_id):
    current = read_flex_config()
    token = token or current["token"]
    query_id = query_id or current["query_id"]
    if not token or not query_id:
        raise ValueError("请填写 Flex 访问令牌和 Query ID。")
    if not query_id.isdigit():
        raise ValueError("Query ID 必须是数字。")
    FLEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLEX_CONFIG_PATH.write_text(json.dumps({"token": token, "query_id": query_id}), encoding="utf-8")
    return {"configured": True, "query_id": query_id}


def flex_get(params):
    request = Request(f"{FLEX_BASE_URL}/{params.pop('_endpoint')}?{urlencode(params)}",
                      headers={"User-Agent": "IBKR Portfolio Viewer/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read(50 * 1024 * 1024 + 1)
    except Exception as exc:
        raise ValueError("连接 IBKR Flex Web Service 失败，请检查网络和 Flex 配置。") from exc
    if len(content) > 50 * 1024 * 1024:
        raise ValueError("IBKR 返回的报告超过 50 MB。")
    return content


def flex_xml_value(root, name):
    return next(((node.text or "").strip() for node in root.iter()
                 if node.tag.rsplit("}", 1)[-1] == name), "")


def flex_error(content):
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return ""
    status = flex_xml_value(root, "Status").lower()
    if status == "success":
        return ""
    if not status and root.tag.rsplit("}", 1)[-1] in ("FlexQueryResponse", "FlexStatement"):
        return ""
    return flex_xml_value(root, "ErrorMessage") or flex_xml_value(root, "ErrorCode") or "IBKR 未能生成报告。"


def fetch_flex_report(date_from, date_to):
    config = read_flex_config()
    if not config["token"] or not config["query_id"]:
        raise ValueError("请先在 Flex API 页面保存访问令牌和 Query ID。")
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").strftime("%Y%m%d")
        end = datetime.strptime(date_to, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError("日期格式必须为 YYYY-MM-DD。") from exc
    if start > end:
        raise ValueError("开始日期不能晚于结束日期。")

    token, query_id = config["token"], config["query_id"]
    response = flex_get({"_endpoint": "SendRequest", "t": token, "q": query_id, "v": "3", "fd": start, "td": end})
    try:
        root = ElementTree.fromstring(response)
    except ElementTree.ParseError as exc:
        raise ValueError("IBKR SendRequest 返回了无法识别的响应。") from exc
    if flex_xml_value(root, "Status").lower() != "success":
        raise ValueError(f"IBKR Flex 查询失败：{flex_xml_value(root, 'ErrorMessage') or flex_xml_value(root, 'ErrorCode') or '未知错误'}")
    reference = flex_xml_value(root, "ReferenceCode")
    if not reference:
        raise ValueError("IBKR 响应中缺少报告 ReferenceCode。")

    report = b""
    for attempt in range(30):
        report = flex_get({"_endpoint": "GetStatement", "t": token, "q": reference, "v": "3"})
        if report.lstrip().startswith(b"<"):
            error = flex_error(report)
            if error and ("progress" in error.lower() or "generat" in error.lower()) and attempt < 29:
                time.sleep(2)
                continue
            if error:
                raise ValueError(f"IBKR 获取 Flex 报告失败：{error}")
        break
    extension = "xml" if report.lstrip().startswith(b"<") else "csv"
    filename = f"flex_{query_id}_{start}_{end}.{extension}"
    FLEX_REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    (FLEX_REPORTS_PATH / filename).write_bytes(report)
    if extension != "csv":
        raise ValueError(f"报告已保存到 data/flex_reports/{filename}，但自动导入只支持 CSV。请将 Flex Query 输出格式设为 CSV。")
    try:
        result = import_file(filename, report)
    except ValueError as exc:
        raise ValueError(f"报告已保存到 data/flex_reports/{filename}，但格式不是可识别的 IBKR 活动账单或 Flex Query CSV：{exc}") from exc
    return {"report_file": filename, "import": result}


def all_imports(db, account=None):
    sql = "SELECT * FROM imports"
    args = []
    if account:
        sql += " WHERE account=?"
        args.append(account)
    return [dict(row) for row in db.execute(sql + " ORDER BY COALESCE(period_end,''), generated_at, id", args)]


def unpack(db, import_id, section):
    return [json.loads(r[0]) for r in db.execute("SELECT payload FROM records WHERE import_id=? AND section=?", (import_id, section))]


def n(value):
    if value is None:
        return 0.0
    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        return float(cleaned) if cleaned and cleaned not in ("--", "-", "N/A") else 0.0
    except (TypeError, ValueError):
        return 0.0


def derive_flex_cashflow_base_amounts(events, activity_rows, base_currency):
    """Use Flex's transaction-level base-currency cash activity to value deposits."""
    unmatched = set()
    event_dates = {}
    for index, event in enumerate(events):
        amount = n(event.get("金额"))
        currency = event.get("货币", "")
        if currency == base_currency:
            event["基础货币金额"] = str(amount)
            continue
        if not amount:
            continue
        unmatched.add(index)
        raw_date = str(event.get("交易时间", ""))[:8]
        try:
            event_dates[index] = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            pass

    def close(a, b):
        return abs(a - b) <= max(0.01, abs(b) * 1e-7)

    candidates = []
    for row in activity_rows:
        if str(row.get("ActivityDescription", "")).strip().casefold() != "net cash activity":
            continue
        if row.get("FunctionalCurrency") != base_currency:
            continue
        quantity = n(row.get("Quantity"))
        if not quantity:
            continue
        raw_date = str(row.get("DateTime", ""))[:8]
        try:
            activity_date = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            continue
        candidates.append((activity_date, row.get("FXCurrency", ""), quantity, n(row.get("Proceeds"))))
    candidates.sort(key=lambda entry: entry[0])

    for activity_date, currency, quantity, proceeds in candidates:
        sign = 1 if quantity > 0 else -1
        eligible = []
        for index in unmatched:
            event = events[index]
            amount = n(event.get("金额"))
            if event.get("货币") != currency or (1 if amount > 0 else -1) != sign or index not in event_dates:
                continue
            date_delta = abs((event_dates[index] - activity_date).days)
            if date_delta <= 1:
                eligible.append((date_delta, index, abs(amount)))
        eligible.sort()
        target = abs(quantity)
        chosen = None
        tolerance = max(0.01, target * 1e-7)
        for delta in sorted({entry[0] for entry in eligible}):
            entries = [entry for entry in eligible if entry[0] == delta][:24]
            chosen = next(([index] for _, index, amount in entries if close(amount, target)), None)
            if chosen is None:
                def subset(position, total, indexes):
                    if abs(total - target) <= tolerance:
                        return list(indexes)
                    if total > target + tolerance:
                        return None
                    for offset in range(position, len(entries)):
                        result = subset(offset + 1, total + entries[offset][2], indexes + (entries[offset][1],))
                        if result is not None:
                            return result
                    return None

                chosen = subset(0, 0.0, ())
            if chosen:
                break
        if not chosen:
            continue
        chosen_total = sum(abs(n(events[index].get("金额"))) for index in chosen)
        if not chosen_total:
            continue
        for index in chosen:
            amount = n(events[index].get("金额"))
            events[index]["基础货币金额"] = str((1 if amount > 0 else -1) * abs(proceeds) * abs(amount) / chosen_total)
            unmatched.discard(index)


def derive_flex_interest_base_amounts(events, activity_rows, base_currency, report_total):
    """Value posted interest transactions in base currency without adding daily accrual rows."""
    def date_key(value):
        value = str(value or "").strip()
        digits = re.sub(r"\D", "", value)
        return digits[:8] if len(digits) >= 8 else value[:10]

    def signature(description):
        text = re.sub(r"\s+", " ", str(description or "").upper()).strip()
        match = re.search(r"\b([A-Z]{3})\s+(CREDIT|DEBIT)\s+INT(?:EREST)?\s+FOR\s+([A-Z]{3,9}-\d{4})\b", text)
        return match.groups() if match else None

    translated = {}
    for row in activity_rows:
        if row.get("CurrencyPrimary") != base_currency:
            continue
        key_signature = signature(row.get("ActivityDescription"))
        key_date = date_key(row.get("Date"))
        if key_signature and key_date:
            translated.setdefault((key_date, key_signature), []).append(n(row.get("Amount")))

    unmatched = []
    for event in events:
        amount = n(event.get("金额"))
        currency = event.get("货币", "")
        if currency == base_currency:
            event["基础货币金额"] = str(amount)
            continue
        event_signature = signature(event.get("描述"))
        key = (date_key(event.get("交易时间") or event.get("日期")), event_signature)
        candidates = translated.get(key) if event_signature else None
        if candidates:
            event["基础货币金额"] = str(candidates.pop(0))
        else:
            unmatched.append(event)

    if report_total is None or not events:
        return
    known_total = sum(n(event.get("基础货币金额")) for event in events
                      if event.get("基础货币金额") not in (None, ""))
    residual = n(report_total) - known_total
    if unmatched:
        denominator = sum(n(event.get("金额")) for event in unmatched)
        absolute_denominator = sum(abs(n(event.get("金额"))) for event in unmatched)
        for event in unmatched:
            amount = n(event.get("金额"))
            weight = amount / denominator if abs(denominator) > 1e-12 else abs(amount) / (absolute_denominator or 1)
            event["基础货币金额"] = str(residual * weight)
    elif abs(residual) > 1e-9:
        # Keep the sum aligned with IBKR's report total if the activity detail is incomplete.
        last = events[-1]
        last["基础货币金额"] = str(n(last.get("基础货币金额")) + residual)


def v(row, label, index=None):
    if label in row and row[label] not in (None, ""):
        return row[label]
    vals = row.get("_values", [])
    return vals[index] if index is not None and len(vals) > index else ""


def is_summary_currency(currency):
    return str(currency or "").startswith(("总数", "Total"))


def latest_snapshot(records):
    return records


def merge_positions(records):
    groups = {}
    for row in records:
        key = (v(row, "资产分类", 1), v(row, "货币", 2), v(row, "代码", 3), v(row, "合约乘数", 5))
        groups.setdefault(key, []).append(row)
    merged = []
    for (asset, currency, symbol, multiplier), group in groups.items():
        # Flex Open Positions may contain both a symbol-level total (PercentOfNAV set)
        # and the underlying tax lots (PercentOfNAV blank). Prefer the total row.
        summary_rows = [row for row in group if row.get("PercentOfNAV") not in (None, "")]
        candidates = summary_rows or group
        unique = {}
        for row in candidates:
            signature = tuple(v(row, label, index) for label, index in (
                ("数量", 4), ("成本基础", 7), ("价值", 9), ("未实现的损益", 10), ("收盘价格", 8)))
            unique.setdefault(signature, row)
        candidates = list(unique.values())
        quantity = sum(n(v(row, "数量", 4)) for row in candidates)
        cost_basis = sum(n(v(row, "成本基础", 7)) for row in candidates)
        market_value = sum(n(v(row, "价值", 9)) for row in candidates)
        unrealized = sum(n(v(row, "未实现的损益", 10)) for row in candidates)
        weight = sum(abs(n(v(row, "数量", 4))) for row in candidates)
        cost_price = (sum(n(v(row, "成本价格", 6)) * abs(n(v(row, "数量", 4))) for row in candidates) / weight
                      if weight else n(v(candidates[0], "成本价格", 6)))
        base = dict(candidates[0])
        base.update({"资产分类": asset, "货币": currency, "代码": symbol, "数量": str(quantity),
                     "合约乘数": multiplier, "成本价格": str(cost_price), "成本基础": str(cost_basis),
                     "收盘价格": v(candidates[0], "收盘价格", 8), "价值": str(market_value),
                     "未实现的损益": str(unrealized)})
        base["_values"] = ["Summary", asset, currency, symbol, str(quantity), multiplier, str(cost_price),
                           str(cost_basis), base["收盘价格"], str(market_value), str(unrealized)]
        merged.append(base)
    return merged


def stored_flex_parse(import_info):
    filename = str(import_info.get("filename", ""))
    if not filename.startswith("flex_"):
        return None
    path = FLEX_REPORTS_PATH / Path(filename).name
    try:
        stat = path.stat()
        cache_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        if cache_key in FLEX_PARSE_CACHE:
            return FLEX_PARSE_CACHE[cache_key]
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != import_info.get("sha256"):
            return None
        parsed = parse_flex_csv(content)
        FLEX_PARSE_CACHE.clear()
        FLEX_PARSE_CACHE[cache_key] = parsed
        return parsed
    except (OSError, ValueError):
        return None


def imported_section(db, import_info, section):
    parsed = stored_flex_parse(import_info)
    if parsed is not None:
        return [payload for current, _, payload in parsed["records"] if current == section]
    return unpack(db, import_info["id"], section)


def cashflow_fx_rates(records, base_currency):
    """Read IBKR's per-currency translated subtotals for a cashflow section."""
    current_currency = None
    original_totals = {}
    base_totals = {}
    for row in records:
        currency = v(row, "货币", 0)
        amount = n(v(row, "金额", 3))
        if currency == "总数":
            if current_currency:
                original_totals[current_currency] = amount
        elif currency == f"总数 {base_currency}":
            if current_currency:
                base_totals[current_currency] = amount
        elif currency and v(row, "结算日期", 1):
            current_currency = currency
    return {currency: base_totals[currency] / total
            for currency, total in original_totals.items()
            if total and currency in base_totals}


def summary(db, account=None, date_from=None, date_to=None):
    imports = all_imports(db, account)
    if not imports:
        return {"imports": [], "accounts": [], "empty": True}
    accounts = sorted({i["account"] for i in imports})
    account = account if account in accounts else accounts[0]
    selected = [i for i in imports if i["account"] == account]
    first_date = min((i["period_start"] for i in selected if i["period_start"]), default="")
    last_date = max((i["period_end"] for i in selected if i["period_end"]), default="")
    date_from = date_from or first_date
    date_to = date_to or last_date
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    snapshots = [i for i in selected if i["period_end"] and (not date_to or i["period_end"] <= date_to)]
    latest = snapshots[-1] if snapshots else None
    iid = latest["id"] if latest else None
    base_currency = selected[-1].get("base_currency") or "USD"
    nlv = unpack(db, iid, "净资产值")
    total_nav_rows = [r for r in nlv if v(r, "资产类型", 0) == "总数"]
    dated_nav_rows = [r for r in total_nav_rows if v(r, "报告日期")]
    def latest_nav(category):
        rows = [r for r in nlv if v(r, "资产类型", 0) == category]
        dated = [r for r in rows if v(r, "报告日期")]
        return max(dated, key=lambda r: v(r, "报告日期")) if dated else next(iter(rows), None)
    nav_total = latest_nav("总数")
    nav_cash = latest_nav("现金")
    nav_equity = latest_nav("股票")
    position_records = [r for r in imported_section(db, latest, "未平仓持仓")
                        if v(r, "DataDiscriminator", 0) == "Summary"] if latest else []
    positions = merge_positions(position_records)
    cashflows = []
    dividends, tax, interest = [], [], []
    for imp in selected:
        statement_cashflows = imported_section(db, imp, "存款和取款")
        fx_rates = cashflow_fx_rates(statement_cashflows, imp.get("base_currency") or "USD")
        for r in statement_cashflows:
            amount = v(r, "金额", 3)
            currency = v(r, "货币", 0)
            if amount and not is_summary_currency(currency):
                converted_amount = (n(r["基础货币金额"]) if r.get("基础货币金额") not in (None, "") else
                                    n(amount) * fx_rates.get(currency, 1 if currency == (imp.get("base_currency") or "USD") else 0))
                cashflows.append({"date": v(r, "结算日期", 1), "currency": currency,
                                  "description": v(r, "描述", 2), "amount": n(amount),
                                  "_base_amount": converted_amount,
                                  "period": imp["period_end"]})
        for section, target in (("股息", dividends), ("代扣税", tax), ("利息", interest)):
            records = imported_section(db, imp, section)
            if section == "利息":
                flex_cash_transactions = [r for r in records
                                          if r.get("_source") == "CashTransactions" or r.get("交易时间")]
                if flex_cash_transactions:
                    records = flex_cash_transactions
            section_fx_rates = cashflow_fx_rates(records, imp.get("base_currency") or "USD")
            for r in records:
                amount = v(r, "金额", 3)
                currency = v(r, "货币", 0)
                if amount and not is_summary_currency(currency):
                    target.append({"date": v(r, "日期", 1), "currency": currency,
                                   "description": v(r, "描述", 2), "amount": n(amount),
                                   "_base_amount": (n(r["基础货币金额"]) if r.get("基础货币金额") not in (None, "") else
                                                    n(amount) * section_fx_rates.get(currency, 1 if currency == (imp.get("base_currency") or "USD") else 0)),
                                   "period": imp["period_end"]})
    # Remove exact overlap rows while retaining legitimate identical occurrences from a single statement.
    def dedupe(events, event_kind=""):
        seen = set(); out = []; counts = Counter()
        for e in events:
            description = e["description"]
            if event_kind == "dividend":
                symbol_match = re.match(r"\s*([^\s(]+)", description)
                symbol = symbol_match.group(1) if symbol_match else ""
                text = description.casefold()
                subtype = "bonus" if "bonus dividend" in text or "(红利)" in text else (
                    "ordinary" if "ordinary dividend" in text or "常规股息" in text else "")
                transaction = "payment-in-lieu" if "payment in lieu" in text or "替代股息" in text else "cash-dividend"
                description = f"{symbol}|{transaction}|{subtype}"
            elif event_kind == "cashflow":
                text = description.casefold()
                if "electronic fund transfer" in text or "电子资金转账" in text:
                    description = "electronic-fund-transfer"
            key = (e["date"], e["currency"], description, e["amount"])
            scope = (e["period"], key)
            occurrence = counts[scope]
            counts[scope] += 1
            marker = (key, occurrence)
            if marker not in seen:
                seen.add(marker); out.append(e)
        return out
    cashflows = dedupe(cashflows, "cashflow")
    dividends = dedupe(dividends, "dividend")
    tax, interest = map(dedupe, (tax, interest))
    all_cashflows = cashflows
    def in_range(event):
        return (not date_from or not event["date"] or event["date"] >= date_from) and (not date_to or not event["date"] or event["date"] <= date_to)
    cashflows, dividends, tax, interest = ([e for e in events if in_range(e)]
                                           for events in (cashflows, dividends, tax, interest))
    # Convert each currency using IBKR's translated subtotals; detail rows retain their original currency.
    net_flows = sum(e["_base_amount"] for e in cashflows)
    deposit_total = net_flows
    base_dividends = sum(e["_base_amount"] for e in dividends)
    base_tax = sum(e["_base_amount"] for e in tax)
    base_interest = sum(e["_base_amount"] for e in interest)
    nlv_value = n(v(nav_total, "当前合计", 4)) if nav_total else 0
    cash_value = n(v(nav_cash, "当前合计", 4)) if nav_cash else 0
    equity_value = n(v(nav_equity, "当前合计", 4)) if nav_equity else 0
    nav_points = []
    for imp in selected:
        daily_nav = [r for r in unpack(db, imp["id"], "净资产值")
                     if v(r, "资产类型", 0) == "总数" and v(r, "报告日期")]
        if daily_nav:
            for row in daily_nav:
                point_date = str(v(row, "报告日期"))
                if re.fullmatch(r"\d{8}", point_date):
                    point_date = datetime.strptime(point_date, "%Y%m%d").strftime("%Y-%m-%d")
                point = {"date": point_date, "value": n(v(row, "当前合计", 4))}
                if not nav_points or point["date"] > nav_points[-1]["date"]:
                    nav_points.append(point)
            continue
        changes = unpack(db, imp["id"], "净资产值变更")
        start = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "开始价值"), None)
        end = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "结束价值"), None)
        start_date, end_date = imp["period_start"], imp["period_end"]
        last_point_date = nav_points[-1]["date"] if nav_points else ""
        if start is not None and start_date and (not nav_points or start_date > last_point_date):
            nav_points.append({"date": start_date, "value": start})
        if end is not None and end_date and (not nav_points or end_date > nav_points[-1]["date"]):
            nav_points.append({"date": end_date, "value": end})
    nav_points.sort(key=lambda point: point["date"])
    nav_dates = [point["date"] for point in nav_points]
    flows_by_index = defaultdict(float)
    for event in all_cashflows:
        if not event["date"] or len(nav_points) < 2:
            continue
        index = next((i for i, point in enumerate(nav_points) if point["date"] >= event["date"]), None)
        if index == 0:
            index = 1
        if index is not None and index < len(nav_points):
            flows_by_index[index] += event["_base_amount"]
    daily_points = []
    for index in range(1, len(nav_points)):
        point = nav_points[index]
        daily_points.append({"date": point["date"], "value": point["value"],
                             "external_flows": flows_by_index[index],
                             "pnl": point["value"] - nav_points[index - 1]["value"] - flows_by_index[index]})

    # Reconcile daily changes to IBKR's exact cash-flow-adjusted totals for complete report periods.
    covered_until = ""
    for imp in selected:
        period_start, period_end = imp["period_start"], imp["period_end"]
        if not period_start or not period_end or (covered_until and period_start <= covered_until):
            continue
        changes = unpack(db, imp["id"], "净资产值变更")
        start_value = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "开始价值"), None)
        end_value = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "结束价值"), None)
        report_flows = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "存款和取款"), None)
        period_points = [point for point in daily_points if period_start <= point["date"] <= period_end]
        if start_value is not None and end_value is not None and report_flows is not None and period_points:
            difference = end_value - start_value - report_flows - sum(point["pnl"] for point in period_points)
            period_points[0]["pnl"] += difference
            period_points[0]["reconciliation"] = difference
        covered_until = period_end

    selected_daily = [point.copy() for point in daily_points
                      if (not date_from or point["date"] >= date_from) and (not date_to or point["date"] <= date_to)]
    range_pnl = sum(point["pnl"] for point in selected_daily)
    range_net_asset_change = 0
    if selected_daily:
        first_date = selected_daily[0]["date"]
        first_index = nav_dates.index(first_date)
        range_net_asset_change = selected_daily[-1]["value"] - nav_points[max(0, first_index - 1)]["value"]

    monthly_totals = defaultdict(lambda: {"pnl": 0.0, "external_flows": 0.0, "trading_days": 0})
    for point in selected_daily:
        month = point["date"][:7]
        monthly_totals[month]["pnl"] += point["pnl"]
        monthly_totals[month]["external_flows"] += point["external_flows"]
        monthly_totals[month]["trading_days"] += 1
    monthly = [{"month": month, **values} for month, values in sorted(monthly_totals.items())]

    # IBKR's MTMPerformanceSummary carries per-security totals at report-period granularity.
    stock_totals = defaultdict(float)
    stock_covered_until = ""
    partial_stock_periods = []
    for imp in selected:
        period_start, period_end = imp["period_start"], imp["period_end"]
        if not period_start or not period_end:
            continue
        if (date_from and period_start < date_from) or (date_to and period_end > date_to):
            if (not date_to or period_start <= date_to) and (not date_from or period_end >= date_from):
                partial_stock_periods.append(f"{period_start} – {period_end}")
            continue
        if stock_covered_until and period_start <= stock_covered_until:
            continue
        for row in imported_section(db, imp, "按市值计算的表现总结"):
            if v(row, "资产分类", 0) != "STK" or not v(row, "代码", 1):
                continue
            stock_totals[v(row, "代码", 1)] += n(v(row, "按市值计算的损益", 2))
        stock_covered_until = period_end
    pnl_assets = [{"asset": "STK", "symbol": symbol, "realized": None, "unrealized": None,
                   "total": total, "currency": base_currency}
                  for symbol, total in sorted(stock_totals.items(), key=lambda item: abs(item[1]), reverse=True)]
    pnl_assets_note = ("个股盈亏使用 IBKR MTMPerformanceSummary 的基础货币值，仅显示完整落入所选日期的 Flex 报告；"
                       "部分报告区间暂不拆分。" if partial_stock_periods else
                       "个股区间盈亏按 IBKR MTMPerformanceSummary 的基础货币值统计；不含现金利息与股息。")
    prior_nav_point = next((p for p in reversed(nav_points) if date_from and p["date"] < date_from), None)
    nav_history = ([prior_nav_point] if prior_nav_point else []) + [
        p for p in nav_points if (not date_from or p["date"] >= date_from) and (not date_to or p["date"] <= date_to)]
    recent_cashflows = sorted(cashflows, key=lambda e: e["date"], reverse=True)[:100]
    recent_dividends = sorted(dividends, key=lambda e: e["date"], reverse=True)[:100]
    recent_tax = sorted(tax, key=lambda e: e["date"], reverse=True)[:100]
    recent_interest = sorted(interest, key=lambda e: e["date"], reverse=True)[:100]
    trades = unpack(db, iid, "交易")
    trade_headers = [r for r in trades if "日期/时间" in r]
    trade_rows = []
    for r in trade_headers:
        discriminator = v(r, "DataDiscriminator", 0)
        if discriminator not in ("Order", "Trade"):
            continue
        trade_rows.append({"type": discriminator, "asset": v(r, "资产分类", 1), "currency": v(r, "货币", 2),
                           "symbol": v(r, "代码", 3), "date": v(r, "日期/时间", 4), "quantity": n(v(r, "数量", 5)),
                           "price": n(v(r, "交易价格", 6)), "proceeds": n(v(r, "收益", 8)),
                           "realized": n(v(r, "已实现的损益", 11)), "mtm": n(v(r, "按市值计算的损益", 12)),
                           "side": v(r, "买卖", 13)})
    for events in (cashflows, dividends, tax, interest):
        for event in events:
            event.pop("_base_amount", None)
    return {"empty": False, "account": account, "accounts": accounts, "latest": latest,
            "imports": selected, "base_currency": base_currency,
            "range": {"start": date_from, "end": date_to, "min": first_date, "max": last_date},
            "kpis": {"net_liquidation": nlv_value, "cash": cash_value, "stocks": equity_value,
                     "period_realized_unrealized": range_pnl, "period_mtm": range_net_asset_change,
                     "deposits_withdrawals": deposit_total,
                     "dividends": base_dividends, "withholding_tax": base_tax,
                     "interest": base_interest},
            "positions": positions, "pnl_assets": pnl_assets, "pnl_assets_note": pnl_assets_note,
            "history": monthly, "monthly_pnl": monthly, "daily_pnl": selected_daily, "nav_history": nav_history,
            "cashflows": recent_cashflows, "dividends_detail": recent_dividends,
            "tax_detail": recent_tax, "interest_detail": recent_interest,
            "trades": sorted(trade_rows, key=lambda e: e["date"], reverse=True)[:200]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/flex/config":
            config = read_flex_config()
            self.send_json({"configured": bool(config["token"] and config["query_id"]),
                            "query_id": config["query_id"]})
            return
        if parsed.path == "/api/summary":
            account = parse_qs(parsed.query).get("account", [None])[0]
            params = parse_qs(parsed.query)
            date_from = params.get("from", [None])[0]
            date_to = params.get("to", [None])[0]
            with connect() as db: self.send_json(summary(db, account, date_from, date_to))
            return
        if parsed.path == "/api/imports":
            with connect() as db: self.send_json(all_imports(db))
            return
        relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
        path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in path.parents and path != WEB_ROOT / "index.html":
            self.send_error(404); return
        if not path.is_file(): self.send_error(404); return
        body = path.read_bytes(); content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/css; charset=utf-8" if path.suffix == ".css" else "application/javascript; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/imports/clear":
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024:
                self.send_json({"error": "请求内容过大。"}, 413); return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if payload.get("confirm") is not True:
                    self.send_json({"error": "清空操作需要明确确认。"}, 400); return
                with connect() as db:
                    count = db.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
                    db.execute("DELETE FROM imports")
                FLEX_PARSE_CACHE.clear()
                self.send_json({"cleared": count})
            except Exception:
                self.send_json({"error": "清空导入数据失败。"}, 500)
            return
        if self.path in ("/api/flex/config", "/api/flex/sync"):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 50 * 1024:
                self.send_json({"error": "请求内容过大。"}, 413); return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/flex/config":
                    result = save_flex_config(str(payload.get("token", "")).strip(),
                                              str(payload.get("query_id", "")).strip())
                else:
                    result = fetch_flex_report(str(payload.get("from", "")), str(payload.get("to", "")))
                self.send_json(result)
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
            except Exception:
                self.send_json({"error": "处理 Flex 请求时发生错误；令牌不会写入日志。"}, 500)
            return
        if self.path != "/api/import": self.send_json({"error": "Not found"}, 404); return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 50 * 1024 * 1024: self.send_json({"error": "单个文件不能超过 50 MB。"}, 413); return
        content_type = self.headers.get("Content-Type", "")
        raw = self.rfile.read(length)
        try:
            if "multipart/form-data" not in content_type: raise ValueError("请使用文件上传表单导入 CSV。")
            message = BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
            part = next((p for p in message.iter_parts() if p.get_param("name", header="content-disposition") == "file"), None)
            if not part: raise ValueError("没有收到 CSV 文件。")
            filename = Path(part.get_filename() or "ibkr.csv").name
            if not filename.lower().endswith(".csv"): raise ValueError("请选择 .csv 文件。")
            result = import_file(filename, part.get_payload(decode=True) or b"")
            self.send_json(result, 200)
        except ValueError as e: self.send_json({"error": str(e)}, 400)
        except Exception as e:
            print(f"Import failed: {e}"); self.send_json({"error": "导入失败，请检查文件格式；原有数据未更改。"}, 500)


if __name__ == "__main__":
    init_db()
    print(f"IBKR Viewer running at http://127.0.0.1:{PORT} (database: {DB_PATH})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
