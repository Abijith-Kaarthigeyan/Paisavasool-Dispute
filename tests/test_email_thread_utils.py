from src.core.services.email_thread_utils import (
    extract_reference_tokens,
    normalize_message_token,
)


def test_normalize_message_token_strips_angle_brackets():
    assert normalize_message_token("<abc@gmail.com>") == "abc@gmail.com"
    assert normalize_message_token("  GmailId123  ") == "gmailid123"


def test_extract_reference_tokens_from_reply_headers():
    tokens = extract_reference_tokens(
        "<parent@gmail.com>",
        "<root@gmail.com> <parent@gmail.com>",
    )
    assert tokens == ["parent@gmail.com", "root@gmail.com"]
