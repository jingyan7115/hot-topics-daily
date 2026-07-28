# 每日热点选题报告（云端版）

不依赖本机、不依赖 WorkBuddy 的每日热点选题流水线。由 **GitHub Actions** 每天北京时间 8:15 左右自动运行（考虑调度延迟，实际推送约 8:15-8:35）：

1. 抓取多平台热榜（微博/抖音/头条/B站等，多源兜底）
2. 趋势分析 + 双抖音账号（黑龙江卫视 / 这就是黑龙江）六维选题评分
3. 生成 Markdown + HTML 报告，附 HotFlashNews 民生栏目
4. 摘要推送到企业微信群（群机器人 Webhook）
5. 完整报告自动发布到 GitHub Pages（公网可访问，推送里带链接）

脚本为纯 Python 标准库实现，无任何第三方依赖。

## 一次性部署步骤（约 5 分钟）

### 1. 创建仓库并上传本目录

- 登录 GitHub → 新建仓库（建议名 `hot-topics-daily`，**Public**，Pages 免费版仅支持公开仓库）
- 把本目录所有文件推送上去（本地已 `git init` 并提交好，只需执行）：

```bash
git remote add origin https://github.com/<你的用户名>/hot-topics-daily.git
git push -u origin main
```

> 若介意仓库公开：配置中已无任何密钥，账号定位信息均为公开资料。仍想私有的话，可改 Private 并删除 workflow 中最后两个 Pages 步骤（企业微信推送不受影响，只是没有公网报告链接）。

### 2. 配置企业微信 Webhook（Secret，不入库）

仓库页面 → Settings → Secrets and variables → Actions → **New repository secret**

- Name: `WECOM_WEBHOOK`
- Value: 你的企业微信群机器人 Webhook 完整地址（与本机 WorkBuddy 配置中一致）

### 3. 启用 GitHub Pages

仓库页面 → Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**

### 4. 手动测试一次

仓库页面 → Actions → 「每日热点选题报告」→ **Run workflow**

约 1 分钟后：企业微信群应收到推送；`https://<你的用户名>.github.io/hot-topics-daily/` 可看到完整报告。

## 修改运行时间

编辑 `.github/workflows/daily-report.yml` 中的 cron（**UTC 时间**，= 北京时间 - 8 小时）：

| 想要的北京时间 | cron 写法 |
|---|---|
| 08:15 | `15 0 * * *` |
| 07:30 | `30 23 * * *` |
| 09:00 | `0 1 * * *` |

## 修改账号定位 / 推送条数

编辑 `hot-topics-config.yaml`（`accounts`、`push.top_n` 等），提交推送即可，下次运行自动生效。
