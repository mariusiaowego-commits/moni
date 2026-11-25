-- 066_case_taxonomy.sql — 案例 8 类体系 + 10 个业务场景受控词表 (P1-1)
-- schema_version 65 → 66

CREATE TABLE IF NOT EXISTS case_categories (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);

-- ── 8 类提案（一行一类，可直接整段替换） ─────────────────────────────────
INSERT OR IGNORE INTO case_categories (code, name, sort_order, description) VALUES
    ('ai_adoption',     'AI与大模型应用',   1, '大模型、智能体、AI助手、智能客服/风控/审单等AI技术业务落地'),
    ('product_launch',  '产品与服务创新',   2, '新金融产品、支付功能、卡业务、信贷/理财/移动银行功能上线'),
    ('digital_infra',   '数字底座与架构',   3, '核心系统、分布式架构、云平台、算力调度、数据中心与芯片底座'),
    ('partnership',     '生态合作与联名',   4, '跨机构战略合作、生态互联、联合实验室、平台接入与渠道合作'),
    ('capital',         '融资与资本运作',   5, '投融资、并购、收购要约、资产剥离、IPO上市与股权交易'),
    ('procurement',     '招采与中标项目',   6, '银行及金融机构科技项目招标、采购预算、入围与中标公示'),
    ('compliance_risk', '合规与风险事件',   7, '监管处罚、合规整改、安全漏洞、智能体失控与数据泄露事件'),
    ('org_strategy',    '组织与战略调整',   8, '组织架构调整、裁员降本、高管变动、业务关停与出海战略布局');

ALTER TABLE cases ADD COLUMN case_category TEXT;
CREATE INDEX IF NOT EXISTS idx_cases_category ON cases(case_category);

CREATE TABLE IF NOT EXISTS case_scenarios (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);

-- ── 10 个受控业务场景（一行一类，可直接整段替换） ─────────────────────────
INSERT OR IGNORE INTO case_scenarios (code, name, sort_order, description) VALUES
    ('cross_border_pay',    '跨境支付与清算',   1, '跨境汇款、国际清算网络、外币结算、多边货币桥与跨境收单'),
    ('mobile_digital_pay',  '移动支付与钱包',   2, '移动支付、二维码/NFC/刷脸支付、数字钱包与商户收单'),
    ('ecny_stablecoin',     '数字人民币与稳定币', 3, '数字人民币(e-CNY)运营扩容与合规稳定币、链上支付结算'),
    ('llm_agent',           '大模型与智能体',   4, '生成式AI、大语言模型、AI Agent、智能编码与知识助手'),
    ('channel_service',     '手机银行与渠道',   5, '手机银行App整合、信用卡App、微信小程序与网点/远程智能服务'),
    ('risk_compliance',     '智能风控与合规',   6, '反欺诈、反洗钱(AML)、KYC身份核验、安全监测与监管科技'),
    ('credit_supply_chain', '数字化信贷与供应链', 7, '普惠小微、消费信贷、供应链金融与产业链数字化融资'),
    ('wealth_advisory',     '财富管理与投顾',   8, '智能投顾、AI投研、理财产品销售与高净值客户经营'),
    ('core_infra_cloud',    '核心系统与算力底座', 9, '核心业务系统改造、分布式云架构、算力芯片与机房基础设施'),
    ('other',               '其他业务场景',     10, '暂未归入上述 9 个核心场景的案例');

CREATE TABLE IF NOT EXISTS case_scenario_links (
    case_id       TEXT NOT NULL,
    scenario_code TEXT NOT NULL,
    is_primary    INTEGER NOT NULL DEFAULT 1,
    source_type   TEXT NOT NULL DEFAULT 'auto_rule'
        CHECK (source_type IN ('auto_rule', 'auto_llm', 'manual_user')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (case_id, scenario_code),
    FOREIGN KEY (case_id)       REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (scenario_code) REFERENCES case_scenarios(code) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_scn_links_scn ON case_scenario_links(scenario_code);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (65, datetime('now'));
