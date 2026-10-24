# moni Chrome Profiles 管理与隔离指南 (v1.0.56)

本文档说明 moni 系统中 Chrome 实例配置文件的存储结构、安全隔离边界、查看工具及迁移操作指引。

---

## 1. 现状与问题排查

### 1.1 根因分析
在早期版本中，moni 直接将 macOS 系统的 Chrome 用户数据根目录 `~/Library/Application Support/Google/Chrome/` 内的目录传入 `--user-data-dir`：

| 磁盘路径 | 所有者 | 实际内容 | moni 既往行为 |
|---|---|---|---|
| `Chrome/Profile 6/` 根层 | **用户真实 profile (UI: hermes)** | Bookmarks, History, Cookies (338个), Preferences, Login Data (1.6G) | **误将其当成 user-data-dir 传入 xwatch** |
| `Chrome/Profile 6/Default/` | **moni xwatch 专属** | Cookies (274个，含 17 个 x.com 登录态), Service Worker (1.2G 缓存), IndexedDB | Chrome 在 user-data-dir 下自动创建的子目录 |
| `Chrome/Profile 7/` 整个目录 | **moni daemon (36kr)** | 36kr Cookies (2个), 缓存 (153M) | moni daemon 启动时创建并使用的独立 user-data-dir |

### 1.2 造成的体感影响
1. **浏览器反复崩溃提示**: moni 进程退出或异常退出时，会在 `Profile 6/Default/Preferences` 中留下 `exit_type = Crashed`。用户日常打开 Chrome（切换账号或启动时）会弹出“恢复未正常关闭的页面”，体感上以为日常浏览器被 moni 强行重启。
2. **根目录污染与文件锁冲突**: moni 启动时向 `Profile 6/` 根层注入 `SingletonLock`、`SingletonSocket`、`SingletonCookie` 与 `Local State`，干扰 Chrome 正常的多 Profile 实例调度。
3. **名字混淆**: Chrome UI 上显示的是账号昵称（如 `hermes`、`Marius`），磁盘上则是按创建顺序命名的目录（`Profile 6`、`Profile 1`）。

---

## 2. 目标隔离架构

将 moni 的所有 Chrome 运行时数据移出系统 Chrome 目录，统一收敛在 `~/.moni/chrome/`：

```
~/.moni/chrome/
├── xwatch/                  ← moni xwatch (X/Twitter 抓取)
│   ├── Default/             ← 完整保留原 Profile 6/Default/ 内容 (含 17 个 x.com 登录态 cookies)
│   └── Local State          ← xwatch 专属 Local State
├── daemon/                  ← moni chrome_daemon (36kr 抓取，原 Profile 7 整体迁移)
│   ├── Default/
│   └── Local State
├── profile-map.json         ← catalog 目录索引，记录映射关系与迁移元数据
├── daemon.lock              ← 进程单实例锁
└── backoff-*.json           ← 跨进程退避状态
```

### 迁移后 Chrome 系统目录变化
- `Chrome/Profile 7/` 彻底消失；
- `Chrome/Profile 6/Default/`（moni 污染层）彻底消失；
- `Chrome/Profile 6/` 根层（用户 hermes 真实账号数据、338 个 Cookies、书签、历史记录）**完全不受影响、一个字节不动**；
- Chrome 根目录下恢复干净的 4 个用户 Profile：`Default`、`Profile`、`Profile 1`、`Profile 6`。

---

## 3. 核心安全边界

1. **绝对不碰用户真实 Profile**: 迁移脚本严格限定仅移动 `Profile 6/Default` 和 `Profile 7`，绝不允许对 `Profile 6` 根目录执行 `mv`。
2. **进程占用强检测**: 搬迁前必须通过 `lsof +D` 确认没有运行中的 Chrome 或 moni 进程持有目标目录；若有进程占用，拒绝执行并提示退出。
3. **精简备份优先**: 执行搬迁前自动在 `~/Documents/moni-chrome-profile-backup-YYYYMMDD/` 下创建 tar 归档，并自动排除 1.2G 的 `Service Worker` 缓存文件，只备份有状态数据（Cookies、Local Storage、IndexedDB、Preferences 等）。
4. **SQLite Cookies 校验**: 迁移后自动校验 Cookies 总数和 x.com 登录态 Cookies 数量必须前后严格一致。
5. **完整可逆回滚**: 支持 `--rollback` 幂等还原。

---

## 4. 工具使用指南

### 4.1 查看全量 Profile 对照表
```bash
/usr/local/bin/python3 scripts/chrome-profiles.py
```
**输出示例**:
```
Chrome UI 名字   磁盘目录                   所有者   大小     账号                         moni 在用?    
----------------------------------------------------------------------------------------------------------
用户1            Default                    用户     872M     fledgeel@gmail.com           否            
您的 Chrome      Profile                    用户     28M      linqin7533@gmail.com         否            
Marius           Profile 1                  用户     1.7G     mariusiaowego@gmail.com      否            
hermes           Profile 6                  用户     1.6G     matthew.qi.tao@gmail.com     否            
(moni daemon legacy) Profile 7                  moni     156M     —                            是(遗留目录)  
(moni xwatch legacy) Profile 6/Default          moni     1.2G     matthew.qi.tao@gmail.com     是(登录态/待迁移)
```

### 4.2 迁移预检 (Dry Run)
在不修改任何磁盘文件的前提下，检查源目录大小、Cookie 数量、进程锁状态并输出搬迁计划：
```bash
/usr/local/bin/python3 scripts/migrate-chrome-profiles.py --dry-run
```

### 4.3 执行搬迁 (Apply)
> ⚠️ **注意**: 必须确认没有运行中的 Chrome/moni 进程占用。
```bash
/usr/local/bin/python3 scripts/migrate-chrome-profiles.py --apply
```

### 4.4 故障回滚 (Rollback)
若搬迁后需要恢复到原始状态：
```bash
/usr/local/bin/python3 scripts/migrate-chrome-profiles.py --rollback
```

---

## 5. 代码接口与兼容性

代码统一通过 `moni.fetch.profile_paths` 模块解析路径：

```python
from moni.fetch.profile_paths import (
    resolve_daemon_user_data_dir,
    resolve_xwatch_user_data_dir,
)

# 获取 daemon user-data-dir
daemon_dir = resolve_daemon_user_data_dir()

# 获取 xwatch user-data-dir
xwatch_dir = resolve_xwatch_user_data_dir()
```

### 环境变量与回退逻辑
1. 若设置了 `MONI_CHROME_USER_DATA_DIR` 或 `MONI_XWATCH_CHROME_USER_DATA`，以环境变量为最高优先级。
2. 默认使用新路径 `~/.moni/chrome/...`。
3. 若新目录不存在但检测到老目录存在且有有效数据，系统自动认领老目录并记录 warning，提示用户运行迁移脚本，保证老部署服务平滑不中断。
