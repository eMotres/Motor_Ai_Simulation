# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Short human-readable labels (EN + zh-CN) of the CAD-only construction parameters: "Part - what it is".

The Geometry Full table shows the label first and the technical template name small and grey under it.  One line per
parameter of ``cad_params.CATALOGUE`` (a test pins that every name has a label in both languages and that the label starts with a
part name from the part tree vocabulary).  Format: ``name|English label|zh-CN label``.
"""
from __future__ import annotations

_LABELS = """
shaft_bore_tip_angle|Shaft - bore tip angle|轴 - 轴孔端部锥角
shaft_chamfer|Shaft - chamfer|轴 - 倒角
shaft_flange_step|Shaft - flange step|轴 - 法兰台阶
shaft_flat_len|Shaft - drive flat length|轴 - 驱动平面长度
shaft_flat_w|Shaft - width across flats|轴 - 平面间宽度
shaft_front_overhang|Shaft - front overhang|轴 - 前端伸出长度
shaft_shoulder_len|Shaft - shoulder length|轴 - 轴肩长度
shaft_shoulder_r|Shaft - shoulder radius|轴 - 轴肩半径
shaft_thread_len|Shaft - thread length|轴 - 螺纹段长度
shaft_thread_r|Shaft - thread radius|轴 - 螺纹段半径
shaft_tip_len|Shaft - tip length|轴 - 轴端长度
shaft_tip_r|Shaft - tip radius|轴 - 轴端半径
carrier_bore_r|Rotor carrier - hub bore radius|转子支架 - 轮毂孔半径
carrier_flange_t|Rotor carrier - flange thickness|转子支架 - 法兰厚度
carrier_floor_wall|Rotor carrier - hub wall|转子支架 - 轮毂壁厚
carrier_hole_count|Rotor carrier - hub hole count|转子支架 - 轮毂孔数量
carrier_hole_d|Rotor carrier - hub hole diameter|转子支架 - 轮毂孔直径
carrier_hole_pcd_r|Rotor carrier - hub hole circle radius|转子支架 - 轮毂孔分布圆半径
carrier_rim_h|Rotor carrier - rim height|转子支架 - 轮缘高度
carrier_rim_wall|Rotor carrier - rim wall|转子支架 - 轮缘壁厚
carrier_tab_cap_r|Rotor carrier - key tab cap radius|转子支架 - 键耳顶部圆角半径
carrier_tab_fillet_r|Rotor carrier - key tab flank radius|转子支架 - 键耳侧面圆角半径
carrier_web_t|Rotor carrier - web thickness|转子支架 - 腹板厚度
rotor_key_count|Rotor - key count|转子 - 键数量
rotor_key_w|Rotor - key width|转子 - 键宽度
rotor_key_h|Rotor - key depth|转子 - 键深度
rotor_key_offset|Rotor - key clearance|转子 - 键间隙
magnet_lower_fill_r|Magnet - bottom fillet radius|磁钢 - 底部圆角半径
offset_rotor|Magnet pocket - insertion clearance|磁钢槽 - 装入间隙
coil_lead_ext|Coil - lead extension|线圈 - 引线延伸长度
coil_lead_len|Coil - lead stub length|线圈 - 引线长度
holder_flange_t|Stator holder - flange thickness|定子支架 - 法兰厚度
holder_lip_t|Stator holder - bearing lip thickness|定子支架 - 轴承唇边厚度
holder_lip_r|Stator holder - bearing lip radius|定子支架 - 轴承唇边半径
holder_hub_wall|Stator holder - hub wall|定子支架 - 轮毂壁厚
holder_bolt_r|Stator holder - bolt circle radius|定子支架 - 螺钉分布圆半径
holder_window_r|Stator holder - window fillet radius|定子支架 - 窗口圆角半径
holder_seat_depth|Stator holder - seat depth|定子支架 - 凸台嵌入深度
holder_chamfer|Stator holder - seat chamfer|定子支架 - 凸台倒角
holder_tap_d|Stator holder - tap hole diameter|定子支架 - 螺纹孔直径
holder_rib_h|Stator holder - rib height|定子支架 - 筋高度
holder_rib_half_w|Stator holder - rib half width|定子支架 - 筋半宽
back_holder_len|Back holder - cylinder length|后支架 - 圆筒长度
back_holder_ref_r|Back holder - reference radius|后支架 - 构造半径
back_holder_fillet_r|Back holder - spoke end fillet|后支架 - 辐条端部圆角
back_screw_r|Back holder - M3 hole circle radius|后支架 - M3孔分布半径
back_screw_tap_d|Back holder - M3 hole diameter|后支架 - M3孔直径
coil_holder_clearance|Stator holder - coil clearance|定子支架 - 线圈间隙
bolt_head_d|Stator holder - boss head diameter|定子支架 - 凸台头部直径
ring_h|Stator holders - offset from the stator face|定子支架 - 距定子端面偏移
ring_holder_gap|Stator holders - extra gap to the flange|定子支架 - 法兰附加间隙
prop_fix_d|Propeller plate - diameter|螺旋桨板 - 直径
prop_fix_relief_r|Propeller plate - corner relief radius|螺旋桨板 - 拐角卸荷半径
prop_fix_hub_t|Propeller plate - hub thickness|螺旋桨板 - 轮毂厚度
prop_fix_plate_t|Propeller plate - plate thickness|螺旋桨板 - 板厚度
prop_fix_clearance|Propeller plate - hole clearance|螺旋桨板 - 孔间隙
bearing_ball_count|Bearing - ball count|轴承 - 钢球数量
bearing_ball_d|Bearing - ball diameter|轴承 - 钢球直径
bearing_edge_r|Bearing - ring edge radius|轴承 - 圈边缘圆角
bearing_inner_land_d|Bearing - inner land diameter|轴承 - 内圈挡边直径
bearing_outer_land_d|Bearing - outer land diameter|轴承 - 外圈挡边直径
bearing_seal_cone_dz|Bearing seal - cone length|轴承密封 - 锥面长度
bearing_seal_groove_r|Bearing seal - lip groove radius|轴承密封 - 唇槽半径
bearing_seal_notch_r|Bearing seal - notch bore radius|轴承密封 - 密封槽孔半径
bearing_seal_notch_t|Bearing seal - notch depth|轴承密封 - 密封槽深度
bearing_seal_ri|Bearing seal - inner radius|轴承密封 - 内半径
bearing_seal_rm|Bearing seal - lip junction radius|轴承密封 - 唇边交界半径
bearing_seal_ro|Bearing seal - outer radius|轴承密封 - 外半径
bearing_seal_t|Bearing seal - flange thickness|轴承密封 - 法兰厚度
bearing_seal_z0|Bearing seal - axial start|轴承密封 - 轴向起点
bearing_seal_z1|Bearing seal - axial end|轴承密封 - 轴向终点
board_inner_r|Phase board - inner radius|相位板 - 内半径
board_t|Phase board - thickness|相位板 - 厚度
board_gap|Phase board - gap to lead ends|相位板 - 与引线末端间隙
board_pin_clear|Phase board - lead sleeve clearance|相位板 - 引线套筒间隙
phase_board_n_offset|Phase board - N-side height|相位板 - N侧高度
phase_board_n_z0|Phase board - STEP axial offset|相位板 - STEP轴向偏移
phase_board_x|Phase board - STEP offset X|相位板 - STEP偏移X
phase_board_y|Phase board - STEP offset Y|相位板 - STEP偏移Y
holder_bolt_count|Screws - holder screw count|螺钉 - 支架螺钉数量
bolt_d|Screws - holder screw diameter|螺钉 - 支架螺钉直径
screw_head_d|Screws - head diameter|螺钉 - 头部直径
screw_head_h|Screws - head height|螺钉 - 头部高度
screw_head_fillet|Screws - head edge radius|螺钉 - 头部边缘圆角
screw_shank_d|Screws - shank diameter|螺钉 - 杆部直径
screw_socket_af|Screws - socket width across flats|螺钉 - 内六角对边宽度
screw_socket_depth|Screws - socket depth|螺钉 - 内六角深度
screw_socket_point|Screws - drill point angle|螺钉 - 钻尖夹角
screw_tip_chamfer|Screws - tip chamfer|螺钉 - 尖端倒角
"""


def labels() -> dict:
    out = {}
    for line in _LABELS.strip().splitlines():
        name, en, zh = line.split("|")
        out[name] = {"en": en, "zh": zh}
    return out
