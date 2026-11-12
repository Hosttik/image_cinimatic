# BLOCKFRAME — Minecraft Machinima Generator · Stage 2

Локальный Python/FastAPI-сервис, который превращает **текстовый промпт в сценарий, набор статичных PNG-кадров и MP4**. Это не конвейер генерации GIF или видеомодель.

## Что реализовано

- AI-сценарист (`gpt-5.6-terra`) генерирует `storyboard.json`: **5 сцен × 4 секунды**.
- `shot_plan.json` размечает кадры: **3 опорных keyframes** и **1–9 промежуточных in-betweens** на сцену.
- GPT-Image-2.6 Sunburst создаёт `character_reference.png` (главный визуальный эталон) и **4/6/8/12 PNG на сцену**.
- Для согласованности персонажа модель видит общую эталонную внешность плюс ближайшие якоря и получает continuity-инструкции, но **это не гарантирует одинаковую геометрию и плавное движение**. У сложных действий возможны артефакты.
- FFmpeg собирает **ровно 20 секунд / 30 или 60 FPS / 1280×720 H.264 MP4**. Можно включить оптическую интерполяцию (`minterpolate`); по умолчанию она выключена ради скорости и отсутствия артефактов.
- После получения ролика можно **перегенерировать конкретную сцену 1–5** с указанием режиссёра. Другие четыре видеоклипа и их PNG остаются неизменными. Пересчитывается только клип изменённой сцены и финальный монтаж.
- Все исходные кадры, версии, состояние проекта и история регенераций сохраняются в `outputs/<job_id>/`. Готовые задания можно снова открыть по ID после перезапуска сервиса.
- Режим **Demo** не вызывает OpenAI. Он проверяет технический конвейер на процедурных рисунках, **не** следует произвольному сюжету промпта.

## Запуск

Нужны Python 3.11+ и `ffmpeg` / `ffprobe` в `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # Windows: copy .env.example .env
# Для AI-режима впишите OPENAI_API_KEY=... в .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Затем открыть **http://127.0.0.1:8000**. Есть также `docker compose up --build` (Docker использует локальную папку outputs).

### Затраты / ограничения

| Настройка | Число запросов генерации изображений при одном AI-ролике |
| --- | ---: |
| 4 кадра/сцену | 21 (20 кадров + 1 reference) |
| 6 кадров/сцену | 31 (30 кадров + 1 reference) |
| 8 кадров/сцену | 41 (40 кадров + 1 reference) |
| 12 кадров/сцену | 61 (60 кадров + 1 reference) |

Плюс **1 запрос** к AI-сценаристу. Регенерация одной сцены стоит ещё **4/6/8/12** запросов к модели изображений соответственно; storyboard и master reference переиспользуются. Цена зависит от реального количества токенов, размера и качества изображения, числа референсов и модели. Пользователь должен предварительно оценить бюджет. Запросы в реальный OpenAI API не тестировались без ключа.

При включённой интерполяции вычисления выполняются внутри FFmpeg в меньшем разрешении перед масштабированием до 720p. Это быстрее, но иногда даёт заметную потерю деталей и геометрические дефекты. 30/60 FPS экспортного MP4 **не означает** 30/60 разных AI-изображений за секунду.

## Этапы и структура

```text
Prompt
  ├─> AI script -> storyboard.json
  ├─> Shot plan -> shot_plan.json
  ├─> Master reference -> character_reference.png
  ├─> For each shot: keyframe_1, keyframe_mid, keyframe_end
  │       └─> intermediate PNG frames anchored on adjacent keyframes
  ├─> clip_01.mp4 .. clip_05.mp4
  └─> result.mp4

Regenerate shot_02
  ├─> reuse storyboard + master reference + preceding shot tail frame
  ├─> revisions/shot_02/rNNN_xxx/frames/*.png
  ├─> render only new clip_02.mp4
  ├─> concat old clips 1,3,4,5 + new clip 2
  └─> publish new result.mp4, keep previous source frames
```

Файлы задания:

```text
outputs/<job_id>/
  project_state.json                # revisions + source paths + history
  storyboard.json
  shot_plan.json
  character_reference.png          # только AI
  frames/shot_01/frame_001.png ...
  frames/shot_02/frame_001.png ...
  ...
  clip_01.mp4 ... clip_05.mp4
  result.mp4
  revisions/
    shot_02/
      r001_<random_id>/
        frames/frame_001.png ...
        clip_02.mp4
        previous_result.mp4
```

## API

```http
POST /api/jobs
Content-Type: application/json

{
  "prompt": "Робинзон очнулся на острове в стиле Minecraft Machinima",
  "mode": "demo",
  "fps": 30,
  "frames_per_shot": 6,
  "interpolate": false
}
```

- `GET /api/jobs/{id}` — статус, сценарий, версии и история.
- `GET /api/jobs/{id}/storyboard` — JSON-сценарий.
- `GET /api/jobs/{id}/shot-plan` — JSON-план кадров.
- `GET /api/jobs/{id}/shots/{shot_number}/frames/{frame_number}` — активный PNG-кадр.
- `GET /api/jobs/{id}/video` — собранный MP4.
- `POST /api/jobs/{id}/shots/{shot_number}/regenerate` — перегенерировать только сцену.

```http
POST /api/jobs/<id>/shots/2/regenerate
Content-Type: application/json

{"instruction":"Более плавный поворот головы, не менять костюм"}
```

При ошибке регенерации активный ролик остаётся доступным и `regen_error` появляется в статусе. В процессе регенерации повторный запрос возвращает **409 Conflict**. В текущей версии коррекция влияет на изображения, но не переписывает исходный сценарий.

## Тесты

```bash
pytest -q
```

Тестируются контракты storyboard/shot plan, payload к Image API через мок, порядок keyframes/in-betweens, сборка ровно 600 кадров, HTTP API, восстановление задания после сброса оперативной памяти, сохранность других сцен и неразрушительное поведение при ошибке регенерации.

## Технические ограничения

- **MVP для локального использования**. Нет пользователей, авторизации, биллинга, БД, очистки истории, очереди на нескольких воркерах, ограничений затрат на сервере. Запускать только на `127.0.0.1` с одним воркером или за обратным прокси с авторизацией.
- Для длительной истории и распределённого рендера понадобятся SQLite/PostgreSQL и отдельный воркер с очередью заданий.
- История **готовых** заданий восстанавливается из `project_state.json` по известному ID; задания, прерванные до готовности, автоматически не продолжаются.
- AI-генерация может не сохранить персонажа идеально и может исказить форму объектов при промежуточном редактировании. Результат необходимо визуально оценить до заявлений о пригодности для кинематографического ролика.
- Контент demo-версии — условный воксельный тест, не демонстрация качества Sunburst.
- Перегенерация предыдущей сцены не обновляет последующие сцены, поэтому на стыке сцен возможна потеря continuity. В следующей итерации можно добавить автоматическую проверку границ соседних кадров.

## Документация OpenAI

- [GPT-Image-2.6 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)
- [Image generation](https://developers.openai.com/api/docs/guides/image-generation)
- [Image edits API](https://developers.openai.com/api/reference/python/resources/images/methods/edit)