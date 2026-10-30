"""Работа с локальными моделями через Ollama: чат-модель агента и модель эмбеддингов.

Модели задаются переменными окружения:
  DOTA_MODEL  — чат-модель с поддержкой инструментов (по умолчанию qwen3:8b);
  DOTA_EMBED  — модель эмбеддингов (по умолчанию bge-m3, понимает русский).
"""
import os

try:
    import ollama
except ImportError:
    ollama = None

MODEL = os.getenv('DOTA_MODEL', 'qwen3:8b')
EMBED_MODEL = os.getenv('DOTA_EMBED', 'bge-m3')
# Один размер контекста на все вызовы: иначе Ollama перезагружает модель при каждой смене num_ctx.
NUM_CTX = 8192


def installed_models():
    """Имена скачанных моделей или None, если Ollama не установлена / не запущена."""
    if ollama is None:
        return None
    try:
        return {m.model for m in ollama.list().models}
    except Exception:
        return None


def has_model(name, models=None):
    models = installed_models() if models is None else models
    if not models:
        return False
    # «bge-m3» и «bge-m3:latest» — одна и та же модель.
    return name in models or f'{name}:latest' in models


def chat(**kwargs):
    """ollama.chat без «размышлений» у qwen3: короче и быстрее ответ.
    Модели без режима размышлений параметр think не принимают — тогда повторяем без него."""
    kwargs.setdefault('model', MODEL)
    try:
        return ollama.chat(think=False, **kwargs)
    except Exception as ex:
        if 'think' not in str(ex).lower():
            raise
        return ollama.chat(**kwargs)


def embed(texts):
    return ollama.embed(model=EMBED_MODEL, input=list(texts)).embeddings
