# 摘要视角定义
# v1.0.5.24: 多视角自适应摘要系统
#
# 每个视角定义:
# - name: 视角名称
# - reader: 目标读者描述
# - focus: 关注重点
# - match_tags: 匹配哪些 tag 时优先使用此视角
# - questions: 摘要要回答的 3 个核心问题

PERSPECTIVES = [
    {
        "name": "银行分析师",
        "reader": "银行管理层（行长/副行长/部门总），没有时间读原文",
        "focus": "这件事对银行业务有什么影响，能否借鉴",
        "match_tags": ["banking", "payment", "regulation"],
        "questions": [
            "谁做了什么（具体银行/公司/产品名）",
            "对银行业务的影响（可量化的数据/战略意义）",
            "可借鉴的实践或趋势信号",
        ],
    },
    {
        "name": "微信渠道经营专家",
        "reader": "头部银行的新媒体/私域运营负责人，关注微信生态内的获客、留存、转化",
        "focus": "微信生态内的渠道机会、用户运营玩法、竞品动态、可复用的策略",
        "match_tags": ["wechat"],
        "questions": [
            "什么机构/品牌在微信生态做了什么（具体动作/功能/活动）",
            "对微信渠道经营的启示（获客/留存/转化/私域的角度）",
            "银行可借鉴或需要警惕的点",
        ],
    },
    {
        "name": "AI科技观察者",
        "reader": "关注AI技术落地的科技从业者和投资人",
        "focus": "技术突破、产品形态、商业模式、竞争格局",
        "match_tags": ["ai-fintech", "general-tech"],
        "questions": [
            "谁发布了什么（技术/产品/模型）",
            "技术或商业上的突破点",
            "对行业格局的影响",
        ],
    },
]


def select_perspective(tags: list[str]) -> dict:
    """根据文章的 tag 选择最匹配的视角。

    优先匹配: tag 数量最多的视角胜出。
    无匹配时 fallback 到银行分析师。
    """
    best = PERSPECTIVES[0]  # fallback: 银行分析师
    best_score = 0
    for p in PERSPECTIVES:
        score = len(set(tags) & set(p["match_tags"]))
        if score > best_score:
            best_score = score
            best = p
    return best
