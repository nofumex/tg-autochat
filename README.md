# tg-autochat

Минимальный бот для Telegram Secretary Mode / Chat Automation.

Бот отвечает только через `FREE_LLM`. Локальных шаблонных автоответов нет: если LLM proxy не дал ответ, бот молчит и не помечает сообщение прочитанным.

## Запуск

```powershell
python -m tg_autochat
```

Бот автоматически читает `.env`.

## Обязательные env

```env
BOT_API_KEY=...
ADMIN_ID=...
```

## LLM

```env
RESPONDER_MODE=llm

FREE_LLM_API_KEY=...
BASE_URL=http://155.212.217.115:3001/v1
ENDPOINT=/v1/chat/completions
FREE_LLM_MODEL=auto
FREE_LLM_FALLBACK_MODELS=auto,@cf/openai/gpt-oss-120b,@cf/qwen/qwen3-30b-a3b-fp8,DeepSeek-V3.2,DeepSeek-V3.1,@cf/deepseek-ai/deepseek-r1-distill-qwen-32b,@cf/meta/llama-4-scout-17b-16e-instruct,gpt-oss-120b,poolside/laguna-m.1:free,poolside/laguna-xs.2:free,groq/compound-mini
LLM_TIMEOUT=90
LLM_MAX_TOKENS=700
```

`FREE_LLM_MODEL=auto` оставляет fallback-цепочку на стороне вашего proxy.
Если `auto` попадает в нерабочий маршрут, бот пробует модели из `FREE_LLM_FALLBACK_MODELS` по очереди.

## Prompt

По умолчанию `back_prompt.md` используется как system prompt для общения с потенциальными агентами из HH.

```env
SYSTEM_PROMPT_FILE=back_prompt.md
# LLM_SYSTEM_PROMPT=...
```

Если задать `LLM_SYSTEM_PROMPT`, он переопределит файл.

## Telegram

1. В `@BotFather` включите у бота `Secretary Mode`.
2. Запустите приложение локально: `python -m tg_autochat`.
3. В настройках Telegram-аккаунта найдите `Chat Automation` / `Telegram Business > Chatbots`.
4. Подключите бота к аккаунту и выдайте минимум чтение сообщений и ответы на сообщения.
5. С другого Telegram-аккаунта напишите на подключенный аккаунт.

Команды в личном чате с ботом:

```text
/status - статус бота, подключений и LLM
/last - последние события
```
