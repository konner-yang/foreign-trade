#!/usr/bin/env python3
"""Portable mailbox commands. No absolute paths and no local mailbox folders."""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_KEYS = (
    "EMAIL_ADDRESS",
    "EMAIL_AUTH_CODE",
    "IMAP_HOST",
    "IMAP_PORT",
    "SMTP_HOST",
    "SMTP_PORT",
)
DEFAULTS = {
    "IMAP_HOST": "imaphz.qiye.163.com",
    "IMAP_PORT": "993",
    "SMTP_HOST": "smtphz.qiye.163.com",
    "SMTP_PORT": "465",
}
UID_RE = re.compile(r"\bUID (\d+)\b")
ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MailError(Exception):
    """Expected operational failure with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def emit(payload: dict, code: int) -> int:
    payload["ok"] = code == 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def fail(code_name: str, message: str, exit_code: int = 1) -> int:
    return emit({"error": code_name, "message": message}, exit_code)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ENV_KEYS or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def account() -> dict:
    load_env_file(ENV_FILE)
    values = {key: os.environ.get(key, DEFAULTS.get(key, "")).strip() for key in ENV_KEYS}
    missing = [key for key in ("EMAIL_ADDRESS", "EMAIL_AUTH_CODE") if not values[key]]
    if missing:
        raise MailError("missing_config", "缺少 " + ", ".join(missing))
    if not ADDRESS_RE.match(values["EMAIL_ADDRESS"]):
        raise MailError("invalid_address", "EMAIL_ADDRESS 不是邮箱地址")
    try:
        values["IMAP_PORT"] = int(values["IMAP_PORT"])
        values["SMTP_PORT"] = int(values["SMTP_PORT"])
    except ValueError as exc:
        raise MailError("invalid_port", "端口必须是数字") from exc
    return values


def decode_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    try:
        return str(make_header(decode_header(str(value))))
    except (LookupError, ValueError, email.errors.HeaderParseError):
        return str(value)


def parse_uid(fetch_meta) -> str | None:
    match = UID_RE.search(decode_text(fetch_meta))
    return match.group(1) if match else None


def message_text(message: email.message.Message) -> str:
    if message.is_multipart():
        plain = ""
        html = ""
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue
            content = part.get_payload(decode=True)
            if content is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = content.decode(charset, errors="replace")
            except LookupError:
                text = content.decode("utf-8", errors="replace")
            if part.get_content_type() == "text/plain" and not plain:
                plain = text
            elif part.get_content_type() == "text/html" and not html:
                html = text
        return plain or html
    content = message.get_payload(decode=True)
    if content is None:
        return ""
    charset = message.get_content_charset() or "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def attachments(message: email.message.Message) -> list[dict]:
    found = []
    for part in message.walk():
        if part.get_content_disposition() != "attachment":
            continue
        content = part.get_payload(decode=True) or b""
        found.append(
            {
                "filename": decode_text(part.get_filename()) or "attachment",
                "contentType": part.get_content_type(),
                "size": len(content),
            }
        )
    return found


def headers(message: email.message.Message, uid: str, folder: str) -> dict:
    return {
        "uid": uid,
        "folder": folder,
        "messageId": decode_text(message.get("Message-ID")),
        "date": decode_text(message.get("Date")),
        "from": decode_text(message.get("From")),
        "to": decode_text(message.get("To")),
        "cc": decode_text(message.get("Cc")),
        "subject": decode_text(message.get("Subject")),
    }


def connect_imap(cfg: dict) -> imaplib.IMAP4_SSL:
    try:
        client = imaplib.IMAP4_SSL(
            cfg["IMAP_HOST"],
            cfg["IMAP_PORT"],
            ssl_context=ssl.create_default_context(),
            timeout=30,
        )
        client.login(cfg["EMAIL_ADDRESS"], cfg["EMAIL_AUTH_CODE"])
    except (TimeoutError, imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        raise MailError("imap_failed", f"{type(exc).__name__}: 收信服务器连接或登录失败") from exc
    return client


def select_folder(client: imaplib.IMAP4_SSL, folder: str) -> None:
    status, _ = client.select(f'"{folder}"', readonly=True)
    if status != "OK":
        raise MailError("folder_not_found", f"服务器上没有文件夹 {folder}")


def search_uids(client: imaplib.IMAP4_SSL, args: argparse.Namespace) -> list[bytes]:
    criteria: list[str] = []
    if args.unseen:
        criteria.append("UNSEEN")
    if args.since:
        try:
            parsed = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError as exc:
            raise MailError("invalid_date", "--since 必须是 YYYY-MM-DD") from exc
        criteria.extend(["SINCE", parsed.strftime("%d-%b-%Y")])
    if args.sender:
        criteria.extend(["FROM", json.dumps(args.sender)])
    if args.query:
        criteria.extend(["TEXT", json.dumps(args.query)])
    status, data = client.uid("SEARCH", None, *(criteria or ["ALL"]))
    if status != "OK" or not data or data[0] is None:
        raise MailError("search_failed", "服务器搜索失败")
    return data[0].split()


def command_doctor(_: argparse.Namespace) -> int:
    cfg = account()
    client = connect_imap(cfg)
    try:
        status, data = client.list()
        if status != "OK":
            raise MailError("list_failed", "无法读取服务器文件夹")
        return emit(
            {
                "account": cfg["EMAIL_ADDRESS"],
                "imap": f"{cfg['IMAP_HOST']}:{cfg['IMAP_PORT']}",
                "smtp": f"{cfg['SMTP_HOST']}:{cfg['SMTP_PORT']}",
                "folders": len(data or []),
                "smtpChecked": False,
            },
            0,
        )
    finally:
        client.logout()


def command_folders(_: argparse.Namespace) -> int:
    cfg = account()
    client = connect_imap(cfg)
    try:
        status, data = client.list()
        if status != "OK":
            raise MailError("list_failed", "无法读取服务器文件夹")
        names = []
        for item in data or []:
            if not item:
                continue
            decoded = decode_text(item)
            match = re.search(r'"([^"]*)"\s*$', decoded)
            names.append(match.group(1) if match else decoded.rsplit(" ", 1)[-1].strip('"'))
        return emit({"account": cfg["EMAIL_ADDRESS"], "folders": names}, 0)
    finally:
        client.logout()


def command_list(args: argparse.Namespace) -> int:
    cfg = account()
    client = connect_imap(cfg)
    try:
        select_folder(client, args.folder)
        uids = search_uids(client, args)
        selected = list(reversed(uids))[: args.limit]
        messages = []
        for uid in selected:
            status, fetched = client.uid(
                "FETCH",
                uid,
                "(UID BODY.PEEK[HEADER.FIELDS (DATE FROM TO CC SUBJECT MESSAGE-ID)])",
            )
            if status != "OK" or not fetched or not fetched[0]:
                continue
            meta, raw = fetched[0][0], fetched[0][1]
            parsed_uid = parse_uid(meta) or decode_text(uid)
            message = email.message_from_bytes(raw)
            item = headers(message, parsed_uid, args.folder)
            messages.append(item)
        return emit(
            {
                "account": cfg["EMAIL_ADDRESS"],
                "folder": args.folder,
                "matched": len(uids),
                "returned": len(messages),
                "messages": messages,
            },
            0,
        )
    finally:
        client.logout()


def fetch_full(args: argparse.Namespace) -> tuple[dict, email.message.Message]:
    cfg = account()
    client = connect_imap(cfg)
    try:
        select_folder(client, args.folder)
        status, fetched = client.uid("FETCH", args.uid.encode(), "(UID BODY.PEEK[])")
        if status != "OK" or not fetched or fetched[0] is None:
            raise MailError("message_not_found", f"文件夹 {args.folder} 中没有 UID {args.uid}")
        meta, raw = fetched[0][0], fetched[0][1]
        message = email.message_from_bytes(raw)
        item = headers(message, parse_uid(meta) or args.uid, args.folder)
        item["account"] = cfg["EMAIL_ADDRESS"]
        return item, message
    finally:
        client.logout()


def command_read(args: argparse.Namespace) -> int:
    item, message = fetch_full(args)
    item["attachments"] = attachments(message)
    item["body"] = message_text(message)
    if args.save:
        save_dir = Path(args.save)
        if not save_dir.is_absolute():
            save_dir = ROOT / save_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for part in message.walk():
            if part.get_content_disposition() != "attachment":
                continue
            name = Path(decode_text(part.get_filename()) or "attachment").name
            target = save_dir / name
            target.write_bytes(part.get_payload(decode=True) or b"")
            saved.append(target.name)
        item["saved"] = saved
    return emit(item, 0)


def command_draft(args: argparse.Namespace) -> int:
    item, message = fetch_full(args)
    subject = item["subject"]
    if subject and not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    sender_name, sender_address = parseaddr(item["from"])
    return emit(
        {
            "account": item["account"],
            "replyToUid": item["uid"],
            "folder": item["folder"],
            "inReplyTo": item["messageId"],
            "to": sender_address,
            "subject": subject or "",
            "originalFrom": formataddr((sender_name, sender_address)) if sender_address else item["from"],
            "originalBody": message_text(message),
            "draft": "",
        },
        0,
    )


def build_message(cfg: dict, args: argparse.Namespace) -> EmailMessage:
    recipients = [item.strip() for item in args.to.split(",") if item.strip()]
    invalid = [item for item in recipients if not ADDRESS_RE.match(item)]
    if not recipients or invalid:
        raise MailError("invalid_recipient", "收件人必须是逗号分隔的邮箱地址")
    message = EmailMessage()
    message["From"] = cfg["EMAIL_ADDRESS"]
    message["To"] = ", ".join(recipients)
    message["Subject"] = args.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(args.body)
    for name in args.attach or []:
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise MailError("attachment_missing", f"附件不存在：{path.name}")
        data = path.read_bytes()
        message.add_attachment(
            data,
            maintype="application",
            subtype="octet-stream",
            filename=path.name,
        )
    return message


def envelope(message: EmailMessage) -> dict:
    return {
        "from": message["From"],
        "to": message["To"],
        "subject": message["Subject"],
        "body": message.get_content(),
        "attachments": [
            part.get_filename()
            for part in message.iter_attachments()
            if part.get_filename()
        ],
    }


def command_send(args: argparse.Namespace) -> int:
    cfg = account()
    message = build_message(cfg, args)
    preview = envelope(message)
    if not args.yes:
        preview["confirmation"] = "未发送。确认后追加 --yes 再执行一次。"
        return emit(preview, 2)
    try:
        with smtplib.SMTP_SSL(
            cfg["SMTP_HOST"],
            cfg["SMTP_PORT"],
            context=ssl.create_default_context(),
            timeout=30,
        ) as client:
            client.login(cfg["EMAIL_ADDRESS"], cfg["EMAIL_AUTH_CODE"])
            client.send_message(message)
    except (TimeoutError, OSError, ssl.SSLError):
        preview["delivery"] = "unknown_do_not_retry"
        return emit(preview, 1)
    except smtplib.SMTPException as exc:
        preview["delivery"] = "rejected"
        preview["detail"] = type(exc).__name__
        return emit(preview, 1)
    preview["delivery"] = "accepted"
    preview["sentAt"] = datetime.now(timezone.utc).isoformat()
    return emit(preview, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取、拟稿和发送企业邮箱")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("folders")

    listing = sub.add_parser("list")
    listing.add_argument("--folder", default="INBOX")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--since")
    listing.add_argument("--from", dest="sender")
    listing.add_argument("--query")
    listing.add_argument("--unseen", action="store_true")

    search = sub.add_parser("search")
    search.add_argument("--folder", default="INBOX")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--since")
    search.add_argument("--from", dest="sender")
    search.add_argument("--query")
    search.add_argument("--unseen", action="store_true")

    reading = sub.add_parser("read")
    reading.add_argument("--uid", required=True)
    reading.add_argument("--folder", default="INBOX")
    reading.add_argument("--save")

    draft = sub.add_parser("draft")
    draft.add_argument("--uid", required=True)
    draft.add_argument("--folder", default="INBOX")

    sending = sub.add_parser("send")
    sending.add_argument("--to", required=True)
    sending.add_argument("--subject", required=True)
    sending.add_argument("--body", required=True)
    sending.add_argument("--attach", action="append")
    sending.add_argument("--yes", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    commands = {
        "doctor": command_doctor,
        "folders": command_folders,
        "list": command_list,
        "search": command_list,
        "read": command_read,
        "draft": command_draft,
        "send": command_send,
    }
    try:
        return commands[args.command](args)
    except MailError as exc:
        return fail(exc.code, str(exc))


if __name__ == "__main__":
    sys.exit(main())
