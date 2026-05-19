# 标普/纳指智能定投执行器 · GitHub Actions + GitHub Pages 增强版 v2

## v2 相比 v1 改了什么

1. 修复“刷新页面数据”体验：按钮现在会明确显示刷新状态，并用 cache bust 重新读取 `data/latest.json`。
2. 恢复国内ETF溢价模块：前端抓腾讯行情、Palmmicro三EST、HaoETF快照，LOF已剔除。
3. 增加折后便宜度：`1 - (1 + 指数涨跌幅) × (1 + ETF溢价)`。
4. 增加最终可执行金额：区分“场内候选”和“经溢价过滤后可执行的场内金额”。
5. 增加手动输入兜底计算：当指数/ETF接口异常时，可以手动填涨跌幅和最低溢价计算。
6. 完整保留阴跌补丁、大跌簇、滚动预算上限的理论说明和可视化状态。

## 部署方法

把本包所有内容上传到你的 GitHub 仓库根目录，覆盖原来的 v1 文件即可。然后手动运行一次：

`Actions → Update ETF DCA Data → Run workflow`

GitHub Pages 仍然选择：

`Settings → Pages → Source → GitHub Actions`

## 注意

- “刷新页面数据”不会触发 GitHub Actions 重新跑后端，只是重新读取已经生成并部署的 `data/latest.json`。
- 如需重新抓取 Twelve Data / Yahoo 并生成最新后端数据，请进入 GitHub Actions 手动 Run workflow，或等待每日定时任务。
- 国内ETF溢价是前端实时抓取，可能受跨域代理影响；失败时请用“手动输入兜底计算”。
