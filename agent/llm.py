"""ساخت کلاینت Ollama. trust_env=False یعنی اتصال محلی هرگز از پروکسی/فیلترشکن ویندوز عبور نمی‌کند."""
import config


def make_client():
    from ollama import Client
    return Client(host=config.OLLAMA_HOST, timeout=config.OLLAMA_TIMEOUT, trust_env=False)
