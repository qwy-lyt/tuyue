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

1. **先备份**：
   ```
   .venv\Scripts\python.exe scripts\backup.py
   ```
   备份写到项目下的 `backups\`（在 `data\` 外面，所以重置 data 不会连备份一起毁掉）。
2. **先问用户**：现在有没有已经注册的账号？有没有不想丢的对话？
3. **能不动就不动**。调试需要干净环境时，用 `YUE_DATA_DIR` 指向临时目录起一个隔离实例，
   不要动真实数据：
   ```
   set YUE_DATA_DIR=%TEMP%\yue_test
   ```
   `scripts/smtptest.py` 就是这么做的，可以照抄。

### 测试脚本的注意事项

- `selftest.py` 不碰数据库。
- `authtest.py` 会创建和删除名为 `authtest_*` 的账号，只在数据库为空时才测管理员分支。
- `httptest.py` / `livetest.py` 用 `_client.py` 建 `selftest_temp` 临时账号，结束即删。
- `smtptest.py` 完全隔离，用临时数据目录。

## 其他约定

- 密码用 `hashlib.scrypt` 加盐；会话 token 只存 SHA-256 哈希；别引入 bcrypt 之类的额外依赖。
- 登录只用密码。曾经实现过邮箱验证码（SMTP + 6 位码），用户明确要求整体移除，不要再加回来；
  同理也不要引入 TOTP——用户不想让使用者装验证器 App。
- 用户表里的 `email` 列是那次功能的遗留字段，已不再读写。**不要为了清理它去 DROP COLUMN**。
- 每个账号用自己的 API Key，服务端不做共用兜底。
- 前端是零构建的原生 HTML/CSS/JS，不引入打包工具和 CDN 资源（CSP 限制 `script-src 'self'`）。
- 服务默认只监听 `127.0.0.1`；对外的路径是 Radmin VPN + 只放行 `26.0.0.0/8` 的防火墙规则。

## 启动服务：不要用后台任务

**不要用工具的后台任务机制启动服务。** 那样起来的进程挂在工具自己的进程树里，
工具清理时会把服务一起带走，用户那边表现为"网站突然打不开"，而且没有任何报错。

用 `start_server.ps1`（内部用 `Start-Process` 脱离进程树）：

```powershell
powershell -ExecutionPolicy Bypass -File E:\LYT\ai-chat\start_server.ps1
```

它自带端口占用检查，会等到接口真的响应才返回。日志在 `data\server.log`。

需要重启时：先 `Get-NetTCPConnection -LocalPort 8000` 找到 PID 并结束，
再跑上面的脚本。改代码后必须重启它才会生效（没有开 `--reload`）。

## 改完代码要跑

```
.venv\Scripts\python.exe scripts\selftest.py    # 解析链路，不花钱
.venv\Scripts\python.exe scripts\authtest.py    # 账号与权限
.venv\Scripts\python.exe scripts\migrationtest.py  # 数据迁移（自起隔离实例）
.venv\Scripts\python.exe scripts\httptest.py    # HTTP 接口
.venv\Scripts\python.exe scripts\livetest.py    # 真实问答，会消耗 API 额度
```
