# 螺旋桨：设计器

用叶素模型评估自己的螺旋桨。横幅“BEMT, generic airfoil polars, not validated”始终显示：结果是筛选估算。

顶行：Diameter（英寸）、Blades、Motor、Polar（平板粗略模型、带弯度、或粘性 mfoil 表）和 Evaluate（运行时显示“Evaluating”）。其下按组列出叶片参数：Root (inner blade)、Blade、Airfoil by zone 以及 Blades and diameter；每个参数有边界和“Reset to seed”。

按 Evaluate 之后：
- 卡片：推力、扭矩、轴功率、转速、叶尖马赫数、每轴瓦推力、电机处气流速度。
- 约束标签：“Constraints met”或“Constraint violated”及规则（扭矩在电机限值内、叶尖马赫数在限值内、求解器收敛），以及“Root flow not validated”、“Inputs unconfirmed”等标志。
- 图表：桨叶平面形状和扭转、沿半径的气流速度、扭矩对转速及电机限值；以及表格“Zones as manufactured”。
- 运行记录和双目标视图：每次 Evaluate 和 Sweep 都会增加一条运行记录；“Use”选用某条，“Clear runs”清空列表。

面板：
- Sweep：选择 Variable、From、To、Points，然后“Run sweep”。
- Optimiser：Population、Generations、Seed、要改变的组（Blade、Root、Airfoils）、Objectives（每瓦推力、电机处气流）、“Enforce tip Mach”及其限值，然后“Run optimiser”。它报告设计数量和前沿。
- Drafts：输入名称，Save、Open 或 Delete 已保存的草稿。
- Stored optimisation runs：保存在工作区的运行记录，可按 Blades 筛选；打开一条可查看收敛过程，Refresh，以及“Load best into the Designer”。

无效的设计会显示“The design is not valid”及违反的规则。
