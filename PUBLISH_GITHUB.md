# GitHub 发布步骤

建议仓库名：

- `guanlan-quant`

建议仓库描述：

- `A VNPY-based quant desktop platform with an AllTick-powered A-share realtime scanner and web dashboard.`

建议 Topics：

- `quant`
- `trading`
- `vnpy`
- `alltick`
- `python`
- `pyside6`
- `flask`
- `systemd`
- `a-share`

## 1. 进入仓库目录

```bash
cd /home/jzy/桌面/guanlan-quant
```

## 2. 检查要发布的文件

```bash
git status
```

## 3. 提交首个版本

```bash
git add .
git commit -m "Initial public release"
```

## 4. 在 GitHub 新建空仓库

仓库地址建议使用：

```text
https://github.com/Jiangzy520/guanlan-quant
```

## 5. 关联远程并推送

```bash
git remote add origin https://github.com/Jiangzy520/guanlan-quant.git
git push -u origin main
```

## 6. 推送后建议立刻补齐的 GitHub 页面信息

- `About` 描述
- `Website`（如果你后面想挂演示站）
- `Topics`
- 置顶到个人主页
- 发布一个 `v0.1.0` Release

## 7. 首个 Release 文案建议

```text
v0.1.0

- Initial public release of Guanlan Quant
- Desktop platform based on VNPY 4.3
- Included AllTick-powered realtime A-share scanner
- Included Flask web dashboard and Linux systemd examples
- Removed private runtime data, API tokens and deployment secrets
```
