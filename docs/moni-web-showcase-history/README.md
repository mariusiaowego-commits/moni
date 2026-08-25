# moni-web-showcase 历史版本目录

## 命名约定 (2026-08-25 制定)

每次新 demo 落版，必须保留当前 `docs/moni-web-showcase.html` 到这个目录，
文件名格式:

```
v<moni-version>-<short-slug>-<YYYY-MM-DD>.html
```

示例：
- `v1.0.35.2-final-2026-08-21.html`
- `v1.0.36.0-refresh-2026-09-15.html`
- `v1.0.37.0-post-sprint-2026-09-30.html`

## 流程

1. rebuild demo (or 手改) 完成后
2. **先** 把当前 `docs/moni-web-showcase.html` 复制为 `v<ver>-<slug>-<date>.html`
3. **再** commit + push + PR
4. PR body 里列上 "history: docs/moni-web-showcase-history/v<ver>-<slug>-<date>.html"

**禁止**: 一次 ship 只留一个单文件 `docs/moni-web-showcase.html` 就完事。

## 当前版本历史

| 文件 | md5 | 来源 | 备注 |
|------|------|------|------|
| `v1.0.35.2-final-2026-08-21.html` | `926312d40d9234b25c2cd93e6f4dade9` | 微信附件 (dad 8-21 09:15 传给参赛评委) | **当前 demo baseline** (v1.0.35.16.3 ship) |
| `v1.0.35.2-git-069b4cc-2026-08-19.html` | `dd49bae4aaa166954e1a0422cf0984bb` | git commit `069b4cc` v1.0.35.2-hotfix (8-19 17:16) | 参考: git 上对应的官方 commit |

## 教训 (2026-08-25)

今天上午 v1.0.35.16 + 16.1 + 16.2 三次 demo rebuild 反复改 demo HTML (commit + revert 来回 6+ 次),
最后发现 dad 传出去的参赛版还正常, 而我的 rebuild 跑飞了 (整页空白).
**未来禁止**: 改 demo HTML 前必须先备份当前版本到这个目录, 不允许盲目覆盖.