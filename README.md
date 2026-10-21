# Tech & Finance Radar（免费 MVP）

GitHub Actions + GitHub Pages + Python + `intfloat/multilingual-e5-small`。
跨语言语义向量聚类、热度评分、新闻源展开、中英日筛选、7 天数据留存、72 小时事件看板。

## 1. 创建公开仓库

在 GitHub 创建 **Public** 仓库 `tech-finance-radar`，不勾选预创建 README。
将本文件夹内容提交至该仓库根目录（**不要只上传 ZIP**）。

若电脑安装了 Git，在本项目目录执行（将 YOUR_NAME 替换成 GitHub 用户名）：

```bash
git init
git branch -M main
git add .
git commit -m "Initial news radar"
git remote add origin https://github.com/YOUR_NAME/tech-finance-radar.git
git push -u origin main
```

首次 HTTPS push 可能需要使用浏览器登录、Git Credential Manager 或 SSH；不能使用 GitHub 账户密码直接进行 HTTPS Git 认证。

## 2. 配置 Pages

仓库 `Settings → Pages → Build and deployment → Source → GitHub Actions`。

## 3. 手动运行一次

仓库 `Actions → Collect, deduplicate and publish news → Run workflow → Run workflow`。
查看 build、deploy 两个 Job 均绿色。首次运行会下载安装 AI 模型及 Python 依赖。

然后访问 `https://YOUR_NAME.github.io/tech-finance-radar/`。

## 4. 自动更新

工作流 `17,47 * * * *`，按 UTC 每小时 17 和 47 分触发，GitHub 调度可能延迟或漏运行。
可以改为 `17 * * * *` 每小时一次，减轻 GitHub 工作负载。
公开仓库如果连续 60 天无活动，GitHub 可停用定时任务。

## 5. 配置新闻源

编辑 `config/feeds.json`，每项包含：
- `name`: 在页面上展示的来源名称
- `url`: RSS 或 Atom 链接（不是普通网页链接）
- `lang`: `zh` / `ja` / `en`
- `category`: `tech` / `finance`
- `type`: `publisher` / `aggregator` / `community`
- `publisher`: 该来源对应的出版方
- `ranked`: 只有**真正是热门榜单**时才设为 `true`

Hacker News API 在 `scripts/update.py` 中额外采集 top 25。
Google News、Reddit 和部分网站可能对抓取有额外限制，需要单独校验及遵守网站条款。

## 6. 调整 AI 合并强度

打开 `.github/workflows/update.yml` 修改 `SIMILARITY_THRESHOLD`：
- `0.94`: 更严格，较少错合并，也可能漏合并
- `0.91`: 当前起始值（**需要使用真实文章样本调试，不是验证过的最佳值**）
- `0.88`: 更宽松，但更容易合并不同事件

这是基于标题的语义聚类，加上数字冲突和 48 小时时窗，不具备完全准确的事件事实判断。跨语言不同公司译名、别名、相关新闻进展等仍可能误合并/漏合并。

## 7. 本地测试（可选）

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python scripts/update.py
python -m http.server 8000 --directory site
```

浏览器访问 `http://localhost:8000`。

## 注意

- **真正的跨语言 AI 语义计算**：模型在 GitHub Actions CPU 运行，**不是外部付费 AI API**。
- 文章留存 7 天，默认只聚类最近 72 小时内的文章；独立来源计数排除纯聚合器，防止转发加倍计数。
- 同一新闻如果来源全是聚合器，独立媒体数可能为 0。
- `data/articles.json` 是存储在 GitHub 仓库里的简单滚动档案，每次 Action 会提交更新。不是数据库。
- RSS 提取公开元数据，网页展示摘要和原站链接。不绕过付费墙。
- 不要把密钥、隐私数据写到公开代码中。
- GitHub 免费政策、网站 RSS 地址和使用条款将来可能变化。
