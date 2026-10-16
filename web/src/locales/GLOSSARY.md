# EN ↔ ZH (Simplified) glossary

This is the single terminology source for `locales/zh-CN/*.json`. When a term
here appears in a string, the translation uses the Chinese term from this table.
A new term is added here first, then used in the JSON. Reviewers check new
strings against this table.

**Never translated:** unit symbols (`N·m`, `kW`, `rpm`, `A rms`, `°C`, `L/min`,
`W/m²K`, `µH`, `nH`, `Ω`, `K/W`, `%`), physical-quantity symbols (`T_j`, `Br`,
`HcJ`, `B`, `J`, `R`, `L_d`, `L_q`, `η`, `γ`, `δ`, `cos φ`, `m`), and part
numbers, product names, `SVPWM`, `THD`, `SPICE`, `PWM`, `MOSFET`, `SiC`, `YAML`.
When a label pairs a word with a symbol, the symbol stays and the word is
translated (`Phase current` → `相电流`, `R phase` → `相电阻 R`).

## Machine and electromagnetics

| EN | ZH | Note |
|---|---|---|
| motor | 电机 / 电动 (mode) | "Motor" as an operating mode is 电动 |
| generator | 发电 (mode), 发电机 (machine) | |
| stator | 定子 | |
| rotor | 转子 | |
| rotor bore | 转子内孔 | |
| shaft | 转轴 / 轴 | |
| air gap | 气隙 | |
| lamination, die (stamped lamination) | 叠片, 冲片 | "die" in the catalog = 冲片 |
| stack (length) | 叠厚 | |
| slot | 槽 | |
| slot fill factor | 槽满率 | |
| pole / pole pairs | 极 / 极对数 | |
| pole arc ratio | 极弧系数 | |
| magnet | 磁钢 | 永磁体 in formal prose |
| sleeve (retaining) | 护套 | |
| winding | 绕组 | |
| coil | 线圈 | |
| turns | 匝数 | |
| end turns / end winding | 端部绕组 | |
| strand, stranded | 股线, 绞线 | |
| flat wire | 扁线 | |
| star / delta (Y / Δ) | 星形 / 三角形 | |
| back-EMF | 反电动势 | |
| cogging torque | 齿槽转矩 | |
| torque ripple | 转矩脉动 | |
| torque density / power density | 转矩密度 / 功率密度 | |
| flux linkage | 磁链 | |
| demagnetisation | 退磁 | |
| remanence Br, coercivity HcJ | 剩磁 Br, 内禀矫顽力 HcJ | |
| saturation | 饱和 | |
| operating point | 工作点 | |
| duty (operating case) | 工况 | a saved operating case |
| duty cycle (S1/S2/S3) | 工作制 | ED = 负载持续率 |
| continuous rating | 连续额定 | |
| configuration | 配置 | |
| efficiency | 效率 | |
| wall-to-shaft efficiency | 电网到轴端效率 | |
| inertia | 转动惯量 | |
| speed | 转速 | |
| current angle γ | 电流角 γ | |

## Losses and thermal

| EN | ZH |
|---|---|
| losses | 损耗 |
| copper loss | 铜损 |
| iron loss / core loss | 铁损 |
| eddy-current loss | 涡流损耗 |
| magnet (eddy) loss | 磁钢（涡流）损耗 |
| windage | 风摩损耗 |
| bearings | 轴承 |
| hot-spot | 热点 |
| junction temperature | 结温 |
| coolant, inlet, flow | 冷却液, 进口温度, 流量 |
| coldplate | 冷板 |
| heatsink | 散热器 |
| forced air / still air | 强迫风冷 / 自然风冷 |
| ambient | 环境温度 |
| housing / frame | 机壳 |
| convection coefficient h | 对流换热系数 h |
| thermal resistance | 热阻 |
| adiabatic | 绝热 |
| emissivity | 发射率 |
| fin efficiency | 肋片效率 |
| steady state | 稳态 |
| coupled EM ↔ thermal | 电磁 ↔ 热耦合 |

## Power electronics (Controller)

| EN | ZH |
|---|---|
| inverter | 逆变器 |
| controller | 控制器 |
| active rectifier | 有源整流 |
| bridge / leg / H-bridge | 桥 / 桥臂 / H 桥 |
| power device | 功率器件 |
| switching loss / conduction loss | 开关损耗 / 导通损耗 |
| dead time | 死区时间 |
| drive (Sine / PWM menu) | 驱动 |
| drive variant (device at a carrier) | 驱动方案 |
| passport (computed motor record) | 性能档案 |
| pack (battery) | 电池组 |
| envelope (voltage / computed range) | 包络 / 已计算范围 |
| inverter device (MOSFET) | 逆变器器件 |
| carrier (frequency) | 载波（频率） |
| modulation index m | 调制比 m |
| sine-triangle PWM / third-harmonic injection | 正弦-三角波 / 三次谐波注入 |
| unipolar / bipolar | 单极性 / 双极性 |
| DC link, DC-link capacitor | 直流母线, 直流母线电容 |
| ripple | 纹波 (current/voltage), 脉动 (torque) |
| gate resistance / gate voltage | 栅极电阻 / 栅压 |
| body diode | 体二极管 |
| datasheet | 数据手册 |
| displacement power factor | 位移功率因数 |
| neutral (point) | 中性点 |
| dual three-phase | 双三相 |
| margin / rating / verdict | 裕量 / 额定值 / 结论 |

## Numerics and app

| EN | ZH |
|---|---|
| mesh | 网格 |
| solve / run | 求解 / 运行（计算） |
| FEM | 有限元 |
| steps per period | 每周期步数 |
| geometry | 几何 |
| materials | 材料 |
| optimization | 优化 |
| catalog / catalogue | 目录 (motors), 器件库 (devices) |
| compare | 对比 |
| configure | 配置 |
| sign in / sign out | 登录 / 退出登录 |
| session | 会话 |
| agent (AI) | 智能体 |

## Help assistant

| EN | ZH |
|---|---|
| Help & feedback | 帮助与反馈 |
| assistant | 助手 |
| ticket | 工单 |
| My tickets | 我的工单 |
| bug | 缺陷 |
| feature request | 功能需求 |
| account (ticket type) | 账号 |
| question (ticket type) | 问题 |
| Send to the team | 发送给团队 |
| open / in progress / resolved / closed | 待处理 / 处理中 / 已解决 / 已关闭 |
