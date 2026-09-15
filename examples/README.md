# 合成测试样例

本目录的数据由测试程序生成，不是任何设备的备份。

- `ORIGINAL_FILE.plist`：`Disabled` 缺失（默认 false）。
- `MODIFIED_FILE.plist`：仅添加布尔字段 `Disabled=true`，其余数据不变。
- `DIFF_FILE.diff`：上述两个文件的规范 XML 差异。

样例没有会话 manifest，不可用于实际应用。实际使用请运行 `PREPARE.sh`，为目标安装新建本地会话。不要把真实 session、设备 UUID 或系统日志提交到公开仓库。
