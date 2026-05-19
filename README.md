# 标普/纳指智能定投执行器 · GitHub Actions + GitHub Pages 半后端版

## 作用

- GitHub Actions 每天自动运行 `scripts/update_data.py`。
- Python 脚本自动抓取纳斯达克100与标普500最新日线数据。
- 自动计算 5/10/20 日累计涨跌、阴跌补丁、大跌簇、滚动预算。
- 自动生成 `data/latest.json`。
- GitHub Pages 展示 `index.html`。

## 文件结构

```text
.github/workflows/update-data.yml   # 自动更新 + 部署 Pages
data/config.json                    # 目标仓位、预算口径、策略参数
data/ndx_history.csv                # 纳指1986-2026历史种子数据
data/spx_history.csv                # 标普1986-2026历史种子数据
scripts/update_data.py              # 半后端数据生成器
index.html                          # 静态网页
requirements.txt                    # Python依赖
```

## 必须配置的 GitHub Secret

在仓库 Settings → Secrets and variables → Actions → New repository secret 新增：

```text
TWELVE_DATA_API_KEY = 你的 Twelve Data API Key
```

不要把 API Key 直接写进代码。

## 页面部署设置

仓库 Settings → Pages → Build and deployment：

```text
Source: GitHub Actions
```

## 手动触发

Actions → Update ETF DCA Data → Run workflow。

## 修改目标金额

编辑 `data/config.json`：

```json
"targets": {
  "ndx": {"current_cost_at_start": 285500, "target_cost": 1330000},
  "spx": {"current_cost_at_start": 951600, "target_cost": 1620000}
}
```

金额单位是人民币元。

## 预算口径

`budget_count_mode` 有两个选项：

- `recommended`：预算按“场外建议 + 场内候选”计入，偏保守。
- `outside_only`：预算只按场外建议计入，适合场内很少实际买入的情况。

默认使用 `recommended`。
