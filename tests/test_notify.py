from bot.notify import TelegramNotifier


def test_notify_disabled_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = TelegramNotifier()
    assert n.enabled is False
    assert n.send("halo") is False
    ok, err = n.send_sync("halo")
    assert ok is False and err


def test_notify_enabled_flag(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    n = TelegramNotifier()
    assert n.enabled is True
