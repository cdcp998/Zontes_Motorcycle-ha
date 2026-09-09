# 升仕 4510 协议逆向：踩坑与破局实战手册

> 性质：团队内部技术沉淀 · 完全脱敏（无真实账号/车辆/会话数据）
> 适用：当官方 App 升级/换钥导致 HA 集成"远程控车失效"时，按本文档快速定位与恢复。

---

## 1. 核心破局秘诀（The Golden Key）

### 1.1 双 RSA 密钥架构（协议的本质）

升仕 4510 远程控制通道（TCP 61.145.9.116:4510，自定义文本帧，以 `#` 分隔）在**加密层**上使用**两把不同的 RSA-2048 公钥**：

| 帧类型 | 加密公钥 | 说明 |
|---|---|---|
| 登录握手 `*UL,...` | **Key-A** | 客户端把整帧明文 RSA(PKCS#1 v1.5) 加密为 256B 后发出 |
| 控车指令 `*UClear` / `*ULoc` | **Key-K1** | 指令/保活帧必须用 K1 加密，与登录帧不同 |

服务端相应地持两把不同的私钥分别解密登录与指令。因此：

- **登录总是能成功**：任何能用 Key-A 构造合法登录帧的客户端都会收到 `*UL,...,OK#` 与 `*BR,1#` 回显（登录校验宽松/仅验证帧可解）；
- **指令帧若误用 Key-A 加密，服务端指令私钥解密失败 → 静默丢弃**：无任何报错、无回显、无 FAIL。这正是"登录 OK、指令零回显"长期悬案的根因。

### 1.2 此前长期失败的真正原因（教训）

排查曾把大量精力投入：哈希算法（验证 8/8 帧一致）、帧字段顺序、`#` 有无、大小写、时间戳精度、seq 格式、心跳有无、双连接、长/短连接、网络出口、TCP 栈特征、LSPosed/Frida…… 全部无关。

决定性实验（同明文、不同密文）：
- 回放 **App 原始指令密文** → 服务端回 `OK#` + 设备 `*AM`；
- 用 **Key-A 重新加密同一明文** → 服务端静默。

教科书 RSA 下同明文不同密文理应同判定，实测不同 ⇒ 服务端对指令帧使用的是**另一把私钥**——由此锁定双钥架构。

### 1.3 真车硬件执行的唯一黄金凭据

- 服务端 `*,OK#` 回显：仅代表"报文解密通过、已接受/转发"，**不代表车辆已动作**；
- 车辆 T-Box 确认帧：
  - `*AM,1,1#` = **上锁（设防）执行到位**；
  - `*AM,2,1#` = **解锁执行到位**；
  - 末位为 `0`（如 `*AM,1,0#`）= 指令被车辆拒绝（过期/重复/状态冲突），**未动作**。
- 因此 HA 锁实体应以"服务端回显 OK# + `*AM`/轮询 `lock` 状态"联合确认；仅凭回显 OK# 时仍需 REST 轮询兜底。

---

## 2. 全流程踩坑与避坑实录（Troubleshooting）

### 坑 ①：Frida 反调试拦截（爱加密 iJiami / SecShell）

- 官方 App（含第三方《骑仕》）均集成加固壳（爱加密/SecShell），常规 `frida-server` attach / spawn 会在 1 秒内被杀：
  - 现象：attach 后进程秒退、脚本 `Java is not defined`、create_script 超时；
  - 壳的检测点：ptrace（TracerPid）、frida-server 端口、maps 特征。
- 结论：**不要在本项目里继续依赖 Frida 做进程内 Hook**。

### 坑 ②：LSPosed 本地构建壁垒

- LSPosed 模块需打包为 APK（XposedBridge API + Gradle + Android SDK）；
- 无 Android SDK / Gradle 的机器上寸步难行；且官方 LSPosed 1.9.2 对高版本 Android 兼容有限。
- 结论：需要"进程内代码注入"时，优先评估收益/成本；本项目最终**未依赖任何 Hook 框架**。

### 坑 ③：终极突破方案——root 直接读进程内存（免 Hook）

绕开一切壳内反调试的最短路径：

1. 打开官方 App 并进入车辆页（让真实密钥载入内存）；
2. Root 下直接读 `/proc/<PID>/mem` 导出堆内存（region space 常见区间 `0x02000000-0x42000000`）：
   ```bash
   adb shell su -c "dd if=/proc/<PID>/mem of=/data/local/tmp/rspace.bin bs=4096 skip=8192 count=262144 conv=noerror,sync"
   adb pull /data/local/tmp/rspace.bin ./rspace.bin
   ```
   （1GB 约 1.5 秒；`conv=noerror,sync` 自动跳过空洞页。）
3. 用 `tools/zontes_rsa_key_scan.py` 扫描 RSA-2048 SPKI/PKCS1 块：
   - 命中"引用次数最高"（实测 180 次）且既非 A 也非 K1 的公钥 = **新轮换的指令钥**；
4. 提取 DER-Base64 后经 `ZONTES_4510_CMD_KEY` 环境变量热替换，无需改代码。

关键点：读取 `/proc/<PID>/mem` 属于**进程外系统级读取**，不触发壳的进程内 ptrace 自检。

### 坑 ④：Android WiFi 代理缓存回写陷阱

- 抓包工具（Reqable 等）在 WiFi 每网络设置里残留静态代理；重启后系统"证书不受信任"、App 登录失败；
- 只改全局 `http_proxy` 无效：**每网络代理（WifiConfigStore.xml）优先**；
- 直接 sed 该 XML 也会被系统框架在关 WiFi/重启时**从内存回写覆盖**；
- 最终解法：关 WiFi → sed 清理 `ProxySettings>STATIC` 与主机/端口行 → `chattr +i`（文件不可变锁）→ 重启 → 验证代理为空 → `chattr -i` 解锁。
- 恢复抓包时需重新设代理并**重装对应 CA**。

---

## 3. 面向未来的应急操作指南（SOP：密钥轮换恢复）

当集成控车再次出现"登录 OK / 指令静默"（且帧校验工具输出 MATCH）时，按序执行：

1. 平板打开官方 App，登录并进入车辆主页（保持前台）；
2. 取得 App PID，按"坑 ③"导出 region space 堆转储并拉回 PC；
3. 运行：
   ```bash
   python tools/zontes_rsa_key_scan.py ./rspace.bin --all
   ```
   挑出标注 `NEW (rotate?)` 且引用次数最高的公钥；
4. 用 `tools/zontes_4510_client.py` 预检（环境变量注入测试账号/车辆/新钥）：
   ```bash
   export ZONTES_USER_CODE=... ZONTES_PKE=... ZONTES_MCUID=...
   export ZONTES_4510_CMD_KEY="<新 DER-Base64>"
   python tools/zontes_4510_client.py unlock   # 或 lock
   ```
   期望：`成功: 服务器回显接受`，随后 REST 轮询 `lock` 翻转；
5. HA 侧无需改代码：在 HA 环境注入 `ZONTES_4510_CMD_KEY`（或更新 `api.py` 中 `CONTROL_CMD_RSA_PUBLIC_KEY` 默认值）后重启集成；
6. 若 A 钥也轮换（登录即失败），同步更新 `CONTROL_RSA_PUBLIC_KEY` / 客户端 `RSA_PUBLIC_KEY_B64`。

### 其他排查速查

| 症状 | 优先检查 |
|---|---|
| 登录都无回显 | 网络/防火墙 → A 钥是否轮换 |
| 登录 OK + 指令静默 | **指令钥是否仍为 K1**（90% 是换钥）→ 走 SOP |
| 指令 OK# 但无 `*AM` 且状态不变 | 车辆离线 / 指令被设备拒（轮询 lock/changeTime） |
| `*AM,x,0#` | 车辆拒绝执行（过期/重复），核对时间戳与状态 |

---

## 4. 附：脱敏说明

- 本文档不含任何真实手机号、userCode、车辆码、mcuid、设备 GUID、令牌、密码；
- 公钥 DER/Base64（Key-A / Key-K1）为官方 App 公开内嵌的公钥，予以保留（无会话上下文）；
- 回归测试使用"虚构账号 + 冻结摘要"（见 `test_4510_qishi_vectors.py` 头注释），真实抓包向量存于私有不入库目录。
