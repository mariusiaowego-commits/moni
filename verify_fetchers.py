#!/usr/bin/env python3
"""Verify all fetchers are importable and registered."""
import sys
sys.path.insert(0, "/Users/mt16/dev/source-monitor/wt/t1-db")

from moni.fetch.sources.kr_rss import Fetcher36krRSS
from moni.fetch.sources.kr_newsflash import Fetcher36krNewsflash
from moni.fetch.sources.mpaypass import FetcherMpaypass
from moni.fetch.sources.qbitai import FetcherQbitai
from moni.fetch.sources.wechat_rss import FetcherWechatRSS
from moni.fetch import list_fetchers

fetchers = list_fetchers()
print("Fetchers registered:", list(fetchers.keys()))

for name, cls in [
    ("36kr_rss", Fetcher36krRSS),
    ("36kr_newsflash", Fetcher36krNewsflash),
    ("mpaypass", FetcherMpaypass),
    ("qbitai", FetcherQbitai),
    ("wechat_rss", FetcherWechatRSS),
]:
    inst = cls()
    print(f"  {name}: source_name={inst.source_name}")

print("\nCLI import test:")
from moni.cli.main import main as cli_main
print("  CLI imported OK")
