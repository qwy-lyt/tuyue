# 给接手这个项目的人（和 AI）的约定

## 最高优先级：绝不能弄丢用户数据

`data/` 目录里是真实用户的东西——账号、密码哈希、每个人的 API Key、对话记录、上传的图纸。
**这里没有可以重新生成的内容。**

### 绝对不要做的事

- **不要删除或重建 `accounts.db`**，即使是为了让测试跑通、为了"留个干净状态"、或者因为表结构变了。
- **不要用 `del` / `rmdir` 清理 `data/` 下的任何东西**，除非用户明确要求删某一条。
- **不要用 `DROP TABLE` 或"建新表再拷数据"的方式改表结构。** 迁移写错不会报错，只会静默丢数据。
- **不要在用户可能已经注册之后，假设"数据库应该是空的"。**

### 改表结构怎么办

`app/accounts.py` 里的 `_REQUIRED_COLUMNS` 就是为此准备的：**加字段就登记到那里**，
`init_db()` 启动时会用 `ALTER TABLE ... ADD COLUMN` 补上，老数据库能平滑升级。

这个机制**只增不减**。想删字段或改类型，先备份，并且问用户同不同意。

### 动数据之前必须做的

1. **先备份**：`.venv\Scripts\python.exe scripts\backup.py`
2. **先问用户**：现在有没有已经注册的账号？有没有不想丢的对话？
3. **能不动就不动**。调试需要干净环境时，用 `YUE_DATA_DIR` 指向临时目录起一个隔离实例，
   不要动真实数据。`scripts\migrationtest.py` 就是这么做的，可以照抄。

### 用户表里的 `email` 列

那是"邮箱验证码"功能的遗留字段，已不再读写。**不要为了清理它去 DROP COLUMN**——
删列要重建表，而重建表正是上次丢账号的原因。

## 启动服务：不要用工具的后台任务

**不要用工具自带的后台任务机制启动服务。** 那样起来的进程挂在工具自己的进程树里，
工具清理时会把服务一起带走，用户那边表现为"网站突然打不开"，而且没有任何报错。

用 `start_server.ps1`（内部用 `Start-Process` 脱离进程树）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_server.ps1
```

它自带端口占用检查，会等到接口真的响应才返回。日志在 `data\server.log`。

改代码后必须重启才生效（没有开 `--reload`）。

## 其他约定

- 密码用 `hashlib.scrypt` 加盐；会话 token 只存 SHA-256 哈希；别引入 bcrypt 之类的额外依赖。
- 登录只用密码，**不要再加回邮箱验证码或 TOTP**——用户明确表示不想让使用者装验证器 App。
- 每个账号用自己的 API Key，服务端不做共用兜底。
- 前端是零构建的原生 HTML/CSS/JS，不引入打包工具和 CDN 资源（CSP 限制 `script-src 'self'`）。
- 服务默认只监听 `127.0.0.1`，不主动对外暴露。要开放先看 README 的部署章节。

## 发布前扫一遍

仓库是公开的，推之前请确认没有把密钥或个人部署信息带进去：

```powershell
.venv\Scripts\python.exe scripts\secretscan.py   # 从 .env 读真实密钥值，逐个文件比对
```

它会检查 `.env` 里所有名字含 KEY / SECRET / TOKEN / PASSWORD / INVITE 的值有没有出现在待提交文件里，
另外还会找 `sk-`、`ghp_` 这类特征串和绝对路径。**通过之前不要推。**

## 改完代码要跑

```bat
.venv\Scripts\python.exe scripts\selftest.py       # 解析链路，不花钱
.venv\Scripts\python.exe scripts\authtest.py       # 账号与权限
.venv\Scripts\python.exe scripts\migrationtest.py  # 数据迁移，隔离运行
.venv\Scripts\python.exe scripts\httptest.py       # HTTP 接口
.venv\Scripts\python.exe scripts\livetest.py       # 真实问答，会消耗 API 额度
```
