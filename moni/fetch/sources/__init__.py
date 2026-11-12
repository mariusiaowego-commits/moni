"""moni.fetch.sources — individual fetcher implementations go here."""

from . import kr_rss      # noqa: F401 — registers Fetcher36krRSS
# v1.0.5.8: removed Fetcher36krNewsflash — 36kr RSS feed already covers
# 36kr content; the React-rendered /newsflashes page wasn't reliably
# extractable via the captcha-bypassing browser path.  The source row
# is removed from DB and config/sources.yaml.
from . import mpaypass     # noqa: F401 — registers FetcherMpaypass
from . import qbitai       # noqa: F401 — registers FetcherQbitai
from . import wechat_rss   # noqa: F401 — registers FetcherWechatRSS
from . import bestblogs    # noqa: F401 — registers FetcherBestBlogs
from . import chinese_independent_blogs  # noqa: F401 — registers FetcherChineseIndependentBlogs
from . import huxiu        # noqa: F401 — registers FetcherHuxiu
from . import xwatch       # noqa: F401 — registers FetcherXWatch (v1.0.11 X keyword monitor)
from . import follow_builders  # noqa: F401 — registers FetcherFollowBuilders (v1.0.41 / sprint-21)
from . import xbot_feed   # noqa: F401 — registers FetcherXbotFeed (v1.0.66 X-bot 精选桥接)
from . import bank_feed   # noqa: F401 — registers FetcherBankFeed (v1.0.76 银行同业 agent 管道)
