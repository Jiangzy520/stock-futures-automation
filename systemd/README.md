# systemd 示例说明

这几个 unit 文件是示例配置，默认按下面的目录结构编写：

- 项目目录：`/opt/guanlan-quant`
- 运行用户：`quant`
- 运行日志：`/opt/guanlan-quant/.guanlan/runtime/`

使用前至少要检查并按你的环境修改这些字段：

- `User`
- `Group`
- `WorkingDirectory`
- `Environment=HOME=...`
- `ExecStartPre`
- `ExecStart`
- `ExecStopPost`

建议安装步骤：

```bash
sudo cp systemd/quant-scan.service /etc/systemd/system/
sudo cp systemd/quant-scan-market-open.service /etc/systemd/system/
sudo cp systemd/quant-scan-market-open.timer /etc/systemd/system/
sudo cp systemd/quant-scan-market-close.service /etc/systemd/system/
sudo cp systemd/quant-scan-market-close.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now quant-scan-market-open.timer
sudo systemctl enable --now quant-scan-market-close.timer
```

默认行为：

- `quant-scan-market-open.timer`：周一到周五 `09:20:00` 启动扫描
- `quant-scan-market-close.timer`：周一到周五 `15:05:00` 停止扫描

如果你需要严格按中国股市交易日启动，请结合 `tools/scan_service_scheduler.py` 的交易日判断逻辑一起使用。
