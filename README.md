# 图阅

上传 PDF 和 DWG 图纸，让 AI 读出里面的数据。

- **PDF**：先提取文字层；如果是扫描件或 CAD 出图（没有文字层），自动把页面渲染成图片交给视觉模型
- **DWG**：经 ODA File Converter 转成 DXF，提取图层、文字标注、块引用、尺寸，同时渲染整张图纸
- **多用户**：账号隔离、邀请码注册、每人配置自己的模型服务和 API Key
- **数据面板**：识图后右侧列出结构化数据（尺寸标注、技术要求、标题栏），可排序、筛选，随对话保存
- **本地存储**：所有数据落在项目目录内，不依赖数据库服务，复制文件夹即可搬走

技术栈：Python 3.11+ / FastAPI / SQLite，前端是原生 HTML/CSS/JS，零构建、无 CDN 依赖。

---

## 界面

左侧是对话与历史记录，中间是聊天区，右侧面板在识图完成后自动展开：上面是图纸的渲染图，下面是提取出的数据表。

表格支持点列头排序。**数值列按数字大小排而不是按字符串**——否则「100」会排在「40」前面；没有数值的行（技术要求这类）固定沉在底部，切换升序降序都不会翻上来。

面板宽度可以拖，双击分隔条复位。

---

## 环境要求

- **Windows 10/11**（DWG 转换器只有 Windows 和 Linux 版，macOS 用户需要自行解决）
- **Python 3.11 或更高**（开发时用的是 3.12）
- 一个支持图片输入的模型 API Key

---

## 快速开始

```bat
git clone https://github.com/qwy-lyt/tuyue.git
cd tuyue

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env
```

然后编辑 `.env`，至少填上 `INVITE_CODE`（随便设一串只有你知道的字符），保存后：

```bat
run.bat
```

浏览器打开 http://127.0.0.1:8000 ，点「用邀请码注册」创建第一个账号——**第一位注册的人自动成为管理员**。登录后点右上角「设置」，选服务商、填自己的 API Key，就能开始用了。

---

## 配置

全部配置在 `.env` 里，每一项都有中文注释。常用的几项：

| 变量 | 作用 |
|---|---|
| `APP_NAME` | 助手名字，显示在侧栏、标签页和回答头像上 |
| `INVITE_CODE` | 注册用的邀请码，第一位注册者成为管理员 |
| `AI_BASE_URL` / `AI_MODEL` | 默认模型接口和模型名，个人可在网页里覆盖 |
| `MAX_IMAGES_PER_TURN` | 单轮最多送几张图，默认 6 |
| `MAX_UPLOAD_MB` | 单个上传文件上限，默认 50 |
| `ODA_CONVERTER_PATH` | ODA 转换器路径，留空自动探测 |
| `DRAWING_CJK_FONT` | 图纸渲染用的中文字体，留空自动挑 |

**换成别的模型**只改两行、重启即可：

```ini
AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_MODEL=glm-5.3
```

注意：**要读图纸就必须用支持图片输入的模型**，纯文本模型会忽略渲染出来的图。内置了 DeepSeek、小米 MiMo、智谱、OpenAI 的服务商列表，也可以在网页里选「自定义」手填。

---

## 关于 DWG：装一个转换器，体验完全不同

DWG 是 Autodesk 的私有二进制格式，Python 生态里**没有**能直接读它的库。所以走这条路：

```
DWG ──[ODA File Converter]──> DXF ──[ezdxf]──> 文字/图层/块 → 交给 AI
                                    └────────> 渲染成图片 → 交给 AI 看图
```

**不装转换器也能用**，但会退化成「降级模式」：只能抠出 DWG 文件头里内嵌的预览缩略图，分辨率低、读不到文字标注。

安装 ODA File Converter（免费，约 10 分钟）：到 https://www.opendesign.com/guestfiles/oda_file_converter 注册一个账号（只需邮箱）→ 下载 Windows 版安装 → 重启本服务。左下角状态栏变成绿色的「ODA 转换器已就绪」即成功。

装到了非默认路径就在 `.env` 里指定 `ODA_CONVERTER_PATH`。

---

## 文件是怎么处理的

| 类型 | 处理方式 |
|---|---|
| **PDF** | 逐页提取文字。文字够读就只发文字；是扫描件或 CAD 出图就把页面渲染成图片交给视觉模型 |
| **DWG** | 经 ODA 转 DXF，用 ezdxf 提取图层名、文字标注、块引用、尺寸标注，同时渲染整张图纸 |
| **DXF** | 跳过转换，直接解析 |
| **PNG/JPG** | 压缩后直接交给视觉模型（也支持从剪贴板粘贴截图） |
| **TXT/MD/CSV** | 按 UTF-8/GBK 依次尝试解码后作为文本上下文 |

### 上下文预算

图片以 base64 内联进请求，而 DeepSeek 的请求体上限是 48 MiB，所以有两道限制：

- 每张图压到长边 ≤ `IMAGE_MAX_PX`（默认 1800px）的 JPEG
- 单轮最多带 `MAX_IMAGES_PER_TURN`（默认 6）张图，**从最新的一轮往前分配**

真撞上限时不会静默丢内容，聊天里会明确提示。如果提问涉及很后面的页面，更省的做法是单独上传那几页。

### 图纸里的中文变成方框？

程序检测到图纸含中文标注时，会把文本样式指向系统中文字体（优先 `simhei.ttf`）再渲染，通常不需要手动处理。

原理：AutoCAD 的 SHX 字体（`txt`、`simplex` 这些）在 ezdxf 里会解析到 Arial，而 Arial 没有中文字形。**文字提取不受影响**，方框只影响渲染出的图。系统里确实没有中文字体时，在 `.env` 里指定 `DRAWING_CJK_FONT`。

---

## 项目结构

```
tuyue/
├─ run.bat                 一键启动（首次会自动装依赖）
├─ start_server.ps1        后台静默启动，脱离启动它的程序
├─ .env                    你的配置（不提交）
├─ app/
│  ├─ main.py              FastAPI 路由
│  ├─ config.py            配置加载、缓存重定向
│  ├─ accounts.py          账号、会话、邀请码、审计（SQLite）
│  ├─ ai.py                消息组装与流式调用、结构化提取
│  ├─ store.py             对话与文件持久化
│  ├─ providers.py         模型服务商列表
│  ├─ secrets_box.py       API Key 加密存储
│  ├─ processing/          PDF / DWG / 图片 / 文本处理
│  └─ static/              前端页面
├─ scripts/                自测与运维脚本
└─ data/                   运行时数据（不提交）
```

所有运行时数据都在项目目录内——包括临时文件和 matplotlib/ezdxf 的缓存，都被重定向进了 `data/`。整个文件夹复制到别的机器就能跑。

---

## 自测

```bat
.venv\Scripts\python.exe scripts\selftest.py       # 解析链路，不花钱
.venv\Scripts\python.exe scripts\authtest.py       # 账号与权限
.venv\Scripts\python.exe scripts\migrationtest.py  # 数据迁移，隔离运行
.venv\Scripts\python.exe scripts\httptest.py       # HTTP 接口
.venv\Scripts\python.exe scripts\checkkey.py       # 只验 Key 能不能通
.venv\Scripts\python.exe scripts\livetest.py       # 真实问答，会消耗 API 额度
```

- **selftest** 造一份 DXF、一份文字型 PDF、一份扫描型 PDF，检查三条解析路径
- **migrationtest** 造一个旧版数据库跑升级，逐项核对账号、密码哈希都没被动过
- **livetest** 最有价值：在文件里埋入确定的事实，上传后提问，再检查模型答对没有

`httptest` / `livetest` 会自动创建临时账号并在结束后删除，也可以用 `YUE_TEST_USER` / `YUE_TEST_PASSWORD` 指定自己的账号。

---

## 部署

这个应用**需要一台一直在线的机器**来跑后端：Python 进程、SQLite、文件处理，缺一不可。

常见的选择：

- **自己的电脑**：最简单。想让别人访问就配合内网穿透工具或虚拟局域网（如 Radmin VPN、Tailscale）
- **云服务器**：一台最低配的 VPS 就够。注意 DWG 需要 ODA File Converter，Linux 版是 Qt 程序，无头运行要额外配置
- **PaaS（Zeabur、Railway、Render 等）**：部署最省事，但要挂持久化卷，否则重启后数据和上传的文件会丢

**不要把后端部署到 GitHub Pages**——它只能托管静态文件，跑不了 Python。

### 对外开放前请先做

1. 改掉默认的邀请码，并在管理后台管理好谁能注册
2. 如果走 HTTPS，把 `.env` 里的 `COOKIE_SECURE` 改成 `true`
3. 确认服务器磁盘上有多少空间给上传的图纸

---

## 已知限制

- **DWG 依赖外部转换器**：ODA File Converter 需要单独安装，且只有 Windows/Linux 版
- **每轮图片有上限**：图纸以图片形式发送，一次问太多张会超出限制
- **登录只有密码一道关**：没有两步验证。防暴力破解靠限流（同一账号 15 分钟 5 次、同一来源地址 20 次），但防不住密码在别处泄露
- **上传的文件会被 PyMuPDF、ezdxf、ODA 解析**：这些是复杂的 C/C++ 解析器，是主要的风险面。不建议对完全陌生的人开放上传
- **没有密码找回**：忘记密码需要管理员在后台重置

---

## 许可

尚未选定。在选定之前，保留所有权利。
