# 企业邮箱

本项目连接一个已配置的企业邮箱。智能体直接在本目录收信、查找、阅读、总结、拟稿和发信。不要使用 `开始使用.bat`，那是员工菜单。

先读本文件，再运行命令。账号已在本目录 `.env`，不要读取、打印或改写授权码。不要把邮箱文件夹下载到本地，也不要写任何绝对路径。

所有命令都在本目录执行，结果是 JSON。

## 查信和分析

1. 先用条件缩小范围，不要一次读取全部正文。
2. 需要正文时再按 UID 读取。
3. 总结必须来自本次读到的正文，不补充正文里没有的事实。
4. 每项结论都带回 `uid`、`folder`、`date`、`from`、`subject`，便于核对原信。
5. 附件只报告文件名、类型和大小；用户要求保存时才使用 `--save`。

```powershell
python mail.py list --limit 20
python mail.py list --since 2026-11-01 --from someone@example.com --query 关键词
python mail.py list --unseen
python mail.py read --uid 123
```

默认文件夹是 `INBOX`。其他文件夹先运行 `python mail.py folders`，再用 `--folder` 指定服务器返回的名称。这些命令不改变邮件的已读状态。

## 回复和发信

```powershell
python mail.py draft --uid 123
python mail.py send --to someone@example.com --subject "主题" --body "正文"
python mail.py send --to someone@example.com --subject "主题" --body "正文" --yes
```

`draft` 只返回原信和回复对象，不生成正文。回复正文由智能体根据原信写完，再交给 `send`。

不带 `--yes` 的 `send` 只展示发件人、收件人、主题、正文和附件，不会发送。必须先把这五项交给用户确认；用户明确同意后，才追加 `--yes` 执行一次。

结果为 `unknown_do_not_retry` 时不要重发，先告知用户结果不明。`rejected` 才可以根据错误修改后重新让用户确认。

## 连接异常

运行 `python mail.py doctor`。失败时说明邮箱没有连通，不要反复重试，也不要改 `.env`。
