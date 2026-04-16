# Запуск GIDEON

## 1. Установка зависимостей

```bash
pip install websockets edge-tts SpeechRecognition pyaudio
```

> **PyAudio на разных системах:**
> - Windows: `pip install pipwin && pipwin install pyaudio`
> - Mac: `brew install portaudio && pip install pyaudio`  
> - Linux: `sudo apt install portaudio19-dev python3-pyaudio`

> **Аудиоплеер для Linux** (воспроизведение TTS):
> ```bash
> sudo apt install mpg123
> ```

## 2. Запуск

```bash
# Полный режим: микрофон + TTS + визуал
python server.py

# Без микрофона (только CLI + визуал)
python server.py --no-voice

# Без TTS (тихий режим)
python server.py --no-tts
```

## 3. Открыть визуал

Открыть `gideon.html` в браузере **Chrome или Edge**.

Зелёная точка в левом верхнем углу = WebSocket подключён.

## 4. Управление голосом

Скажите в микрофон:
- **"Гидеон"** — пробудить
- **"Подумай"** — режим анализа
- **"Слушай"** — режим прослушивания
- **"Статус"** — Гидеон скажет своё состояние
- **"Стоп"** — вернуть в idle
- **"Спать"** — выключить

## 5. Управление через терминал

После `python server.py` в терминале работает CLI — просто вводите команды.

## Структура файлов

```
gideon.html  — визуальный интерфейс (открыть в браузере)
server.py    — точка входа, WebSocket + CLI
core.py      — логика состояний и интентов
voice.py     — микрофон (STT) + синтез речи (TTS)
memory.py    — JSON-память
```
