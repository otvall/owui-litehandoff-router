# Development

Целевая среда: Open WebUI **0.12.3**, Python 3.11 или 3.12. Runtime
использует стандартную библиотеку и библиотеки установленного OWUI;
дополнительный сервис и библиотеки оркестрации не требуются.

На машине с интернетом заранее подготовьте зависимости:

```sh
python3 -m venv .venv
.venv/bin/python -m pip download -r requirements-native.txt -d wheelhouse
```

Перенесите wheelhouse на ту же ОС/архитектуру с той же версией Python:

```sh
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements-native.txt
.venv/bin/python -m pip install --no-index --no-build-isolation .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Это установка пакета для разработки. Для сервера OWUI используются два
самостоятельных файла `dist/`, которым этот пакет и checkout не нужны.
