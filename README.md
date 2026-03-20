# Bridge Console

一个本地控制台，用于管理 Claude-to-IM、QQ、飞书以及 Happy Codex 的工作目录与日志查看。

## 仓库结构

本仓库的源码位于：

```
bridge-console-source/
```

更完整的使用说明请参阅：

```
bridge-console-source/README.md
```

## 快速开始

### Web 版本

```powershell
cd bridge-console-source
npm start
```

访问：

```
http://127.0.0.1:3210
```

### 桌面客户端（Qt，推荐）

```powershell
cd bridge-console-source
python bridge_console_qt.py
```

或双击：

```
bridge-console-source/start-bridge-console-client.bat
```

## 本地配置

复制示例配置并按需修改（本地配置文件已在 .gitignore 中忽略，不会提交到仓库）：

```powershell
cd bridge-console-source
copy bridge-console.config.example.json bridge-console.local.json
```
