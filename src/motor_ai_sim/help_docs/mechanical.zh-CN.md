# 仿真 > 机械

用途：转子离心应力和护套尺寸在转速下的计算、接触和安全系数，其后是振动模态、轴临界转速，以及轴承和风阻损耗。仅限有仿真界面的工程人员。Solve 必须手动按下：不会自动重新计算。

控件（顶行）：
- 只有一个转速：“rpm”取自电磁页的工况点；在 rpm 字段输入验证转速即可覆盖；清空该字段即恢复跟随电磁页。开启耦合热时，转速和温度取自该次运行并被锁定。
- 载荷菜单：离心、扭矩或两者；扭矩有自己的“T N-m”字段。磁拉力菜单：无、电磁场或均匀（带“p MPa”）。
- “interference mm”，以及“rotor C”和“sleeve C”温度。
- “Solve”（运行时显示“Solving”）和“Limit speed (SF = 1)”，后者搜索最小平均安全系数达到 1 的转速。
- 网格块：“mesh mm”、整个转子或 1 个极扇区，以及“Build mesh”（只画网格不求解；下次 Solve 复用）。
- 界面接触菜单：Separation、Bonded 或 Sliding，以及摩擦系数 µ。按下 Solve 之前不会重新计算。

结果：安全系数卡片（磁钢脱离、护套脱离、温度下的配合、转子质量等）、变形后的转子应力图和接触汇总。“Add to comparison”把结果加入比较。

下方各部分：轴承（通过 Browse 从目录中选择轴承 A 和 B、油脂或油气、预紧、轴承温度手动或来自 Thermal；给出轴承摩擦和转子风阻）、轴承配合和支架强度，以及模态分析（“Solve modes”求转子或定子模态，“Critical speeds”求轴系）。

警告：几何改变后显示“result is for a previous geometry - re-Solve”；“Loaded from history”及 Recompute。

常用顺序：先运行电磁，在此设置载荷和接触，需要时 Build mesh，Solve，然后 Limit speed，再看模态和轴承部分。
