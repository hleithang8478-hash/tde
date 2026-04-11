# -*- coding: utf-8 -*-
"""邮件信号接入：轮询邮箱并解析交易信号写入数据库"""

import os
import sys
import json
import time
import imaplib
import email
from email.header import decode_header

from sqlalchemy import create_engine

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import DB_CONFIG, EMAIL_INGEST_CONFIG
from src.core.repository import SignalRepository
from src.signal_ingest import insert_signal, SignalValidationError


def create_db_engine():
    db_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
    )
    return create_engine(db_url, pool_pre_ping=True, future=True)


def decode_mime_words(raw_subject: str) -> str:
    parts = decode_header(raw_subject or "")
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def extract_plain_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore")
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="ignore")
    return ""


def parse_signal_from_email(subject: str, body: str):
    """
    约定：
    1) 邮件主题需以 subject_prefix 开头（默认 EMS_SIGNAL）
    2) 邮件正文为单行JSON，例如：
       {"stock_code":"600519.SH","signal_type":"ORDER","action":"BUY","volume":100,"price_type":"MARKET"}
    """
    prefix = EMAIL_INGEST_CONFIG["subject_prefix"]
    if not subject.startswith(prefix):
        return None

    body = body.strip()
    if not body:
        raise SignalValidationError("邮件正文为空，无法解析信号")

    try:
        return json.loads(body)
    except Exception as exc:
        raise SignalValidationError("邮件正文不是合法JSON") from exc


def mark_seen(mail: imaplib.IMAP4_SSL, uid: bytes):
    mail.uid("STORE", uid, "+FLAGS", "(\\Seen)")


def main():
    if not EMAIL_INGEST_CONFIG.get("enabled", False):
        print("[INFO] 邮件接入未启用，请在 src/config.py 设置 EMAIL_INGEST_CONFIG['enabled']=True")
        return

    engine = create_db_engine()
    repo = SignalRepository(engine)

    host = EMAIL_INGEST_CONFIG["imap_host"]
    port = int(EMAIL_INGEST_CONFIG["imap_port"])
    username = EMAIL_INGEST_CONFIG["username"]
    password = EMAIL_INGEST_CONFIG["password"]
    folder = EMAIL_INGEST_CONFIG.get("folder", "INBOX")
    interval = int(EMAIL_INGEST_CONFIG.get("poll_interval_seconds", 15))

    print("[INFO] 邮件接入启动成功，开始轮询邮箱...")

    while True:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(host, port)
            mail.login(username, password)
            mail.select(folder)

            status, data = mail.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("IMAP SEARCH 失败")

            uids = (data[0] or b"").split()
            for uid in uids:
                s2, msg_data = mail.uid("FETCH", uid, "(RFC822)")
                if s2 != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                subject = decode_mime_words(msg.get("Subject", ""))
                body = extract_plain_text(msg)

                try:
                    parsed = parse_signal_from_email(subject, body)
                    if parsed is None:
                        # 不符合规则的邮件直接标已读跳过
                        mark_seen(mail, uid)
                        continue

                    signal_id = insert_signal(repo, parsed)
                    print(f"[OK] 邮件信号入库成功 signal_id={signal_id}, subject={subject}")
                    mark_seen(mail, uid)
                except SignalValidationError as e:
                    print(f"[WARN] 邮件信号格式错误: {e}, subject={subject}")
                    mark_seen(mail, uid)
                except Exception as e:
                    print(f"[ERROR] 处理单封邮件失败: {type(e).__name__}: {e}")
                    # 不标已读，保留下次重试

        except Exception as e:
            print(f"[ERROR] 邮件轮询异常: {type(e).__name__}: {e}")
        finally:
            try:
                if mail is not None:
                    mail.logout()
            except Exception:
                pass

        time.sleep(interval)


if __name__ == "__main__":
    main()
