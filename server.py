from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("IBKR_DB", ROOT / "data" / "ibkr.sqlite3"))
WEB_ROOT = ROOT / "web"
PORT = int(os.environ.get("PORT", "8000"))


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


def import_file(filename, content):
    digest = hashlib.sha256(content).hexdigest()
    parsed = parse_csv(content)
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


def v(row, label, index=None):
    if label in row:
        return row[label]
    vals = row.get("_values", [])
    return vals[index] if index is not None and len(vals) > index else ""


def latest_snapshot(records):
    return records


def summary(db, account=None):
    imports = all_imports(db, account)
    if not imports:
        return {"imports": [], "accounts": [], "empty": True}
    accounts = sorted({i["account"] for i in imports})
    account = account if account in accounts else accounts[0]
    selected = [i for i in imports if i["account"] == account]
    latest = selected[-1]
    iid = latest["id"]
    nlv = unpack(db, iid, "净资产值")
    nav_total = next((r for r in nlv if v(r, "资产类型", 0) == "总数"), None)
    nav_cash = next((r for r in nlv if v(r, "资产类型", 0) == "现金"), None)
    nav_equity = next((r for r in nlv if v(r, "资产类型", 0) == "股票"), None)
    positions = [r for r in unpack(db, iid, "未平仓持仓") if v(r, "DataDiscriminator", 0) == "Summary"]
    realized = unpack(db, iid, "已实现和未实现的表现总结")
    realized_total = next((r for r in realized if v(r, "资产分类", 0) in ("总数", "Total")), None)
    pnl_change = unpack(db, iid, "净资产值变更")
    cashflows = []
    dividends, tax, interest = [], [], []
    for imp in selected:
        for r in unpack(db, imp["id"], "存款和取款"):
            amount = v(r, "金额", 3)
            if amount and v(r, "货币", 0) != "总数":
                cashflows.append({"date": v(r, "结算日期", 1), "currency": v(r, "货币", 0),
                                  "description": v(r, "描述", 2), "amount": n(amount), "period": imp["period_end"]})
        for section, target in (("股息", dividends), ("代扣税", tax), ("利息", interest)):
            for r in unpack(db, imp["id"], section):
                amount = v(r, "金额", 3)
                if amount and v(r, "货币", 0) != "总数":
                    target.append({"date": v(r, "日期", 1), "currency": v(r, "货币", 0),
                                   "description": v(r, "描述", 2), "amount": n(amount), "period": imp["period_end"]})
    # Remove exact overlap rows while retaining legitimate identical occurrences from a single statement.
    def dedupe(events):
        seen = set(); out = []; counts = Counter()
        for e in events:
            key = (e["date"], e["currency"], e["description"], e["amount"])
            scope = (e["period"], key)
            occurrence = counts[scope]
            counts[scope] += 1
            marker = (key, occurrence)
            if marker not in seen:
                seen.add(marker); out.append(e)
        return out
    cashflows, dividends, tax, interest = map(dedupe, (cashflows, dividends, tax, interest))
    # Use the current statement's realized/unrealized totals and NAV movement as report-period figures.
    realized_pnl = n(v(realized_total, "总数", 13)) if realized_total else 0
    report_change = next((n(v(r, "域值", 1)) for r in pnl_change if v(r, "域名称", 0) == "按市值计价"), 0)
    # The summaries below use the account's base currency; individual events retain their original currency.
    net_flows = sum(e["amount"] for e in cashflows if e["currency"] == latest.get("base_currency"))
    deposit_total = net_flows
    base_dividends = sum(e["amount"] for e in dividends if e["currency"] == latest.get("base_currency"))
    base_tax = sum(e["amount"] for e in tax if e["currency"] == latest.get("base_currency"))
    base_interest = sum(e["amount"] for e in interest if e["currency"] == latest.get("base_currency"))
    nlv_value = n(v(nav_total, "当前合计", 4)) if nav_total else 0
    cash_value = n(v(nav_cash, "当前合计", 4)) if nav_cash else 0
    equity_value = n(v(nav_equity, "当前合计", 4)) if nav_equity else 0
    monthly = []
    for imp in selected:
        changes = unpack(db, imp["id"], "净资产值变更")
        end = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "结束价值"), None)
        start = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "开始价值"), None)
        flows = next((n(v(r, "域值", 1)) for r in changes if v(r, "域名称", 0) == "存款和取款"), None)
        period_pnl = (end - start - flows) if start is not None and end is not None and flows is not None else 0
        monthly.append({"period": f"{imp['period_start']} – {imp['period_end']}", "pnl": period_pnl,
                        "start": start, "end": end, "flows": flows or 0})
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
                           "side": v(r, "代码", 13)})
    pnl_assets = []
    for r in realized:
        asset = v(r, "资产分类", 0)
        if asset and asset not in ("总数", "Total") and v(r, "代码", 1):
            pnl_assets.append({"asset": asset, "symbol": v(r, "代码", 1), "realized": n(v(r, "已实现的 总数", 7)),
                               "unrealized": n(v(r, "未实现的 总数", 12)), "total": n(v(r, "总数", 13))})
    return {"empty": False, "account": account, "accounts": accounts, "latest": latest,
            "imports": selected, "base_currency": latest.get("base_currency") or "USD",
            "kpis": {"net_liquidation": nlv_value, "cash": cash_value, "stocks": equity_value,
                     "period_realized_unrealized": realized_pnl, "period_mtm": report_change,
                     "deposits_withdrawals": deposit_total,
                     "dividends": base_dividends, "withholding_tax": base_tax,
                     "interest": base_interest},
            "positions": positions, "pnl_assets": pnl_assets, "history": monthly,
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
        if parsed.path == "/api/summary":
            account = parse_qs(parsed.query).get("account", [None])[0]
            with connect() as db: self.send_json(summary(db, account))
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
