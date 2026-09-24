# IBKR Portfolio Viewer

一个本机运行的 Interactive Brokers 活动账单查看器。Python 标准库提供 HTTP API 和 SQLite 存储，前端使用原生 HTML、CSS、JavaScript；不需要安装第三方包，也不把账单上传到外部服务。

## 启动

在 Windows PowerShell 中运行：

```powershell
cd E:\gitlab\ibkr-viewer
python server.py
```

浏览器打开 <http://127.0.0.1:8000>。停止服务时在终端按 `Ctrl+C`。首次启动会在 `data\ibkr.sqlite3` 创建本地数据库。可通过环境变量 `PORT` 修改端口，或通过 `IBKR_DB` 指定数据库路径。

## 导入与更新

点击页面右上角的“导入 CSV”，可以一次选择多份 IBKR 活动账单 CSV 或 Flex Query CSV。Flex Query 请导出为 CSV，并在查询中包含账户信息、净资产值和现金流水等区块。后续只要补充新的 CSV 再导入即可：

- 相同文件按 SHA-256 识别并跳过。
- 跨账单重叠的交易、股息、税款、利息和资金流水按日期、币种、说明和金额去重，同时保留同一账单中内容相同的多笔原始记录。
- 导入窗口提供“清空已导入数据”操作，需再次确认；会清空所有账户账本记录，但保留 Flex 配置和本机下载的 Flex CSV。
- 持仓和账户快照按所选结束日期，取不晚于该日期的最近账单；页面默认展示全部历史区间。
- 所有原始分区数据都保存在本机 SQLite 数据库。

## 页面内容

- **总览**：历史总账本的净清算价值、现金、股息、区间盈亏趋势、资产配置和近期活动；可按日期范围筛选。
- **持仓**：所选结束日期的未平仓持仓、数量、成本基础、市值与未实现盈亏；可回看历史快照。
- **盈亏表现**：账单期已实现/未实现表现、MTM、证券盈亏明细及净资产变化扣除账单净出入金后的区间盈亏。
- **资金与分红**：存取款、股息、代扣税、利息的独立流水表。
- **交易记录**：最新账单中的订单汇总和成交明细。
- **Flex API**：本地保存 Flex Web Service 令牌和 Query ID，按日期请求并导入 CSV 报告；也可下载 Flex CSV 后用右上角手动导入。

汇总金额统一显示为账户基础货币；明细保留 IBKR 原币金额，不将不同币种相加。跨币种换算仅使用 IBKR 账单本身提供的基础货币汇总。区间盈亏使用“期末净资产 − 期初净资产 − IBKR 报告的净存取款”，与时间加权收益率、MTM 的统计定义并不相同。

历史净值曲线优先使用 Flex Query 的逐日报告净值，否则使用活动账单提供的期初/期末快照。区间盈亏曲线按导入账单显示；要查看更细的区间盈亏变化，请导入覆盖对应日期范围的 Flex Query。

## API

- `GET /api/summary`：默认账户的汇总和最新快照。
- `GET /api/summary?account=U...&from=YYYY-MM-DD&to=YYYY-MM-DD`：指定账户和日期范围；期末快照取不晚于 `to` 的最近账单。
- `GET /api/imports`：已导入账单索引。
- `POST /api/import`：以 `multipart/form-data` 上传单份 CSV，字段名为 `file`。
- `GET /api/flex/config`、`POST /api/flex/config`：读取（不返回令牌）或保存本地 Flex 配置。
- `POST /api/flex/sync`：使用配置的 Flex Query ID 和日期范围获取报告并尝试导入。
