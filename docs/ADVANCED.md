# 进阶手册：macOS DEPNag Toolkit

**登录后的注册提醒控制工具 · v2.2.0 · 中文网络恢复手册**

[源代码](https://github.com/jackylam0812/macos-depnag-toolkit) · [v2.2.0 下载](https://github.com/jackylam0812/macos-depnag-toolkit/releases/tag/v2.2.0) · [问题反馈](https://github.com/jackylam0812/macos-depnag-toolkit/issues)

无需 U 盘：在 macOS 恢复模式联网下载完整工具包，把当前安装的备份会话保存在 Data 卷，再明确执行应用；正常启动后做只读验收。**下载本身不会修改原生状态，也不会自动运行准备或应用。**


> 日常执行请看[极简首页](../README.md)。本文保留手工逐步操作、回滚和故障排查细节。

## 一键入口与手工流程的区别

固定版本 `ONE_CLICK.sh` 提供以下入口：

| 命令 | 环境与行为 |
| --- | --- |
| `prepare` | 正常 macOS，以 `sudo` 运行；下载完整工具包、自检、只读检查。**不创建原生状态备份，也不应用。** 可以跳过，直接进入恢复模式运行 `apply`。 |
| `apply [DATA]` | 恢复模式；自动下载、自检，为当前状态创建新备份会话并应用。省略 DATA 时仅在唯一符合原生文件及真实挂载点条件的候选存在时自动选卷。 |
| `check` | 正常 macOS，以 `sudo` 运行；只读验证本次启动后的原生状态和日志。 |
| `rollback [DATA [SESSION_NAME]]` | 恢复模式；默认使用最后一次成功应用的会话，也可明确指定目标卷和会话目录名。 |

- 恢复模式先联网、挂载并解锁目标 Data 卷；多卷时明确指定挂载点，如 `apply /Volumes/Data`。`DATA` 是挂载点，不是设备编号或系统卷名称。
- 一键入口缓存位于 `Users/Shared/mdm-nag-recovery/toolkit-v2.2.0`，会话位于同一工作目录的 `sessions/`。恢复模式路径需带所选 Data 卷前缀，正常启动后使用 `/Users/Shared/...`。
- 一键入口只在**成功应用**后更新 `LAST_APPLIED_SESSION_NAME.txt`。准备、检查及已经关闭时的重复应用均不覆盖最后应用记录，旧备份保留。
- 下文手工流程使用另一个便利定位文件 `LATEST_SESSION_NAME.txt`；它代表最近准备会话，**不等于一键入口的最后成功应用会话**。两种流程的回滚定位不要混用。
- 默认回滚对应最后一次成功应用。明确回滚旧会话时使用 `rollback /Volumes/Data install-YYYYmmddTHHMMSSZ-PID`，将示例目录名替换为真实会话目录名；不要传入整个 plist 文件。
- 一键入口用工作目录中的 `.operation.lock` 目录阻止并发。异常断电可能留下锁；先确认没有其他入口进程运行，再对该**空锁目录**运行 `rmdir`。保留会话和备份，不删除整个工作目录来解锁。
- 成功应用输出 `result=one_click_applied`；原来已经关闭输出 `result=already_disabled`；成功回滚输出 `result=one_click_rolled_back`。`prepare` 在需要恢复模式处理时输出 `result=ready_for_recovery`。`check` 的退出码沿用本手册第 9 节的 `VERIFY_AFTER_BOOT.sh` 规则。

以下保留不使用一键入口、分别执行各核心脚本的方法。

## 1. 工具实际改变什么

本工具针对**已经能够登录 macOS 桌面后，由 DEPNag 再次安排的注册提醒**，只把当前安装下列 plist 的 `Disabled` 字段设置为布尔值 `true`，其余字段保留：

```text
/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist
Disabled = true
```

它不是 MDM 描述文件删除工具，也不解除 Apple 自动设备注册（ADE）的服务端分配。**全面抹盘后，首次设置助理中的“远程管理”属于另一环节；解除该分配仍需原管理方在服务端操作。** 本机字段不能保证跨抹盘、跨系统版本永久保留。

| 情况 | 操作 |
| --- | --- |
| 已进入桌面，随后出现注册提醒 | 针对当前安装准备新会话，在恢复模式应用，再重启验收。 |
| 普通重启或睡眠唤醒 | 先检查字段及原生日志，不必重复写入。 |
| 系统升级、重装或还原后 | 重新读取状态；需要处理时为当前状态创建新会话，不套用旧 UUID、旧哈希或整个旧 plist。 |
| Data 卷被抹掉 | 网络可重新获取工具，但已被删除的本机会话不会从 GitHub 自动恢复。 |
| 首次设置停在“远程管理” | 这不是本包已验证的登录后提醒，应处理服务端分配。 |

工具不删除 `/System/Library/LaunchDaemons/com.apple.ManagedClient.enroll.plist`，不改 SIP / Authenticated Root，不添加常驻程序、定时任务、hosts 屏蔽或 CoreFollowUp 数据库触发器。

## 2. 一次操作的顺序与目录

```text
恢复模式联网 → 下载固定版本 → 包自检
              ↓
读取当前安装，创建新会话（不写原生状态）
              ↓
用户明确执行 APPLY_IN_RECOVERY.sh
              ↓
正常重启 → STATUS.sh + VERIFY_AFTER_BOOT.sh → 观察原提醒时间点
```

以下使用**通用示例挂载名** `/Volumes/Data`；它不是对任何机器卷名的识别结果。磁盘工具显示不同挂载点时，请修改每个代码块中的 `DATA`。这些示例不包含真实机器的 UUID、哈希或备份。

| 内容 | 恢复模式路径 | 同一安装正常启动后的路径 |
| --- | --- | --- |
| 工具包 | `/Volumes/Data/Users/Shared/mdm-nag-recovery/toolkit-v2.2.0` | `/Users/Shared/mdm-nag-recovery/toolkit-v2.2.0` |
| 会话父目录 | `/Volumes/Data/Users/Shared/mdm-nag-recovery/sessions` | `/Users/Shared/mdm-nag-recovery/sessions` |
| 会话定位文件 | 上述工作目录中的 `LATEST_SESSION_NAME.txt` | 同名文件，仅保存会话目录名，不保存恢复模式绝对路径。 |

会话放在 **Data 持久目录**，不是 `/tmp`。重启后 `/Volumes/Data` 前缀不再适用；下文已分别给出两种环境的命令。工具和会话目录按私有权限创建，正常系统的读取命令使用 `sudo`。此处的“持久”仅指保留该 Data 卷的重启，**不包括抹掉 Data 卷**。

## 3. 在恢复模式联网并确认目标卷

### 3.1 进入恢复模式

- Apple Silicon：关机，长按电源键至启动选项，选择“选项”。
- Intel：开机时按住 Command-R。
- 在恢复环境使用 Wi-Fi 菜单连接网络，或连接可用网线，再打开“实用工具 → 终端”。
- 下载需要可访问 `raw.githubusercontent.com`、`github.com` 及 GitHub Release 重定向的资源域名。需要网页登录的网络可能要先完成网络认证。

### 3.2 识别 Data 卷

```sh
/usr/sbin/diskutil apfs list
/bin/ls -la /Volumes
```

在“磁盘工具”中找到目标安装的 **Data 卷**；若未挂载或被 FileVault 锁定，使用界面装载并解锁。密码只在系统界面输入。多套 macOS 安装时，不要把另一套系统的 Data 卷当作目标。

确认实际挂载点后检查原生文件：

```sh
DATA="/Volumes/Data"
/usr/sbin/diskutil info "$DATA"
/bin/ls -l "$DATA/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist"
```

目标文件必须已经存在。脚本不制造缺失的原生状态，也不把缺失解释为已关闭。

## 4. 从网络下载固定版本，不自动应用

### 4.1 获取下载器

先可在浏览器查看[固定版本下载器源代码](https://github.com/jackylam0812/macos-depnag-toolkit/blob/v2.2.0/GET_TOOLKIT.sh)。以下命令只下载、改名并检查 shell 语法；**不会执行下载器**。失败的 `.part` 文件不会进入后面的执行步骤。

```sh
(
  set -eu
  /usr/bin/curl --fail --location \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 20 --retry 3 \
    --output /tmp/GET_TOOLKIT-v2.2.0.sh.part \
    https://raw.githubusercontent.com/jackylam0812/macos-depnag-toolkit/v2.2.0/GET_TOOLKIT.sh
  /bin/mv /tmp/GET_TOOLKIT-v2.2.0.sh.part /tmp/GET_TOOLKIT-v2.2.0.sh
  /bin/sh -n /tmp/GET_TOOLKIT-v2.2.0.sh
  printf '%s\n' 'bootstrap_downloaded=yes; not_executed=yes'
)
```

只有看到 `bootstrap_downloaded=yes; not_executed=yes` 且没有错误时，才继续下一块。`sh -n` 只检查语法，不是代码可信性认证。

### 4.2 明确执行下载器

```sh
(
  set -eu
  umask 077
  DATA="/Volumes/Data"
  WORK="$DATA/Users/Shared/mdm-nag-recovery"
  /bin/mkdir -p "$WORK"
  /bin/sh /tmp/GET_TOOLKIT-v2.2.0.sh "$WORK/toolkit-v2.2.0"
)
```

下载器从 [v2.2.0 Release](https://github.com/jackylam0812/macos-depnag-toolkit/releases/tag/v2.2.0) 获取 `macos-depnag-toolkit-v2.2.0.tar.gz` 及其 `.tar.gz.sha256`，解包后执行包自检，**不运行 `PREPARE.sh` 或 `APPLY_IN_RECOVERY.sh`**。

成功输出应包含：

```text
SELF_TEST_OK
checksums=ok
native_write_attempted=no
result=toolkit_download_ok
```

同时会输出实际 `archive_sha256` 和 `toolkit_path`。目标工具目录必须尚不存在，父目录须已存在，各级目录不得为符号链接。如果已成功下载过，保留原目录并自检，不要覆盖：

```sh
DATA="/Volumes/Data"
KIT="$DATA/Users/Shared/mdm-nag-recovery/toolkit-v2.2.0"
/bin/sh "$KIT/SELF_TEST.sh"
```

自检失败时保留失败副本排查，重新下载到另一个**新目录**，后续命令也使用那个目录。

### 下载的信任与依赖

- 初始信任来自 **HTTPS、GitHub 账号 `jackylam0812` 和指定版本 tag**，不是来自独立签名。固定 `v2.2.0` 避免自动跟随 `main/latest`，但仓库管理者仍可能移动 tag 或替换 Release 资产。
- 下载的 hash 清单与 `sha256-file` 来自同一个发布渠道；它们校验一致性，**不等于独立的发布者身份认证**。helper 自检也不证明实现可信。需要更强来源认证时，应通过独立渠道核对可信的提交或发布哈希。
- 运行依赖恢复环境自带的 `/bin/sh`、`curl`、`tar`、`mktemp`、`awk` 及基础文件工具；本地准备/应用还依赖 `plutil`、`diskutil`、`stat`、`diff` 等 macOS 工具。SHA-256 使用随包原生程序，不要求 Perl、OpenSSL、Python、Homebrew 或 Git。
- 不使用 `curl | sh`，不关闭 TLS 校验，不执行下载失败后的部分包。网络/TLS/时钟问题先排查网络、系统时间和完整报错；缺工具时见第 10 节的正常系统预下载方法。

## 5. 为当前安装创建新会话

在**恢复模式**执行。这里只读取原生文件，在独立目录保存原始备份和修改副本；不修改原生文件。

```sh
(
  set -eu
  umask 077
  DATA="/Volumes/Data"
  WORK="$DATA/Users/Shared/mdm-nag-recovery"
  KIT="$WORK/toolkit-v2.2.0"
  NAME="install-$(/bin/date +%Y%m%d-%H%M%S)"
  SESSION="$WORK/sessions/$NAME"
  /bin/mkdir -p "$WORK/sessions"
  /bin/sh "$KIT/SELF_TEST.sh"
  /bin/sh "$KIT/PREPARE.sh" --volume "$DATA" "$SESSION"
  printf '%s\n' "$NAME" > "$WORK/LATEST_SESSION_NAME.txt"
  printf 'prepared_session=%s\n' "$SESSION"
)
```

成功应含 `result=prepared`、`target_Disabled=true`、`native_write_attempted=no`。定位文件仅在准备成功后更新，并且只记录如 `install-YYYYMMDD-HHMMSS` 的**目录名**，所以重启后不会指向失效的 `/Volumes/Data/...`。

每个会话包含：

| 文件 | 用途 |
| --- | --- |
| `ORIGINAL_FILE.plist` | 本次准备时原生状态的原始备份。 |
| `MODIFIED_FILE.plist` | 仅把 `Disabled` 设为布尔 `true` 的修改副本。 |
| `DIFF_FILE.diff` | 原始/修改副本的规范化差异。 |
| `MANIFEST.plist` | 当前 Data 卷 UUID、源/目标哈希、原字段值和元数据。 |
| `VERIFICATION.txt` | 准备阶段证据，不替代后续应用输出。 |

若新会话名碰巧已存在，换一个新名字，不覆盖旧会话。准备失败后不要继续应用；保留失败目录排查，也不要把定位文件里的旧会话当作本次成功结果。

**会话含机器特定配置及历史状态，不要上传到本 public 仓库、Issue 或 Release。** 工具包中的 `examples/` 和公开测试均使用合成数据。需要跨抹盘保留原备份时，另存到可信的私有备份位置；GitHub 源代码下载不能恢复丢失的本机备份。

## 6. 在恢复模式应用

确认第 5 节是**本次安装/本次状态**准备成功的会话，然后明确执行：

```sh
(
  set -eu
  DATA="/Volumes/Data"
  WORK="$DATA/Users/Shared/mdm-nag-recovery"
  KIT="$WORK/toolkit-v2.2.0"
  NAME=$(/bin/cat "$WORK/LATEST_SESSION_NAME.txt")
  case "$NAME" in
    install-*) ;;
    *) printf '%s\n' 'Invalid session name.' >&2; exit 2 ;;
  esac
  case "$NAME" in
    *[!A-Za-z0-9_-]*) printf '%s\n' 'Invalid session name.' >&2; exit 2 ;;
  esac
  SESSION="$WORK/sessions/$NAME"
  printf 'Data=%s\nSession=%s\n' "$DATA" "$SESSION"
  /bin/sh "$KIT/SELF_TEST.sh"
  /bin/sh "$KIT/APPLY_IN_RECOVERY.sh" "$DATA" "$SESSION"
)
```

`LATEST_SESSION_NAME.txt` 只是定位便利文件，不替代脚本对会话、卷 UUID、源文件内容的核对。成功应包括：

```text
result=apply
Disabled=true
native_write_attempted=yes
metadata=owner/group/mode/flags-preserved
```

还会输出本次实际目标 `sha256`；若已是该会话的目标状态，则为 `result=already_apply`、`Disabled=true`、`native_write_attempted=no`。保存此次完整输出或截图，然后通过苹果菜单正常重新启动。

失败不等于成功。自动回退是否完成，以 `rollback=verified` 或失败输出为准。工具不要求为本操作关闭 SIP 或 Authenticated Root。

## 7. 正常启动后的只读验收

此时使用**不带 `/Volumes/Data` 前缀**的安装内路径：

```sh
KIT="/Users/Shared/mdm-nag-recovery/toolkit-v2.2.0"
sudo /bin/sh "$KIT/SELF_TEST.sh"
sudo /bin/sh "$KIT/STATUS.sh"
sudo /bin/sh "$KIT/VERIFY_AFTER_BOOT.sh"
```

- `STATUS.sh` 应显示 `Disabled=true`、`result=disabled`、`native_write_attempted=no`。
- `VERIFY_AFTER_BOOT.sh` 默认查询本次开机以来的原生日志，列出 `schedule_or_launch_log_count`、`MiniBuddy_processes`、`Setup_Assistant_processes`；无新排期/启动记录且相关进程为 0 时报告当前窗口检查通过。
- `DEPNag: Removing CoreFollowUp` 配合字段与无新排期可作为支持证据；单独一条移除日志或侧边栏红点消失不是完整证明。如果另有侧边栏屏蔽工具，尤其不能只看红点。
- 记录缺失、隐私处理、日志轮转和其他用途的 Setup Assistant 进程都需要结合时间与界面判断。

可直接只读查看字段：

```sh
sudo /usr/bin/plutil -extract Disabled raw -o - \
  /private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist
```

预期输出 `true`。进一步完成以下观察：

1. 保持联网，跨过原先提醒排定的时间；不知道时点时延长正常使用观察。
2. 正常睡眠、唤醒一次，检查是否再次出现注册界面。
3. 再做一次普通重启，并重复两项只读检查。
4. 系统升级/还原后重新检查版本、字段和行为，不把几分钟无弹窗当作永久保证。

**不要用 `profiles renew -type enrollment` 验证。** 它会主动重新启动注册，不是只读查询；显式注册操作可能重新启用提醒。

## 8. 回滚到本次准备前状态

再次进入恢复模式、挂载同一 Data 卷后，使用**应用时的同一个会话**：

```sh
(
  set -eu
  DATA="/Volumes/Data"
  WORK="$DATA/Users/Shared/mdm-nag-recovery"
  KIT="$WORK/toolkit-v2.2.0"
  NAME=$(/bin/cat "$WORK/LATEST_SESSION_NAME.txt")
  case "$NAME" in
    install-*) ;;
    *) printf '%s\n' 'Invalid session name.' >&2; exit 2 ;;
  esac
  case "$NAME" in
    *[!A-Za-z0-9_-]*) printf '%s\n' 'Invalid session name.' >&2; exit 2 ;;
  esac
  SESSION="$WORK/sessions/$NAME"
  printf 'Restore Data=%s\nSession=%s\n' "$DATA" "$SESSION"
  /bin/sh "$KIT/SELF_TEST.sh"
  /bin/sh "$KIT/ROLLBACK.sh" "$DATA" "$SESSION"
)
```

如果后来准备了新会话，定位文件可能指向新会话；回滚前按自己的记录选回正确目录名，不直接套用“最近一个”。

成功为 `result=restore` 或 `result=already_restore`。回滚恢复该会话记录的原始状态，`Disabled` 可为 `absent`、`false` 或 `true`，不一定意味着重新开启提醒。源状态已变化时脚本会拒绝覆盖，不要修改 manifest 或删除哈希检查强行回滚。

写入中出现错误会尝试恢复操作前字节并验证 owner/group/mode/flags；这不等于对所有 ACL、扩展属性、断电或磁盘故障提供完整事务保证。回退失败时保留所有原备份及输出，再检查磁盘和目标状态。

### 一键入口的额外结果

`ONE_CLICK.sh` 保留底层脚本的退出状态；下载失败也会保留下载工具的错误状态。`apply` 在原生修改已经成功、但最后应用记录的保存失败时退出 **4**，输出 `result=applied_pointer_update_failed`、`native_applied=yes` 和实际 `session` 路径。此时原生修改没有被回滚，备份仍在；应保留输出，验证状态，并按指定会话回滚，而不是把它当作“没有写入”。指针路径的常规类型/权限检查在原生应用前完成。

## 9. 脚本接口与退出码

| 文件 | 作用 |
| --- | --- |
| `GET_TOOLKIT.sh /absolute/new-directory` | 下载固定 Release、解包、自检；不准备或应用。父目录需已存在，目标需不存在。 |
| `PREPARE.sh --live /absolute/new-session` | 在正常系统读取当前安装并创建新会话。 |
| `PREPARE.sh --volume /Volumes/Data /absolute/new-session` | 在恢复模式从离线 Data 卷创建新会话。 |
| `APPLY_IN_RECOVERY.sh DATA SESSION` | 在恢复模式核对目标、备份和字段差异后应用。 |
| `ROLLBACK.sh DATA SESSION` | 在恢复模式恢复指定会话的原始状态。 |
| `STATUS.sh [--volume DATA]` | 只读检查正常系统或离线卷。 |
| `VERIFY_AFTER_BOOT.sh [--since 'YYYY-MM-DD HH:MM:SS']` | 正常启动后查询原生状态及指定窗口日志；日期占位符需替换为实际本地时间。 |
| `SELF_TEST.sh` | helper 自检、shell 语法及完整文件清单校验。 |
| `sha256-file` / `src/` | 独立 SHA-256 程序及构建源码；日常使用无需编译。 |
| `tests/` / `examples/` | 合成数据的开发验证和示例；不是机器会话。 |

检查上一命令的退出码时，立即运行 `echo $?`，中间不要执行其他命令。

| 准备/应用/回滚退出码 | 含义 |
| --- | --- |
| `0` | 成功或已经处于指定会话要求的状态。 |
| `2` | 用法、环境或预检失败，未尝试写原生目标。 |
| `1` | 写入失败，自动回退已验证。 |
| `3` | 自动回退未验证，需保留输出并检查原始备份及目标。 |
| `129` / `130` / `143` | 收到 HUP / INT / TERM；结合输出判断写入及回退。回退未验证时改为 `3`。 |

- `STATUS.sh`：`0` = 字段为 `true`；`1` = `false` 或缺失；`2` = 读取、格式或工具错误。
- `VERIFY_AFTER_BOOT.sh`：`0` = 当前窗口符合检查；`1` = 字段未关闭或有待复查的日志/进程；`2` = 查询未完成。`0` 不是跨时间或跨抹盘的保证。
- `SELF_TEST.sh`：`0` = 自检通过；`2` = 自检失败。
- 下载器以 `result=toolkit_download_ok` 和退出 `0` 为成功依据；非零时不要执行不完整目录内的脚本。下载器不写原生 MDM 状态。

## 10. 网络与其他错误处理

| 情况 | 处理 |
| --- | --- |
| GitHub 网络、TLS、DNS 或下载失败 | 检查网络认证、系统时间、GitHub 可达性后重新下载；保留完整错误，不用 `-k` 或关闭证书校验。 |
| Recovery 缺少 `curl` / `tar` 等依赖 | 若正常系统仍可启动且 Data 将保留，先按下方方法预下载到 Data，恢复模式直接使用完整本地包；不跳过校验。 |
| `sha256-file` 缺失/执行失败/自检失败 | 重新获取完整发布包，确认架构及可执行卷；不改用“忽略哈希”。旧 `shasum` 对 Perl 的依赖不是此版本运行条件。 |
| 工具目录已存在 | 先运行其中的 `SELF_TEST.sh`；若需重新下载，使用新的目录名并同步后续路径，不覆盖已有包。 |
| 会话路径已存在 | 用一个新的会话目录名重新准备，保留原会话。 |
| 卷 UUID 不同、源哈希改变 | 先确认目标安装和状态变化，再为当前安装准备新会话；不修改旧 UUID 或哈希强行套用。 |
| 原生 plist 缺失 | 确认正确 Data 卷；确实缺失时保留检查结果，不创建虚构状态。 |
| 卷未挂载、未解锁或只读 | 用磁盘工具确认、解锁正确 Data 卷；磁盘异常先处理磁盘状态。 |
| `Operation not permitted` | 确认操作是在恢复模式且目标是离线 Data 卷，保留输出；不把正常系统里的 `sudo` 当作恢复模式。 |
| 自动回退已验证 | 本次应用未完成，原状态已恢复；检查原始错误后再决定下一次操作。 |
| 自动回退未验证 | 保留会话和完整输出，检查文件及磁盘；不要声称回滚成功或反复覆盖。 |
| 字段为 `true` 仍出现界面 | 记录系统版本、发生时间、字段和只读日志，区分首次设置、显式注册与登录后提醒。 |

### 可选：正常 macOS 先下载到内置 Data，无需 U 盘

适用于正常系统仍可启动，且接下来只进入恢复模式、不抹掉保存目录的情况。在正常系统先执行第 4.1 节的**下载器获取**代码，再明确运行：

```sh
sudo /bin/mkdir -p /Users/Shared/mdm-nag-recovery
sudo /bin/sh /tmp/GET_TOOLKIT-v2.2.0.sh \
  /Users/Shared/mdm-nag-recovery/toolkit-v2.2.0
```

成功后进入恢复模式，完整目录映射为 `/Volumes/Data/Users/Shared/mdm-nag-recovery/toolkit-v2.2.0`（按实际 Data 挂载点调整）。从第 5 节准备新会话继续。不要把正常系统的 `/Users/Shared/...` 在恢复模式里误认为目标安装的路径。

如果也想在正常系统先准备会话，使用 `PREPARE.sh --live` 并保存到同一持久会话父目录；之后回到恢复模式只应用该会话，不再另外准备一个混用。网络下载不会把之前抹掉的备份找回来。

## 11. 验证记录、版本更新与反馈

- 原生字段机制和旧版单次安装脚本已在 Apple Silicon 的 macOS 27.0（build `26A428`）真实恢复模式及随后正常启动中取得成功记录。该记录不等于 public 版本的网络流程已完成真实恢复模式验收。
- **v2.1.0 引入固定版本网络获取，v2.2.0 新增 `ONE_CLICK.sh` 场景入口；新入口、通用动态会话及网络流程的实际恢复环境与重启流程仍需按本手册逐项验收。** 仓库的隔离测试使用合成数据，不会修改本机原生文件，不是全面抹盘的端到端测试。
- `sha256-file` 为 arm64 / x86_64 universal Mach-O，仅依赖系统 `libSystem`。架构分片存在不等于两个架构都已完成真实恢复模式实测。
- 具体已执行的测试和结果见[验证说明](VALIDATION.md)及随包 `tests/`；示例数据不代表任何用户真实配置。
- 工具没有对未来系统版本作行为保证：结构、类型、哈希和 UUID 检查通过，只证明操作对象符合检查，不证明未来 macOS 仍按相同逻辑处理 `Disabled`。
- 以后使用新版：明确选择该版 Release，将**整包**下载到新目录，执行新包自检；系统已升级、还原或状态变化时重新准备会话。保留旧版本及其对应会话，别拼接不同版本的脚本和 helper。

[提交 Issue](https://github.com/jackylam0812/macos-depnag-toolkit/issues) 时仅提供脱敏后的系统版本、脚本版本、步骤、退出码和必要错误片段。**不要上传完整会话、原始配置文件、序列号、卷 UUID、账号信息或未经检查的完整系统日志。**
