"""moni.fetch.sources — individual fetcher implementations go here."""

from . import kr_rss      # noqa: F401 — registers Fetcher36krRSS
from . import kr_newsflash  # noqa: F401 — registers Fetcher36krNewsflash
from . import mpaypass     # noqa: F401 — registers FetcherMpaypass
from . import qbitai       # noqa: F401 — registers FetcherQbitai
from . import wechat_rss   # noqa: F401 — registers FetcherWechatRSS
