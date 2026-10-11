# Результаты локального STT-бенчмарка (синтетическая речь, CPU, int8)

Сгенерировано: 2026-10-11 15:06 UTC · CPU: 4 ядер · backend: sherpa-onnx 1.13.8, whisper-onnx onnxruntime 1.31.0

Все числа — WER/CER после нормализации (нижний регистр, ё→е, без пунктуации). RTF = время декодирования / длительность аудио (меньше — быстрее). **Аудио синтетическое (Piper TTS) — реальные записи Zoom дадут заметно худший WER.**

## Сводная таблица WER

| Модель | ru чист. (ru) | ru деград. (ru) | kk чист. (kk) | mixed чист. (auto) | mixed чист. (ru) | mixed чист. (kk) | mixed деград. (auto) | RTF (ru чист.) |
|---|---|---|---|---|---|---|---|---|
| tiny (whisper-onnx) | 55.0% | 57.3% | 104.4% | 86.2% | 87.3% | 102.8% | 84.0% | 0.13 |
| base (whisper-onnx) | 33.6% | 36.8% | 94.2% | 80.1% | 77.3% | 91.7% | 75.7% | 0.20 |
| small (whisper-onnx) | 20.2% | 25.4% | 78.8% | 74.0% | 74.6% | 101.1% | 71.3% | 0.54 |
| medium (whisper-onnx) | 18.2% | 16.6% | 67.2% | 66.8% | 66.8% | 88.9% | 68.0% | 1.98 |
| large-v3-turbo (whisper-onnx) | 13.0% | 13.0% | 42.3% | 58.0% | 56.9% | 69.1% | 59.1% | 0.78 |
| tiny (sherpa-onnx) | 45.9% | 49.2% | 97.1% | 75.7% | 74.6% | 90.1% | 86.2% | 0.09 |
| base (sherpa-onnx) | 35.8% | 32.6% | 97.1% | 76.8% | 76.8% | 97.2% | 80.1% | 0.13 |
| small (sherpa-onnx) | 23.1% | 25.1% | 92.7% | 74.0% | 71.3% | 92.3% | 74.0% | 0.41 |
| medium (sherpa-onnx) | 18.9% | 20.2% | 86.1% | 68.0% | 68.5% | 90.1% | 66.8% | 1.60 |

## Все прогоны

| Модель | Backend | Подмножество | Условие | Язык | n | WER | CER | RTF | Определённые языки |
|---|---|---|---|---|---|---|---|---|---|
| tiny | whisper-onnx | ru | clean | forced:ru | 32 | 55.0% | 26.4% | 0.13 | ru:32 |
| tiny | whisper-onnx | ru | clean | auto | 32 | 55.0% | 26.4% | 0.07 | ru:32 |
| tiny | whisper-onnx | ru | degraded | forced:ru | 32 | 57.3% | 28.4% | 0.11 | ru:32 |
| tiny | whisper-onnx | kk | clean | forced:kk | 30 | 104.4% | 57.0% | 0.23 | kk:30 |
| tiny | whisper-onnx | kk | clean | auto | 30 | 105.1% | 91.6% | 0.09 | tr:16, ru:5, ja:3, de:2, ko:1, zh:1, lt:1, tl:1 |
| tiny | whisper-onnx | mixed | clean | forced:ru | 30 | 87.3% | 60.3% | 0.11 | ru:30 |
| tiny | whisper-onnx | mixed | clean | forced:kk | 30 | 102.8% | 65.9% | 0.07 | kk:30 |
| tiny | whisper-onnx | mixed | clean | auto | 30 | 86.2% | 64.1% | 0.04 | ru:26, tr:3, ja:1 |
| tiny | whisper-onnx | mixed | degraded | forced:ru | 30 | 84.5% | 55.1% | 0.12 | ru:30 |
| tiny | whisper-onnx | mixed | degraded | auto | 30 | 84.0% | 57.6% | 0.06 | ru:26, tr:1, pl:1, de:1, ja:1 |
| base | whisper-onnx | ru | clean | forced:ru | 32 | 33.6% | 10.9% | 0.20 | ru:32 |
| base | whisper-onnx | ru | clean | auto | 32 | 34.5% | 12.7% | 0.12 | ru:31, pl:1 |
| base | whisper-onnx | ru | degraded | forced:ru | 32 | 36.8% | 12.6% | 0.21 | ru:32 |
| base | whisper-onnx | kk | clean | forced:kk | 30 | 94.2% | 43.9% | 0.42 | kk:30 |
| base | whisper-onnx | kk | clean | auto | 30 | 108.0% | 91.2% | 0.18 | tr:17, ru:3, ar:3, ko:2, pl:1, az:1, ka:1, id:1, hy:1 |
| base | whisper-onnx | mixed | clean | forced:ru | 30 | 77.3% | 45.4% | 0.18 | ru:30 |
| base | whisper-onnx | mixed | clean | forced:kk | 30 | 91.7% | 44.4% | 0.14 | kk:30 |
| base | whisper-onnx | mixed | clean | auto | 30 | 80.1% | 54.8% | 0.09 | ru:24, tr:4, lt:1, ja:1 |
| base | whisper-onnx | mixed | degraded | forced:ru | 30 | 76.2% | 41.3% | 0.20 | ru:30 |
| base | whisper-onnx | mixed | degraded | auto | 30 | 75.7% | 45.6% | 0.12 | ru:27, tr:2, ja:1 |
| small | whisper-onnx | ru | clean | forced:ru | 32 | 20.2% | 6.9% | 0.54 | ru:32 |
| small | whisper-onnx | ru | clean | auto | 32 | 20.2% | 6.9% | 0.41 | ru:32 |
| small | whisper-onnx | ru | degraded | forced:ru | 32 | 25.4% | 8.7% | 0.53 | ru:32 |
| small | whisper-onnx | kk | clean | forced:kk | 30 | 78.8% | 29.4% | 1.15 | kk:30 |
| small | whisper-onnx | kk | clean | auto | 30 | 94.9% | 58.7% | 0.71 | kk:12, tr:7, ru:5, mn:1, nl:1, de:1, sq:1, lt:1, hy:1 |
| small | whisper-onnx | mixed | clean | forced:ru | 30 | 74.6% | 42.8% | 0.49 | ru:30 |
| small | whisper-onnx | mixed | clean | forced:kk | 30 | 101.1% | 62.5% | 0.56 | kk:30 |
| small | whisper-onnx | mixed | clean | auto | 30 | 74.0% | 47.5% | 0.35 | ru:26, tr:2, kk:1, bs:1 |
| small | whisper-onnx | mixed | degraded | forced:ru | 30 | 72.9% | 40.7% | 0.49 | ru:30 |
| small | whisper-onnx | mixed | degraded | auto | 30 | 71.3% | 41.5% | 0.34 | ru:27, kk:1, uk:1, tr:1 |
| medium | whisper-onnx | ru | clean | forced:ru | 32 | 18.2% | 8.2% | 1.98 | ru:32 |
| medium | whisper-onnx | ru | clean | auto | 32 | 18.2% | 8.2% | 1.63 | ru:32 |
| medium | whisper-onnx | ru | degraded | forced:ru | 32 | 16.6% | 5.5% | 2.12 | ru:32 |
| medium | whisper-onnx | kk | clean | forced:kk | 30 | 67.2% | 25.9% | 4.33 | kk:30 |
| medium | whisper-onnx | kk | clean | auto | 30 | 84.7% | 47.8% | 3.07 | kk:16, az:4, tr:3, ru:2, ar:1, uk:1, ur:1, en:1, ko:1 |
| medium | whisper-onnx | mixed | clean | forced:ru | 30 | 66.8% | 42.5% | 1.61 | ru:30 |
| medium | whisper-onnx | mixed | clean | forced:kk | 30 | 88.9% | 52.8% | 2.19 | kk:30 |
| medium | whisper-onnx | mixed | clean | auto | 30 | 66.8% | 41.0% | 1.45 | ru:20, kk:8, az:1, mk:1 |
| medium | whisper-onnx | mixed | degraded | forced:ru | 30 | 70.7% | 47.1% | 1.71 | ru:30 |
| medium | whisper-onnx | mixed | degraded | auto | 30 | 68.0% | 42.3% | 1.70 | ru:22, kk:8 |
| large-v3-turbo | whisper-onnx | ru | clean | forced:ru | 32 | 13.0% | 4.4% | 0.78 | ru:32 |
| large-v3-turbo | whisper-onnx | ru | clean | auto | 32 | 13.0% | 4.4% | 0.20 | ru:32 |
| large-v3-turbo | whisper-onnx | ru | degraded | forced:ru | 32 | 13.0% | 4.7% | 0.78 | ru:32 |
| large-v3-turbo | whisper-onnx | kk | clean | forced:kk | 30 | 42.3% | 10.5% | 1.61 | kk:30 |
| large-v3-turbo | whisper-onnx | kk | clean | auto | 30 | 42.3% | 10.5% | 0.44 | kk:30 |
| large-v3-turbo | whisper-onnx | mixed | clean | forced:ru | 30 | 56.9% | 32.0% | 0.87 | ru:30 |
| large-v3-turbo | whisper-onnx | mixed | clean | forced:kk | 30 | 69.1% | 40.0% | 0.28 | kk:30 |
| large-v3-turbo | whisper-onnx | mixed | clean | auto | 30 | 58.0% | 31.0% | 0.24 | kk:17, ru:13 |
| large-v3-turbo | whisper-onnx | mixed | degraded | forced:ru | 30 | 60.8% | 36.1% | 0.92 | ru:30 |
| large-v3-turbo | whisper-onnx | mixed | degraded | auto | 30 | 59.1% | 31.3% | 0.24 | ru:17, kk:13 |
| tiny | sherpa-onnx | ru | clean | forced:ru | 32 | 45.9% | 17.9% | 0.09 | ru:32 |
| tiny | sherpa-onnx | ru | clean | auto | 32 | 45.9% | 17.9% | 0.09 | ru:32 |
| tiny | sherpa-onnx | ru | degraded | forced:ru | 32 | 49.2% | 18.8% | 0.08 | ru:32 |
| tiny | sherpa-onnx | kk | clean | forced:kk | 30 | 97.1% | 68.5% | 0.11 | kk:30 |
| tiny | sherpa-onnx | kk | clean | auto | 30 | 107.3% | 91.7% | 0.11 | tr:13, ja:4, en:2, ru:2, lt:2, zh:1, de:1, ro:1, hr:1, ko:1, tl:1, az:1 |
| tiny | sherpa-onnx | mixed | clean | forced:ru | 30 | 74.6% | 39.3% | 0.08 | ru:30 |
| tiny | sherpa-onnx | mixed | clean | forced:kk | 30 | 90.1% | 47.3% | 0.09 | kk:30 |
| tiny | sherpa-onnx | mixed | clean | auto | 30 | 75.7% | 44.8% | 0.09 | ru:26, da:1, tr:1, de:1, sk:1 |
| tiny | sherpa-onnx | mixed | degraded | forced:ru | 30 | 78.5% | 42.4% | 0.09 | ru:30 |
| tiny | sherpa-onnx | mixed | degraded | auto | 30 | 86.2% | 47.2% | 0.09 | ru:27, tr:2, ja:1 |
| base | sherpa-onnx | ru | clean | forced:ru | 32 | 35.8% | 13.7% | 0.13 | ru:32 |
| base | sherpa-onnx | ru | clean | auto | 32 | 37.8% | 15.4% | 0.14 | ru:31, pl:1 |
| base | sherpa-onnx | ru | degraded | forced:ru | 32 | 32.6% | 11.3% | 0.14 | ru:32 |
| base | sherpa-onnx | kk | clean | forced:kk | 30 | 97.1% | 63.6% | 0.18 | kk:30 |
| base | sherpa-onnx | kk | clean | auto | 30 | 108.8% | 89.5% | 0.19 | tr:12, kk:4, en:2, mn:2, ko:1, pt:1, bo:1, de:1, ru:1, lt:1, ka:1, ar:1, id:1, hy:1 |
| base | sherpa-onnx | mixed | clean | forced:ru | 30 | 76.8% | 43.7% | 0.12 | ru:30 |
| base | sherpa-onnx | mixed | clean | forced:kk | 30 | 97.2% | 56.5% | 0.15 | kk:30 |
| base | sherpa-onnx | mixed | clean | auto | 30 | 76.8% | 51.5% | 0.13 | ru:23, tr:2, kk:1, lt:1, la:1, uk:1, ja:1 |
| base | sherpa-onnx | mixed | degraded | forced:ru | 30 | 75.7% | 40.5% | 0.12 | ru:30 |
| base | sherpa-onnx | mixed | degraded | auto | 30 | 80.1% | 46.8% | 0.13 | ru:24, tr:2, kk:1, lt:1, uk:1, ja:1 |
| small | sherpa-onnx | ru | clean | forced:ru | 32 | 23.1% | 7.9% | 0.41 | ru:32 |
| small | sherpa-onnx | ru | clean | auto | 32 | 23.1% | 7.9% | 0.43 | ru:32 |
| small | sherpa-onnx | ru | degraded | forced:ru | 32 | 25.1% | 8.7% | 0.41 | ru:32 |
| small | sherpa-onnx | kk | clean | forced:kk | 30 | 92.7% | 58.7% | 0.55 | kk:30 |
| small | sherpa-onnx | kk | clean | auto | 30 | 108.0% | 76.3% | 0.55 | kk:9, tr:7, ru:5, hy:2, az:2, ar:1, ms:1, fi:1, en:1, ps:1 |
| small | sherpa-onnx | mixed | clean | forced:ru | 30 | 71.3% | 39.1% | 0.35 | ru:30 |
| small | sherpa-onnx | mixed | clean | forced:kk | 30 | 92.3% | 63.6% | 0.43 | kk:30 |
| small | sherpa-onnx | mixed | clean | auto | 30 | 74.0% | 45.0% | 0.37 | ru:23, kk:3, lt:2, tr:1, uk:1 |
| small | sherpa-onnx | mixed | degraded | forced:ru | 30 | 72.9% | 41.5% | 0.33 | ru:30 |
| small | sherpa-onnx | mixed | degraded | auto | 30 | 74.0% | 48.0% | 0.35 | ru:25, lt:3, kk:1, tr:1 |
| medium | sherpa-onnx | ru | clean | forced:ru | 32 | 18.9% | 7.6% | 1.60 | ru:32 |
| medium | sherpa-onnx | ru | clean | auto | 32 | 18.9% | 7.6% | 1.72 | ru:32 |
| medium | sherpa-onnx | ru | degraded | forced:ru | 32 | 20.2% | 7.4% | 1.72 | ru:32 |
| medium | sherpa-onnx | kk | clean | forced:kk | 30 | 86.1% | 56.3% | 2.35 | kk:30 |
| medium | sherpa-onnx | kk | clean | auto | 30 | 89.8% | 66.1% | 2.16 | kk:21, az:7, tr:1, fi:1 |
| medium | sherpa-onnx | mixed | clean | forced:ru | 30 | 68.5% | 47.9% | 1.06 | ru:30 |
| medium | sherpa-onnx | mixed | clean | forced:kk | 30 | 90.1% | 59.2% | 1.67 | kk:30 |
| medium | sherpa-onnx | mixed | clean | auto | 30 | 68.0% | 44.5% | 1.33 | ru:18, kk:12 |
| medium | sherpa-onnx | mixed | degraded | forced:ru | 30 | 70.2% | 46.8% | 1.20 | ru:30 |
| medium | sherpa-onnx | mixed | degraded | auto | 30 | 66.8% | 44.2% | 1.55 | ru:19, kk:11 |

## Распределение определённого языка на mixed (auto-detect, чистое аудио)

| Модель | Определённые языки (код:число реплик) |
|---|---|
| tiny (whisper-onnx) | ru:26, tr:3, ja:1 |
| base (whisper-onnx) | ru:24, tr:4, lt:1, ja:1 |
| small (whisper-onnx) | ru:26, tr:2, kk:1, bs:1 |
| medium (whisper-onnx) | ru:20, kk:8, az:1, mk:1 |
| large-v3-turbo (whisper-onnx) | kk:17, ru:13 |
| tiny (sherpa-onnx) | ru:26, da:1, tr:1, de:1, sk:1 |
| base (sherpa-onnx) | ru:23, tr:2, kk:1, lt:1, la:1, uk:1, ja:1 |
| small (sherpa-onnx) | ru:23, kk:3, lt:2, tr:1, uk:1 |
| medium (sherpa-onnx) | ru:18, kk:12 |

> Оговорка о backend `sherpa-onnx`: его Python API возвращает пустые строки для байтовых BPE-токенов, поэтому казахские буквы ұ ө ә ң ғ ү һ (часто и қ) выпадают из гипотез — WER/CER по kk и mixed у этого backend завышены. Backend `whisper-onnx` (те же ONNX-веса, собственный байтовый декодер) этой проблемы не имеет.
