#!/usr/bin/env python3
"""First-run guide. Employees only answer the questions shown here."""

from __future__ import annotations

import getpass
import io
import os
import sys
from contextlib import redirect_stdout

import mail


def say(text: str = "") -> None:
    print(text, flush=True)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def pause(message: str) -> None:
    say(message)
    ask("按回车继续...")


def quiet(func, *args):
    with redirect_stdout(io.StringIO()):
        return func(*args)


def doctor_code() -> int:
    try:
        return quiet(mail.command_doctor, mail.argparse.Namespace())
    except mail.MailError:
        return 1


def configured() -> bool:
    mail.load_env_file(mail.ENV_FILE)
    return bool(os.environ.get("EMAIL_ADDRESS") and os.environ.get("EMAIL_AUTH_CODE"))


def save_account(address: str, auth_code: str) -> None:
    lines = [
        f"EMAIL_ADDRESS={address}",
        f"EMAIL_AUTH_CODE={auth_code}",
        f"IMAP_HOST={mail.DEFAULTS['IMAP_HOST']}",
        f"IMAP_PORT={mail.DEFAULTS['IMAP_PORT']}",
        f"SMTP_HOST={mail.DEFAULTS['SMTP_HOST']}",
        f"SMTP_PORT={mail.DEFAULTS['SMTP_PORT']}",
        "",
    ]
    temporary = mail.ENV_FILE.with_name(".env.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(mail.ENV_FILE)
    os.environ["EMAIL_ADDRESS"] = address
    os.environ["EMAIL_AUTH_CODE"] = auth_code


def setup() -> bool:
    say("第一次使用，只需要填两项。")
    say("授权码不是网页登录密码。管理员会单独发给你。")
    say("输入授权码时屏幕不会显示字符，这是正常的。")
    say()
    address = ask("企业邮箱地址：")
    if not mail.ADDRESS_RE.match(address):
        pause("邮箱地址格式不对。请重新双击本文件。")
        return False
    if sys.stdin.isatty():
        auth_code = getpass.getpass("授权码：").strip()
    else:
        auth_code = ask("授权码：")
    if not auth_code:
        pause("授权码不能为空。请重新双击本文件。")
        return False
    save_account(address, auth_code)
    say()
    say("已保存。正在检测邮箱...")
    return True


def show_messages(messages: list[dict]) -> None:
    if not messages:
        say("收件箱里没有邮件。")
        return
    say(f"最近 {len(messages)} 封邮件：")
    say()
    for index, item in enumerate(messages, 1):
        subject = item.get("subject") or "(无主题)"
        sender = item.get("from") or "(无发件人)"
        date = item.get("date") or ""
        say(f"{index}. {subject}")
        say(f"   发件人：{sender}")
        if date:
            say(f"   时间：{date}")
        say()


def read_one(messages: list[dict]) -> None:
    choice = ask("输入序号查看正文，直接回车返回：")
    if not choice:
        return
    if not choice.isdigit() or not 1 <= int(choice) <= len(messages):
        pause("序号不对。")
        return
    item = messages[int(choice) - 1]
    args = mail.build_parser().parse_args(["read", "--uid", item["uid"], "--folder", "INBOX"])
    item, message = mail.fetch_full(args)
    say(item.get("subject") or "(无主题)")
    say(f"发件人：{item.get('from') or '(无发件人)'}")
    say()
    say(mail.message_text(message) or "(这封邮件没有可显示的正文)")
    say()
    pause("看完了。")


def browse() -> None:
    args = mail.build_parser().parse_args(["list", "--folder", "INBOX", "--limit", "10"])
    cfg = mail.account()
    client = mail.connect_imap(cfg)
    try:
        mail.select_folder(client, "INBOX")
        uids = mail.search_uids(client, args)
        selected = list(reversed(uids))[:10]
        messages = []
        for uid in selected:
            status, fetched = client.uid(
                "FETCH",
                uid,
                "(UID BODY.PEEK[HEADER.FIELDS (DATE FROM TO CC SUBJECT MESSAGE-ID)])",
            )
            if status != "OK" or not fetched or not fetched[0]:
                continue
            meta, raw = fetched[0]
            parsed = mail.email.message_from_bytes(raw)
            messages.append(mail.headers(parsed, mail.parse_uid(meta) or mail.decode_text(uid), "INBOX"))
    finally:
        client.logout()
    show_messages(messages)
    if messages:
        read_one(messages)


def compose() -> None:
    say("先展示内容，不会直接发出。")
    recipient = ask("收件人邮箱：")
    subject = ask("主题：")
    say("正文：输入完成后，单独输入一行 结束")
    body_lines = []
    while True:
        line = ask("")
        if line == "结束":
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if not body:
        pause("正文是空的，没有发送。")
        return
    preview_args = mail.build_parser().parse_args(
        ["send", "--to", recipient, "--subject", subject, "--body", body]
    )
    try:
        preview = mail.envelope(mail.build_message(mail.account(), preview_args))
    except mail.MailError:
        pause("收件人格式不对，没有发送。")
        return
    say()
    say("请核对，现在还没有发送：")
    say(f"发件人：{preview['from']}")
    say(f"收件人：{preview['to']}")
    say(f"主题：{preview['subject']}")
    say("正文：")
    say(preview["body"])
    say()
    if ask("确认发送吗？输入 是 才发送：") != "是":
        pause("已取消，没有发送。")
        return
    send_args = mail.build_parser().parse_args(
        ["send", "--to", recipient, "--subject", subject, "--body", body, "--yes"]
    )
    code = quiet(mail.command_send, send_args)
    if code == 0:
        pause("发送成功。")
    else:
        pause("发送没有确认成功。请不要重复发送，先联系管理员。")


def menu() -> int:
    say("企业邮箱")
    say("============")
    if not configured() and not setup():
        return 1
    while doctor_code() != 0:
        say()
        say("邮箱暂时连不上。")
        choice = ask("输入 1 重新填写，输入 0 退出：")
        if choice != "1" or not setup():
            return 1
    say("连接成功。")
    while True:
        say()
        say("请选择：")
        say("1. 看最近的邮件")
        say("2. 写一封邮件")
        say("3. 重新填写账号")
        say("0. 退出")
        choice = ask("输入数字：")
        say()
        try:
            if choice == "1":
                browse()
            elif choice == "2":
                compose()
            elif choice == "3":
                if not setup():
                    return 1
                if doctor_code() != 0:
                    pause("新账号没有连接成功，原来的账号信息已被覆盖。")
                    return 1
                say("新账号连接成功。")
            elif choice == "0":
                return 0
            else:
                say("请输入 1、2、3 或 0。")
        except mail.MailError:
            pause("这一步没有完成。请稍后重试；仍然失败就联系管理员。")


if __name__ == "__main__":
    sys.exit(menu())
