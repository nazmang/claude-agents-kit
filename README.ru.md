# claude-agents-kit

**Целая команда долгоживущих сессий [Claude Code](https://claude.com/claude-code) на одной машине, и всё под присмотром.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Claude Code mods](https://img.shields.io/badge/Claude%20Code-mods-D97757)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)

[English](README.md)

Команда агентов в tmux и несколько вкладок проектов открыты сутками. Этот
набор даёт:

- **`agents`**: одна таблица всех живых сессий на машине (занята или
  простаивает, сколько осталось кэшу, размер контекста, модель, окно tmux) и
  `up` / `down`, чтобы поднимать и закрывать сессии проекта в tmux, не теряя кэш.
- **agents-panel**: строка над полем ввода с теми, кто сейчас работает, и
  панель `/team`, где каждая сессия — пиксельная фигурка в цвете своей модели.
- **push-guard**: не даёт сделать `git push` в ветку, чей PR уже влит, и
  сначала спрашивает *тебя*.

Уход за кэшем промптов (сжатие простаивающих сессий до того, как остынет кэш)
живёт в соседнем репозитории
[claude-cache-keeper](https://github.com/nazmang/claude-cache-keeper). Вместе они
работают хорошо, но друг без друга обходятся.

## Что внутри

| часть | что делает | нужно |
|---|---|---|
| `bin/agents` | `agents status`: все живые сессии на машине — статус, простой, кэш, контекст, модель, окно tmux. `agents up/down`: поднять и закрыть сессии проекта в tmux. `agents messages`: сообщения между сессиями. | Python 3, tmux |
| `plugins/push-guard/` (**мод push-guard**) | Не даёт сделать `git push` в ветку, чей PR уже влит (коммиты не дойдут до базовой ветки), и спрашивает **тебя** в диалоге. Пушит только после твоего «да», иначе агенту велено завести новую ветку. Обхода, который модель могла бы вписать в команду сама, нет. Нет ответа за `PUSH_GUARD_ASK_MIN` минут (10) — отказ, так что агент без присмотра не зависнет на вопросе; уведомление на рабочем столе скажет, что агент ждёт. Без `gh` и вне GitHub пропускает. | `gh` с логином |
| `plugins/agents-panel/` (**мод agents-panel**) | Одна строка над полем ввода: кто сейчас работает, сколько сессий простаивают и у скольких из них остыл кэш (💤), последнее сообщение между сессиями и сколько пунктов чек-листа ждут тебя. `/team` открывает и закрывает панель: каждая живая сессия — пиксельная девочка или мальчик (`people.conf`) в цвете модели (Haiku синий, Sonnet янтарный, Opus фиолетовый, Fable зелёный), что каждый делает, последние сообщения; клик по имени (или 1–9) — переход в её окно tmux. `/checklist` — вкладка только для чтения: открытые пункты по проектам, твои сверху. | `bin/agents` в `PATH` |

## Быстрый старт

```bash
git clone https://github.com/nazmang/claude-agents-kit ~/src/claude-agents-kit
~/src/claude-agents-kit/install.sh

# по желанию: примеры конфигов, чтобы отредактировать
mkdir -p ~/.config/agents
cp -n ~/src/claude-agents-kit/examples/*.conf ~/.config/agents/
```

Установщик выпускает релиз (`release.sh`) и делает из него ссылку `agents` в
`~/.local/bin`. `settings.json` он **не** трогает, моды добавь сам (замени
`/home/you`):

```jsonc
// ~/.claude/settings.json
{
  // несколько плагинов: через двоеточие (запятая не работает)
  "env": { "CLAUDE_CODE_PLUGIN_DIRS": "/home/you/.local/share/claude-agents-kit/current/plugins/push-guard:/home/you/.local/share/claude-agents-kit/current/plugins/agents-panel" }
}
```

Если стоит и claude-cache-keeper, все три пути — в одну переменную.

> [!IMPORTANT]
> - **Уже запущенные сессии мод не подхватят**, даже по `/reload-plugins`:
>   список каталогов плагинов читается при старте процесса. Перезапусти их
>   (сначала `/compact`!).
> - Переменную задавай в `settings.json`, не в shell: из окружения она не
>   подхватилась (Claude Code 2.1.296).

### Что нужно

- Linux: `agents` читает `/proc`, `release.sh` использует GNU `mv -T`
- Python 3 и tmux
- Claude Code с поддержкой модов (проверено на 2.1.296 и 2.1.318)
- `gh` с логином — для push-guard
- `agents compact` и `down` находят поле ввода по приглашению `❯`
  стандартного терминального интерфейса Claude Code

## Использование

```text
$ agents status
session                      status      idle   cache   context  model / window
Coordinator                  idle         34m     26m   compact  sonnet-5-5 / team:Coordinator
Reviewer                     idle        114m expired      191k  opus-5-5 / team:Reviewer
webapp-dev                   busy          0m     60m       77k  opus-5-5 / no tmux
```

- `context` = `compact` — после сжатия ничего не добавилось.
- `agents compact <имя> --wait` — сжать сейчас и дождаться нового
  `compact_boundary` в транскрипте.
- `agents up|down <проект> [имена…]` — поднять/закрыть сессии проекта в tmux.
  - Простые сессии — в `~/.config/agents/sessions.conf`
    (`проект | имя | каталог | модель | id сессии [| доп. аргументы]`): окно на
    строку, `claude -n <имя> --resume <id>`; без id — новая сессия, id допишется
    сам. Уже запущенные (в tmux или во вкладке терминала) не трогает. `down`
    сначала сжимает, потом `/exit`; занятые и с черновиком в поле ввода пропускает.
  - Команды со своими скриптами — в `~/.config/agents/projects.conf`
    (`проект | каталог | команда up | команда down`).
  - Перенос вкладки в tmux: во вкладке `/compact`, `/exit`, затем `agents up <проект>`.
  - `#` начинает комментарий в начале строки или после пробела, вне кавычек:
    `--append-system-prompt "fix issue #5"` остаётся как есть.
- `agents down <проект> --no-compact` — закрыть без сжатия: если кэш уже остыл,
  сжатие само перечитает весь контекст по полной цене. `agents up <проект>
  --fresh` — новая сессия вместо сохранённого id (например, ночной перезапуск
  дежурного с чистого листа). 6-й столбец `sessions.conf` — доп. аргументы
  `claude` для этой сессии.
- Сессия, у которой работает только фоновый shell или монитор, видна как
  `shell`; она считается свободной, и `down` сам отвечает на диалог
  «Background work is running».
- `agents messages [--hours H]` — сообщения между сессиями (SendMessage);
  ❄ — сообщение разбудило получателя с остывшим кэшем.
- Лог сжатий, которые делает `agents`: `~/.local/state/agents/compact.log`.

### Данные agents-panel

У мода нет доступа к файлам, поэтому он спрашивает `agents status --json
--max-age 5`; ответ кэшируется, и все сессии вместе пересчитывают его не чаще
раза в 5 с.

- `~/.config/agents/people.conf` — `имя сессии | отображаемое имя | f|m|-|x`
  (девочка, мальчик, кто угодно, скрыть; см. `examples/`).
- Чек-лист: по JSON на пункт в `~/.local/state/agents/checklist/items/`
  (`{id, data: {project, title, who, status}}`) — например, выгрузка базы
  артефакта Claude. Открытые (`status: todo`), у которых `who` из `OWNER_WHO`
  (по умолчанию `owner,together,you` и русские близнецы), считаются ждущими тебя.

### Окно-табло

Запусти одну дешёвую сессию (любая модель; запросов она не делает) с
`AGENTS_PANEL_OPEN=1` в `env` своего файла настроек
(`claude --settings ~/.config/agents/panel-settings.json`) — она сама откроет
`/team` и `/checklist`. Пометь её `x` в `people.conf`, чтобы не попадала в
список агентов. Сбоку от поля ввода панели встают только в достаточно широком
терминале, иначе — над ним. Пиксельные фигурки (`Raster`) рисует только
терминальный интерфейс.

### Настройки

| переменная | по умолчанию | что делает |
|---|---|---|
| `MIN_CTX` | `60000` | `agents down`: контекст меньше не сжимать |
| `OWNER_WHO` | `owner,together,you` + русские близнецы | чек-лист: чьи пункты ждут тебя |
| `PUSH_GUARD_ASK_MIN` | `10` | push-guard: сколько минут ждать твоего ответа |
| `AGENTS_PANEL_OPEN` | не задана | agents-panel: `1` открывает `/team` и `/checklist` при старте |
| `AGENTS_LANG` | по локали | `ru` или `en` для всего, что читает человек |
| `DEST` | `~/.local/share/claude-agents-kit` | `release.sh`, `install.sh`: куда кладутся релизы |
| `BIN` | `~/.local/bin` | `install.sh`: куда ставится ссылка `agents` |
| `SKIP_PLUGIN_CHECKS` | не задана | `release.sh`: `1` — выпустить без `claude` в `PATH` (без validate/test) |

Если `AGENTS_LANG` не задан, решает первое непустое из `LC_ALL`, `LC_MESSAGES`,
`LANG`: начинается с `ru` — по-русски, иначе по-английски. `status --json` и
текст, который получает модель, не переводятся.

## Безопасность

- `agents compact` и `down` пишут в окно tmux, только если сессия `idle` (или
  `shell`) **и** поле ввода пустое (видно на экране после приглашения `❯`).
  Сессии вне tmux не трогают.
- `down` называет сессию закрытой, только когда её процесс завершился; если
  через 30 с после `/exit` он ещё жив, об этом будет сказано, а код выхода — 1.
- push-guard только спрашивает, сам ничего не пушит.

## Разработка

### Релизы: не грузить моды из рабочей папки

Запущенные сессии перечитывают мод при каждом изменении его файлов. Если
настройки смотрят в рабочую копию, каждая недописанная правка сразу попадает
во всех агентов. Поэтому:

- `./release.sh` отказывает при незакоммиченных и неотслеживаемых файлах,
  запускает `claude plugin validate` и `claude plugin test` для всех плагинов
  (без `claude` отказывается работать), копирует закоммиченные `bin/` и
  `plugins/` в `~/.local/share/claude-agents-kit/releases/<sha>` и атомарно
  переключает `current`. Повторный выпуск того же коммита только переключает на
  него. Хранятся последние 5 релизов.
- `./release.sh --list` — список релизов, `./release.sh --rollback <sha>` — откат.
- Настройки и `~/.local/bin/agents` смотрят в
  `~/.local/share/claude-agents-kit/current/...`, никогда не в репозиторий.
- Разработка — в отдельном worktree (`git worktree add ../claude-agents-kit-dev -b dev`),
  слить в `main`, потом выпустить.

### Тесты

```bash
claude --plugin-dir plugins/agents-panel   # один раз, интерактивно: генерирует .claude-plugin/types
for p in plugins/*; do claude plugin validate $p && claude plugin test $p; done
```

### Грабли, на которые уже наступили

- Конец `/compact` нельзя определять по экрану: там может висеть старая
  надпись `Compacted`. `agents compact --wait` смотрит новый `compact_boundary`
  в транскрипте.
- Перед перезапуском с `--resume` — сначала `/compact`, иначе весь несжатый
  хвост снова уйдёт в кэш.
- Последний `usage` в транскрипте завышает контекст, если после него было сжатие.
- dev- и release-копии `agents` пишут один кэш `status.json` — проверять мод
  после выпуска, а не из dev.

## Удаление

```bash
rm ~/.local/bin/agents
```

Убери два пути модов из `CLAUDE_CODE_PLUGIN_DIRS` в `settings.json` и
перезапусти сессии. После этого можно удалить `~/.local/share/claude-agents-kit/`,
`~/.config/agents/` и `~/.local/state/agents/`.

<details>
<summary>Переход с claude-cache-keeper (до октября 2026)</summary>

`agents`, agents-panel и push-guard раньше лежали в
[claude-cache-keeper](https://github.com/nazmang/claude-cache-keeper); их история
перенесена сюда. Если ты ставил их оттуда, настройки и `~/.local/bin/agents` всё
ещё смотрят в `~/.local/share/claude-cache-keeper/`:

1. Запусти `install.sh` из этого репозитория: он перевесит `~/.local/bin/agents`.
2. В `CLAUDE_CODE_PLUGIN_DIRS` замени пути `push-guard` и `agents-panel` на
   пути из `~/.local/share/claude-agents-kit/current/plugins/`.
3. Обнови и claude-cache-keeper: в его новых релизах этих модов нет.
4. Перезапусти сессии (сначала `/compact`).

`~/.config/agents/` и `~/.local/state/agents/` остаются как есть.

</details>

## Лицензия

[MIT](LICENSE)
