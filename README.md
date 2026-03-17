# Zontes Smart Motorcycle Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

HomeAssistant自定义集成，用于接入升仕（Zontes）智能摩托车数据。通过官方App接口获取车辆实时状态，支持多车辆自动轮询、数值转换及国际化显示。

---

## ✨ 功能特性
- **纯AI写的**
- **多车支持**：自动轮询账户下所有摩托车，每辆车创建独立设备。
- **实时数据**：获取油量、胎压、续航、速度、电压、水温、里程、保养里程、GSM信号、卫星数、骑行时间、故障码、锁状态、车型等信息。
- **数值修正**：
  - 胎压原始值自动 ×2 得到 kPa
  - 里程（总里程/小计里程）、电压、平均油耗原始值 ÷10 显示
  - 骑行时间（`RideTimes`）分钟转小时显示
- **国际化**：支持中文、英文界面（实体名称自动翻译）。
- **设备跟踪**：通过 `device_tracker` 实体在地图上显示车辆位置。

---

## 📦 安装

### 通过 HACS 安装（推荐）
1. 确保已安装 [HACS](https://hacs.xyz/)。
2. 在 HACS 中点击“集成”，选择“自定义存储库”。
3. 添加仓库 URL：`https://github.com/cdcp998/Zontes_Motorcycle-ha`，类别选择“集成”。
4. 点击“下载”按钮，下载完成后重启 Home Assistant。

### 手动安装
1. 下载 [最新发布版本](https://github.com/cdcp998/Zontes_Motorcycle-ha/releases/latest) 的 `Zontes_Motorcycle-ha.zip`。
2. 解压到 Home Assistant 配置目录的 `custom_components/Zontes_Motorcycle-ha` 文件夹中。
3. 重启 Home Assistant。

---

## ⚙️ 配置

1. 进入 **配置 → 设备与服务**，点击“添加集成”。
2. 搜索并选择 **Zontes Smart Motorcycle**。
3. 输入您的升仕账号、密码，以及更新间隔（默认30秒）。
4. 点击提交，集成将自动获取您的车辆列表并创建设备和实体。

---

## 🧩 实体说明

每辆车会生成以下实体（前缀为 `sensor.车型名称_`）：

| 实体键 | 描述 | 单位 | 转换 |
|--------|------|------|------|
| `oil` | 油量 | % | 无 |
| `pressure_front` | 前轮胎压 | kPa | 原始值 ×2 |
| `pressure_rear` | 后轮胎压 | kPa | 原始值 ×2 |
| `range` | 续航里程 | km | 无 |
| `speed` | 速度 | km/h | 无 |
| `voltage` | 电池电压 | V | 原始值 ÷10 |
| `water_temp` | 水温 | °C | 无 |
| `odo_mileages` | 总里程 | km | 原始值 ÷10 |
| `trip_mileage` | 小计里程 | km | 原始值 ÷10 |
| `maintenance_mileage` | 保养剩余里程 | km | 无 |
| `gsm_rssi` | GSM信号强度 | dBm | 无 |
| `satellite_num` | 卫星数量 | 个 | 无 |
| `ride_times` | 累计骑行时间 | h | 原始值 ÷60 |
| `fault_code` | 故障码 | - | 无故障时显示“没有故障” |
| `trip_max_speed` | 最高速度 | km/h | 无 |
| `oil_ause` | 平均油耗 | L/100km | 原始值 ÷10 |
| `vehicle_model` | 车型 | - | 无 |

此外，每辆车还包含一个**二进制传感器** `lock` 表示锁状态（开锁/关锁），以及一个**设备跟踪器** `location` 显示GPS位置。

---

## 🗂️ 文件结构

```
custom_components/zontes/
├── __init__.py          # 集成入口
├── manifest.json        # 元数据
├── config_flow.py       # 配置流
├── const.py             # 常量
├── api.py               # API客户端
├── sensor.py            # 传感器实体
├── binary_sensor.py     # 二进制传感器
├── device_tracker.py    # 设备跟踪器
└── translations/        # 国际化文件
    ├── en.json
    └── zh-Hans.json
```

---

## ⚠️ 注意事项

- 首次添加集成时需要输入正确的账号密码，若账号未注册或密码错误会给出明确提示。
- 如果车辆具有 `StartTime` / `EndTime` 授权时间，超出时间后对应实体将自动变为不可用。
- 更新间隔不宜过短，建议至少60秒，避免被服务器限流。
- 若某辆车切换失败（例如“没有该授权车辆信息”），集成会跳过该车，其他车辆不受影响。

---

## 🔄 更新日志
### V0.1.1 (2026-03-18)
- ✨ 添加：修改扫描间隔选项
- 🐛 修复：domain错误
### v0.1.0 (2026-03-17)
- ✨ 新增：多车辆自动轮询支持
- ✨ 新增：授权时间管理，过期车辆自动移除
- ✨ 新增：骑行时间（RideTimes）分钟转小时显示
- ✨ 新增：故障码无故障时显示友好文本（支持国际化）
- ✨ 新增：设备信息显示车型、固件版本
- 🐛 修复：胎压、里程、电压、油耗数值转换错误
- 🐛 修复：空字符串导致传感器崩溃的问题
- 🌐 国际化：支持中文/英文实体名称
---

## 🤝 贡献

欢迎提交 Issue 或 Pull Request。  
项目地址：[https://github.com/cdcp998/Zontes_Motorcycle-ha](https://github.com/cdcp998/Zontes_Motorcycle-ha)

---

## 📄 许可证

GPL-3.0 license
