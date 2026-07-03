"""
Demo intersection definition.
"""

# 默认用算法管控这两个路口
DEMO_TLS_IDS = ("15", "21")

INTERSECTIONS = {
    "15": {
        # 绿灯相位
        "phases": [0, 3, 6],
        # 各相位对应的进口道lane
        "links": {
            0: ["-1033_0"],
            3: ["-1088_0"],
            6: ["-1125_0"],
        },
        "green_states": ["rrrggrr", "rrrrrgg", "gggrrrr"],
        "yellow_states": ["rrryyrr", "rrrrryy", "yyyrrrr"],
    },
    "21": {
        "phases": [0, 3, 6, 9],
        "links": {
            0: ["-1204_0"],
            3: ["-1038_0"],
            6: ["-1206_0"],
            9: ["-1092_0"],
        },
        "green_states": [
            "ggggrrrrrrrrrr",
            "rrrrrrrrrrrggg",
            "rrrrrrrggggrrr",
            "rrrrgggrrrrrrr",
        ],
        "yellow_states": [
            "yyyyrrrrrrrrrr",
            "rrrrrrrrrrryyy",
            "rrrrrrryyyyrrr",
            "rrrryyyrrrrrrr",
        ],
    },
}
