# macOS DEPNag Toolkit

只处理登录 macOS 后的注册提醒；首次设置中的“远程管理”不是本包已验证的流程。

## 先下载入口

**每次换环境或重启后，先执行这一段。下载无报错，再选下面一个操作。**

```sh
curl -fL --proto '=https' --proto-redir '=https' \
  -o /tmp/ONE_CLICK-v2.2.0.sh.part \
  https://raw.githubusercontent.com/jackylam0812/macos-depnag-toolkit/v2.2.0/ONE_CLICK.sh &&
mv /tmp/ONE_CLICK-v2.2.0.sh.part /tmp/ONE_CLICK-v2.2.0.sh &&
/bin/sh -n /tmp/ONE_CLICK-v2.2.0.sh
```

## 1. 正常系统：提前下载工具（可跳过）

```sh
sudo /bin/sh /tmp/ONE_CLICK-v2.2.0.sh prepare
```

## 2. 恢复模式：关闭提醒

先联网，必要时在“磁盘工具”装载、解锁 Data 卷，再运行：

```sh
/bin/sh /tmp/ONE_CLICK-v2.2.0.sh apply
```

多卷时明确指定实际挂载点，例如：

```sh
/bin/sh /tmp/ONE_CLICK-v2.2.0.sh apply /Volumes/Data
```

看到 `result=one_click_applied` 或 `result=already_disabled` 后，正常重启。

## 3. 正常重启后：验证

```sh
sudo /bin/sh /tmp/ONE_CLICK-v2.2.0.sh check
```

## 4. 恢复模式：撤销上次修改

```sh
/bin/sh /tmp/ONE_CLICK-v2.2.0.sh rollback
```

多卷时加上实际挂载点，例如 `rollback /Volumes/Data`。

[完整说明与报错处理](docs/ADVANCED.md) · [v2.2.0 发布包](https://github.com/jackylam0812/macos-depnag-toolkit/releases/tag/v2.2.0)
