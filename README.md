# Google One 套餐注入器 (`gemini-pro-frida`)

在已 root 的 Android 设备/模拟器上注入 Google One，按套餐弹出 Play 收银台。  
**不会自动点订阅**，确认页留给你自己点。

你只需要准备：**Root（`su` 或 `adb root`）+ 已登录 Google + 已安装 Google One**。  
`frida-server`、`module_521.apk`、adb 连接由 `main.py` 自动补齐。

## 运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

常用 CLI 参数：

```text
python main.py --plan pro_12m                        # 直接新购并等待支付后自动移除账号
python main.py --plan pro_12m --loop                 # 按顺序循环处理设备上全部 Google 账号
python main.py --plan pro_12m --no-auto-remove       # 仅弹出收银台，不等待/不删号
python main.py --renew --plan pro_6m                 # 续费叠加（STACK=true）
python main.py --check-offers                        # 检查所有套餐 Offer 可用性与价格（不弹窗）
python main.py --plan pro_12m --log-file audit.jsonl # 输出结构化 JSONL 审计日志
python main.py --remove-accounts                     # 立即移除当前设备上的所有 Google 账号
python main.py --serial emulator-5554                # 指定设备（一般不用）
```

## 菜单

套餐定义在 `plans.json`，按 **Plus / Pro / Ultra 5x / Ultra 20x** 分组。

| 输入 | 作用 |
|------|------|
| 编号或套餐名 | 下单，弹出收银台 |
| `s` | 切换续费叠加（`STACK`） |
| `a` | 附加 YouTube 套餐 |
| `r` | 切换支付成功后自动移除 Google 账号 |
| `p` | 循环处理设备上所有 Google 账号（已订阅直接删号跳下一个） |
| `o` | 检查所有套餐 Offer 可用性与价格 |
| `x` | 立即移除当前设备上的 Google 账号 |
| `e` | 再跑一遍环境检测 |
| `g` | 重启 Google One 并重新注入 |
| `q` | 退出 |

## 新购和续费

`frida.js`：

```js
var STACK = false; // 新购
var STACK = true;  // 续费：叠到现有 Google One 订阅上（ReplacementMode = 5）
```

默认新购。菜单按 `s`，或命令行加 `--renew`。续费要求当前账号已经有一笔可叠加的订阅。

## 脚本会自动做的事

1. 查找 adb（PATH / Android SDK / 各盘符下的 MuMu 安装目录）
2. 没有设备时重启 adb，并尝试连接 MuMu / 雷电 / 夜神常用端口
3. 多设备去重，优先选已 root、已装 Google One、已登录的那台
4. 兼容传统 `su` 与 Android 15 模拟器（如 MuMu 6.0 / `adb root`）
5. 本机没有 Frida 则 `pip install frida-tools`
6. 缺少 `/data/local/tmp/module_521.apk` 时从 `module_b64.py` 解开并 push
7. 设备 `frida-server` 缺失或版本和本机不一致时，从 `assets/` 或 GitHub 补齐并启动
8. 支持在支付成功回调（`purchase_cb code=0`）后通过 `AccountManagerService` 自动移除该 Google 账号

## 文件

| 文件 | 说明 |
|------|------|
| `main.py` | 入口：环境、菜单、注入、循环与自动删号 |
| `frida.js` | Frida 插件，含 BillingClient 与 Offer 审计逻辑 |
| `plans.json` | 套餐分组与 SKU/Offer 配置 |
| `module_b64.py` | BillingClient 5.2.1 dex |
| `tests/test_main.py` | 单元测试 |
| `requirements.txt` | Python 依赖 |
| `assets/` | 本地缓存的 `frida-server`（已忽略，不提交到 Git） |

## 测试

```powershell
python -m unittest discover -s tests -v
```
