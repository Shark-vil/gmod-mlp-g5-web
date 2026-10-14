// The site's languages: Russian (the pages are written in it) and English. Loaded in <head> after theme.js.
// The first visit takes the browser's language (the first of navigator.languages that's ru / en), English when
// none of them is; the visitor's own choice is kept like the theme (only with "remember settings" allowed).
// The pages stay plain Russian HTML / JS: this file translates the document's text and the title / placeholder /
// aria-label / alt attributes as they appear (a MutationObserver), so dynamic text is covered too, and switching is
// instant. Text with numbers or names in it: patterns ({n} a number, {0} anything). Blocks with markup inside:
// data-i18n="id" -> "#id" below. Dialogs and canvas text: tx("Русский текст {0}", value).
//   window.mlpLang() -> "ru" | "en";  window.mlpSetLang(l);  window.tx(key, ...args);  window.mlpLocale()
(function () {
  const LANGS = ["ru", "en"], KEY = "mlpLang";
  const EN = {
    // ---------------------------------------------------------------- shared: top bar, navigation, login, cookies
    "Рекорды": "Leaderboard", "Песни": "Songs", "Мои песни": "My songs", "Моя статистика": "My stats",
    "Войти через Steam": "Sign in with Steam", "Выйти": "Sign out", "админка": "admin", "вход не удался": "sign-in failed",
    "Вход через Steam": "Sign in with Steam",
    "Вход проходит на сайте Steam (steamcommunity.com). Пароль вы вводите только там — нам он не виден.": "You sign in on Steam's own site (steamcommunity.com). You type your password only there — we never see it.",
    "Steam сообщает нам только ваш SteamID64 — публичный номер профиля (тот, что в ссылке на профиль).": "Steam tells us only your SteamID64 — the public number of your profile (the one in your profile link).",
    "С публичной страницы профиля мы берём ник и аватар, чтобы показывать вас автором песен.": "From your public profile page we take your name and avatar, to show you as the author of your songs.",
    "Храним: SteamID64, ник, аватар, ваши песни и время входа. Вход запоминается в cookie этого сайта на 30 дней.": "We keep: SteamID64, name, avatar, your songs and when you signed in. The sign-in is remembered in this site's cookie for 30 days.",
    "Не получаем и не храним: пароль, почту, телефон, друзей, инвентарь, список игр. Ничего не публикуем от вашего имени. Выйти можно в любой момент кнопкой «Выйти».": "We don't get or keep: your password, e-mail, phone, friends, inventory, games list. We never post anything as you. Sign out at any time with «Sign out».",
    "Отмена": "Cancel", "Закрыть": "Close", "Назад": "Back",
    "Светлая тема": "Light theme", "Тёмная тема": "Dark theme",
    "Светлая тема (не запомнится: сохранение настроек выключено в «Cookie»)": "Light theme (won't be remembered: saving settings is off in «Cookies»)",
    "Тёмная тема (не запомнится: сохранение настроек выключено в «Cookie»)": "Dark theme (won't be remembered: saving settings is off in «Cookies»)",
    "Cookie и настройки": "Cookies and settings", "Для входа нужен cookie": "Signing in needs a cookie",
    "Вход через Steam запоминается в cookie этого сайта. Без него войти нельзя. Разрешите его — и продолжим вход.": "Signing in with Steam is remembered in this site's cookie. Without it you can't sign in. Allow it and we'll go on.",
    "Здесь нет рекламы, аналитики и чужих трекеров. Используется только то, без чего не работают функции сайта:": "No ads, no analytics, no third-party trackers here. Only what the site's features can't work without:",
    "Cookie mlp_session на 30 дней — помнит, что вы вошли. Только для этого сайта.": "The mlp_session cookie for 30 days — remembers that you signed in. For this site only.",
    "Без него: не получится войти через Steam — не будет «Мои песни», загрузки и редактора песен, «Моей статистики». Если вы уже вошли — выйдете.": "Without it: you can't sign in with Steam — no «My songs», no song uploads or editor, no «My stats». If you're signed in, you'll be signed out.",
    "Запоминать настройки": "Remember settings",
    "Память браузера (localStorage): язык, тема, режим редактора нот, вид списков. Никуда не отправляется.": "The browser's storage (localStorage): language, theme, the note editor's mode, list views. Never sent anywhere.",
    "Без этого: язык, тема, режим редактора и вид списков не запомнятся — при каждом открытии снова как в системе и по умолчанию.": "Without it: language, theme, the editor's mode and list views won't be remembered — every visit starts from your system's and the defaults again.",
    "Рекорды и список песен открыты всем и работают без cookie. Передумать можно в любой момент — «Cookie и настройки» внизу страницы.": "The leaderboard and the songs list are open to everyone and work without cookies. Change your mind any time — «Cookies and settings» at the bottom of the page.",
    "Отказаться от всего": "Refuse all", "Сохранить выбор": "Save my choice", "Принять все": "Accept all", "Разрешить и войти": "Allow and sign in",
    "Без cookie вы потеряете": "Without cookies you lose", "Без части cookie вы потеряете": "Without some of the cookies you lose",
    "Не получится войти через Steam: не будет «Мои песни», загрузки и редактора песен, «Моей статистики». Если вы уже вошли — выйдете.": "You can't sign in with Steam: no «My songs», no song uploads or editor, no «My stats». If you're signed in, you'll be signed out.",
    "Язык, тема, режим редактора и вид списков не запомнятся: при каждом открытии — снова как в системе и по умолчанию.": "Language, theme, the editor's mode and list views won't be remembered: every visit starts from your system's and the defaults again.",
    "Останется: рекорды, профили игроков, список песен и их прослушивание. Ваш выбор запомнится, чтобы не спрашивать снова.": "You keep: the leaderboard, player profiles, the songs list and listening to them. Your choice is remembered so we don't ask again.",
    "Всё равно отказаться": "Refuse anyway",
    "Язык: English": "Language: English", "Язык: русский": "Language: Russian",
    "Язык: English (не запомнится: сохранение настроек выключено в «Cookie»)": "Language: English (won't be remembered: saving settings is off in «Cookies»)",
    "Язык: русский (не запомнится: сохранение настроек выключено в «Cookie»)": "Language: Russian (won't be remembered: saving settings is off in «Cookies»)",
    "Загрузка…": "Loading…", "Загружается…": "Loading…", "Сохранить": "Save", "Удалить": "Delete", "Поиск": "Search",
    "Название": "Title", "Исполнитель": "Artist", "Длина": "Length", "Обложка": "Cover", "Мод в Мастерской": "Workshop mod",
    "Ничего не нашлось": "Nothing found", "автор": "by", "черновик": "draft", "мод собран": "mod built", "на проверке": "in review",
    "одобрена": "approved", "отклонена": "rejected", "Не получилось: {0}": "Didn't work: {0}", "{n} нот": "{n} notes",

    // ---------------------------------------------------------------- the leaderboard + stats (index, me, stats.js)
    "Maretime Bay — рекорды": "Maretime Bay — leaderboard", "Maretime Bay · рекорды": "Maretime Bay · leaderboard",
    "Категория": "Category", "Показатель": "Stat", "Найти пони по нику": "Find a pony by name", "Поиск по нику": "Search by name",
    "Всё открывается только игрой. Никакого доната. Нажмите на игрока — его статистика.": "Everything is unlocked by playing only. No donations. Click a player for their stats.",
    "Сервер статистики недоступен": "The stats server is unavailable", "Никого с таким ником": "Nobody with that name", "Пока пусто": "Empty so far",
    "Моя статистика — Maretime Bay": "My stats — Maretime Bay", "Здесь ваша статистика из игры.": "Your stats from the game are here.",
    "Профиль Steam": "Steam profile", "Статистика": "Stats",
    "Статистики пока нет: сыграйте на сервере с модом (сайт получает её с серверов).": "No stats yet: play on a server with the mod (the site gets them from the servers).",
    "Общее": "General", "Звёзды за всё время": "Stars, all time", "Коллекция": "Collection", "Заклинания": "Spells", "Время в игре": "Time played",
    "Танец": "Dance", "Лучший счёт": "Best score", "Победы": "Wins", "Золотые медали": "Gold medals", "Сыграно": "Played",
    "Гонка в небе": "Sky race", "Лучшее время": "Best time", "Гонка на роликах": "Roller race", "Таран": "Rampage",
    "Сбито пони-тараном": "Knocked over by the pony ram", "Против игроков": "Against players", "Победы, всего": "Wins, total",
    "Победы в танце": "Dance wins", "Победы в небе": "Sky race wins", "Победы на роликах": "Roller race wins", "Победы в таране": "Rampage wins",
    "Против ботов": "Against bots", "Над мастером": "Over a master", "Над опытным": "Over a skilled one", "Над любителем": "Over a casual one",
    "Над новичком": "Over a rookie", "Санни": "Sunny", "Иззи": "Izzy", "Хитч": "Hitch", "Пипп": "Pipp", "Зипп": "Zipp",
    "За 30 дней": "Last 30 days", "Время в игре по дням, мин": "Time played per day, min", "Пока ничего": "Nothing yet",
    "Играл(а) за": "Played as", "Не получилось загрузить": "Couldn't load", "{n} ч": "{n} h", "{n} мин": "{n} min",

    // ---------------------------------------------------------------- songs (songs.html)
    "Песни для «Танца» — Maretime Bay": "Songs for «Dance» — Maretime Bay", "Maretime Bay · песни": "Maretime Bay · songs",
    "Найти песню, исполнителя или автора": "Find a song, artist or author", "Поиск песен": "Search songs",
    "Песен пока нет — загрузите первую в «Мои песни»": "No songs yet — upload the first one in «My songs»",
    "Песни для мини-игры «Танец». Их делают игроки в редакторе (вкладка «Мои песни») и выкладывают модом в Мастерскую; в игре песню выбирают голосованием в лобби, мод скачивается сам. Здесь звучит только фрагмент.": "Songs for the «Dance» minigame. Players make them in the editor («My songs») and put them on the Workshop as a mod; in the game a song is picked by a vote in the lobby and its mod downloads by itself. Only a fragment plays here.",
    "Показать ещё": "Show more", "← Все песни": "← All songs", "Такой песни нет (или она ещё не опубликована).": "No such song (or it's not published yet).",
    "Песня ещё не опубликована.": "The song isn't published yet.", "Открыть в редакторе": "Open in the editor",
    "В игре: зона «Танец» → голосование в лобби. Если мода песни у вас нет, игра скачает его сама и сверит с сайтом.": "In the game: the «Dance» zone → the lobby's vote. If you don't have the song's mod, the game downloads it and checks it against the site.",
    "Нот": "Notes", "Удержаний": "Holds", "Версия": "Version", "Опубликована": "Published", "{0} — песни Maretime Bay": "{0} — Maretime Bay songs",

    // ---------------------------------------------------------------- my songs (my-songs.html)
    "Мои песни — Maretime Bay": "My songs — Maretime Bay", "Мои песни для «Танца»": "My songs for «Dance»",
    "Здесь ваши песни для «Танца»: загрузка, редактор нот, сборка мода.": "Your songs for «Dance» are here: upload, the note editor, building the mod.",
    "Нужно войти через Steam": "You need to sign in with Steam", "Песен пока нет — загрузите первую ниже.": "No songs yet — upload the first one below.",
    "Название песни": "Song title", "Исполнитель (необязательно)": "Artist (optional)", "Файл песни": "Song file",
    "Загрузить и открыть редактор": "Upload and open the editor",
    "до 40 МБ, 20 с — 5 мин; ноты ставите сами в редакторе (или кнопкой «Расставить автоматически»)": "up to 40 MB, 20 s — 5 min; you place the notes in the editor (or with «Place automatically»)",
    "Загруженные песни хранятся на сайте: их можно открыть и поправить в любой момент без повторной загрузки.": "Uploaded songs are kept on the site: open and fix them any time without uploading again.",
    "Редактор": "Editor", "обрабатывается…": "processing…", "не удалось обработать": "couldn't process", "опубликована v{n}": "published v{n}",
    "Файл больше 40 МБ": "The file is over 40 MB", "Файл слишком большой": "The file is too big", "Нет связи с сайтом": "No connection to the site",
    "Слишком много загрузок, попробуйте позже": "Too many uploads, try later",
    "Слишком много черновиков — закончите или удалите один": "Too many drafts — finish or delete one",
    "Удалить песню «{0}» вместе со звуком?": "Delete the song «{0}» together with its sound?",

    // ---------------------------------------------------------------- admin (admin.html)
    "Админка — Maretime Bay": "Admin — Maretime Bay", "Maretime Bay · админка": "Maretime Bay · admin", "Админка": "Admin",
    "Админка — после входа.": "Admin — after signing in.", "Это страница админов сайта.": "This is the site admins' page.",
    "На проверке": "In review", "Все песни": "All songs", "Запрещённые слова": "Banned words", "Очередь пуста": "The queue is empty",
    "Название, исполнитель, автор, SteamID, uuid, номер мода": "Title, artist, author, SteamID, uuid, mod number",
    "Статус": "Status", "все статусы": "all statuses", "Ноты в редакторе": "Notes in the editor", "Мод (.zip)": "Mod (.zip)",
    "Одобрить": "Approve", "Отклонить": "Reject", "Забанить автора": "Ban the author", "Разбанить автора": "Unban the author",
    "автор забанен": "author banned", "скрыта словом": "hidden by a word", "в игре v{n}": "in game v{n}", "мод {n}": "mod {n}",
    "изменена": "edited", "причина:": "reason:",
    "Песни, в названии или исполнителе которых есть такое слово, скрываются из списка и из игры (не удаляются).": "Songs with such a word in the title or artist are hidden from the list and the game (not deleted).",
    "по строкам": "by lines", "через запятую": "comma-separated", "по одному": "one by one", "по одному слову на строку": "one word per line",
    "слово, затем Enter или «Добавить»": "a word, then Enter or «Add»", "Добавить": "Add", "убрать": "remove", "Загрузить .txt": "Upload .txt",
    "Добавить строки из .txt (дубликаты уберутся)": "Add lines from a .txt (duplicates are dropped)",
    "Добавлено: {n}": "Added: {n}", "Уже есть в списке": "Already in the list", "Файл пуст": "The file is empty",
    "Сохранено: {n} слов(а).": "Saved: {n} word(s).",
    "Причина (её увидит автор):": "Reason (the author will see it):", "Причина бана автора {0}:": "Reason for banning {0}:",
    "Удалить песню «{0}» насовсем (вместе со звуком)?": "Delete the song «{0}» for good (with its sound)?",

    // ---------------------------------------------------------------- the song editor (editor.html)
    "Редактор песни — Maretime Bay": "Song editor — Maretime Bay", "Maretime Bay · редактор песни": "Maretime Bay · song editor",
    "Редактор — после входа.": "The editor — after signing in.", "Песня не найдена (или это не ваша песня).": "Song not found (or it isn't yours).",
    "Песня обрабатывается: темп, сетка долей, фрагмент… (страница обновится сама)": "Processing the song: tempo, beat grid, fragment… (the page updates by itself)",
    "Полного трека нет на сайте — редактировать нельзя.": "The full track isn't on the site — it can't be edited.",
    "Загружается звук…": "Loading the sound…", "Загружается обрезанная песня…": "Loading the trimmed song…",
    "Не получилось загрузить песню: {0}": "Couldn't load the song: {0}", "Дорожка нот": "Note lane", "Прокрутка": "Scroll",
    "● Запись": "● Record", "■ Стоп (Esc)": "■ Stop (Esc)", "▶ Играть": "▶ Play", "❚❚ Пауза": "❚❚ Pause",
    "Live-запись: песня играет, вы жмёте клавиши в такт": "Live recording: the song plays, you press keys to the beat",
    "Масштаб": "Zoom", "Скорость": "Speed", "Метроном": "Metronome", "Привязка": "Snap", "нет": "off", "к удару": "to beat",
    "½ удара": "½ beat", "¼ удара": "¼ beat",
    "Медленнее — проще попасть в такт (звук ниже). На «Проверить» не действует": "Slower — easier to hit the beat (the sound is lower). Doesn't affect «Test»",
    "Щелчки на каждом ударе во время игры (M)": "A click on every beat while playing (M)",
    "Удержание": "Hold", "Удалить ноту": "Delete note", "↶ Отменить": "↶ Undo",
    "Расставить автоматически": "Place automatically", "Очистить все ноты": "Clear all notes", "Убрать все ноты": "Remove all notes",
    "Проверить": "Test", "Что меняют кнопки направления": "What the direction buttons change",
    "B — переключить": "B — switch", "🖌 Кисть: новые ноты": "🖌 Brush: new notes", "✎ Правка: выбранные ноты": "✎ Edit: picked notes",
    "Вверх (1)": "Up (1)", "Вниз (2)": "Down (2)", "Влево (3)": "Left (3)", "Вправо (4)": "Right (4)",
    "Действие: Пробел / E (5)": "Action: Space / E (5)", "Случайно: в каждой игре своя клавиша (0)": "Random: a different key every game (0)",
    "Зеркало выбранных: ← ↔ →, ↑ ↔ ↓ (X)": "Mirror the picked: ← ↔ →, ↑ ↔ ↓ (X)", "⇄ Зеркало": "⇄ Mirror",
    "Ноты на каждом шаге привязки между первой и последней выбранной (F)": "A note on every snap step between the first and the last picked (F)",
    "⋯ Заполнить по сетке": "⋯ Fill by grid",
    "Фрагмент для прослушивания": "Preview fragment", "Его услышат все в общем списке песен.": "Everyone hears it in the songs list.",
    "Начало, с": "Start, s", "с": "s", "С текущего места": "From here", "▶ Фрагмент": "▶ Fragment",
    "На дорожке он подсвечен сверху.": "It's highlighted at the top of the lane.",
    "Обложка песни": "Song cover", "Загрузить свою": "Upload your own", "Убрать свою": "Remove your own",
    "jpg / png / webp до 5 МБ, обрежется в квадрат 512×512. Она же — значок мода в Мастерской.": "jpg / png / webp up to 5 MB, cropped to a 512×512 square. It's also the mod's Workshop icon.",
    "Ваша обложка.": "Your cover.", "Обложка из аудиофайла.": "The cover from the audio file.", "Обложки нет — показывается заглушка.": "No cover — a placeholder is shown.",
    "загружается обложка…": "uploading the cover…", "обложка сохранена": "cover saved", "своя обложка убрана": "your cover removed",
    "картинка больше 5 МБ": "the picture is over 5 MB", "обложка не загрузилась: {0}": "the cover didn't upload: {0}",
    "Live-запись": "Live recording", "заменить ноты в записанном отрезке": "replace the notes in the recorded stretch", "Поправка, мс": "Offset, ms",
    "Если ноты встают позже звука — поставьте, например, −60": "If the notes land after the sound, set e.g. −60",
    "Привязка (к удару / ½ / ¼) действует и на запись.": "Snapping (to beat / ½ / ¼) applies to recording too.",
    "Обрезка песни": "Trim the song",
    "Оставьте нужный кусок загруженной песни (не меньше 20 с). Ноты сдвинутся вместе с ним, ноты вне куска уберутся. Исходная песня хранится — обрезку можно поменять или вернуть всю песню.": "Keep the part of the uploaded song you need (20 s at least). The notes move with it, notes outside it go. The original is kept — change the trim or bring the whole song back any time.",
    "Конец, с": "End, s", "По текущее место": "Up to here", "Обрезать": "Trim", "Вся песня": "Whole song",
    "обрезается…": "trimming…", "обрезано — осталось {n} нот": "trimmed — {n} notes left", "нужно оставить хотя бы 20 с": "keep 20 s at least",
    "не обрезалось: {0}": "not trimmed: {0}", "обрезано: {0} — {1} из {2}": "trimmed: {0} — {1} of {2}", "вся песня ({0})": "whole song ({0})",
    "Оставить {0} — {1}? Ноты вне этого куска уберутся.": "Keep {0} — {1}? Notes outside it will go.",
    "Вернуть всю песню? Ноты останутся на своих местах.": "Bring the whole song back? The notes stay where they are.",
    "Как править": "How to edit",
    "есть несохранённые изменения": "unsaved changes", "сохранено": "saved", "сохраняется…": "saving…", "только просмотр": "view only",
    "не сохранилось: {0}": "not saved: {0}", "исправьте отмеченные ⛔ ноты": "fix the notes marked ⛔",
    "расставляется…": "placing…", "не получилось": "didn't work", "не получилось: {0}": "didn't work: {0}",
    "Расставить ноты автоматически?": "Place the notes automatically?",
    "Расставить ноты автоматически? Текущие ноты заменятся (их можно вернуть «Отменить»).": "Place the notes automatically? The current notes will be replaced (bring them back with «Undo»).",
    "Вы уверены? Все ноты ({0}) будут удалены. Вернуть можно кнопкой «Отменить», пока страница открыта.": "Are you sure? All the notes ({0}) will be deleted. «Undo» brings them back while the page is open.",
    "все ноты удалены — не забудьте сохранить": "all notes deleted — don't forget to save",
    "у удержания любая клавиша": "a hold takes any key", "выберите ноту (или несколько — Shift)": "pick a note (or several — Shift)",
    "выберите ноту, потом направление": "pick a note, then a direction", "выберите ноты (Shift+протянуть)": "pick notes (Shift+drag)",
    "выберите отрезок: Shift+протянуть или две ноты Shift+кликом": "pick a stretch: Shift+drag, or two notes with Shift+click",
    "в выбранных нет стрелок": "no arrows among the picked", "свободных мест на сетке между ними нет": "no free grid places between them",
    "выбрано нот: {n}": "notes picked: {n}", "вставлено нот: {n}": "notes pasted: {n}", "добавлено нот: {n}": "notes added: {n}",
    "отзеркалено нот: {n}": "notes mirrored: {n}", "скопировано нот: {n} — Ctrl+V вставит их с места курсора": "notes copied: {n} — Ctrl+V pastes them at the cursor",
    "кисть {0} — новые ноты получат это направление": "brush {0} — new notes get this direction",
    "кисть {0}, перекрашено нот: {n}": "brush {0}, notes repainted: {n}",
    "ставьте ноты — каждая получит {0} · выбрано {n}: кнопка перекрасит их": "place notes — each gets {0} · {n} picked: a button repaints them",
    "ставьте ноты — каждая получит {0}": "place notes — each gets {0}",
    "метроном включён": "metronome on", "метроном выключен": "metronome off",
    "записано {n} нот — проверьте ⛔ / ⚠ и сохраните": "recorded {n} notes — check ⛔ / ⚠ and save", "0 нот": "0 notes",
    "{0} · {1} BPM · {n} нот, {n} удерж.": "{0} · {1} BPM · {2} notes, {3} holds",
    "ноты {n} и {n} ближе {0} с ({1}) — так нельзя": "notes {0} and {1} are closer than {2} s ({3}) — not allowed",
    "ноты {n} и {n} очень близко ({0}) — трудно попасть": "notes {0} and {1} are very close ({2}) — hard to hit",
    "нота {n} внутри удержания ({0})": "note {0} is inside a hold ({1})", "…и ещё {n}": "…and {n} more",
    "Стрелки / WASD — ноты со стрелками · Пробел / E — круглые · жёлтая полоса — держать любую · Esc — выйти": "Arrows / WASD — arrow notes · Space / E — round ones · yellow bar — hold any · Esc — exit",
    "Готово! Очки: {n} · Esc — выйти": "Done! Points: {n} · Esc — exit", "СУПЕР!": "SUPER!", "КРУТО!": "GREAT!", "ХОРОШО": "GOOD", "ОЙ!": "OOPS!",
    "БОНУС +{n}": "BONUS +{n}",
    "Публикация": "Publishing",
    "Когда ноты готовы, соберите мод песни: сайт упакует звук и ноты, а вы выложите его в Мастерскую Steam. После сборки править нельзя (можно вернуться к правке, тогда мод придётся собрать заново).": "When the notes are ready, build the song mod: the site packs the sound and the notes, and you put it on the Steam Workshop. After building it can't be edited (you can go back to editing, then the mod has to be built again).",
    "Собрать мод": "Build the mod", "не собралось: {0}": "didn't build: {0}", "Скачайте мод:": "Download the mod:", "мод песни (.zip)": "the song mod (.zip)",
    "Проверьте у себя: распакуйте папку из архива в garrysmod/addons/ — песня появится в голосовании в лобби танца.": "Check it yourself: unpack the archive's folder into garrysmod/addons/ — the song appears in the dance lobby's vote.",
    "Обновите уже выложенный мод (папка garrysmod/bin):": "Update the mod you already published (the garrysmod/bin folder):",
    "Выложите в Мастерскую (папка garrysmod/bin):": "Publish it to the Workshop (the garrysmod/bin folder):", "и": "and",
    "(подробно — в README.txt в архиве).": "(details in README.txt in the archive).",
    "Вставьте ссылку на мод ниже и отправьте на проверку. Не меняйте файлы мода — игра сверяет их с сайтом.": "Paste the mod's link below and send it for review. Don't change the mod's files — the game checks them against the site.",
    "Ссылка на мод в Мастерской": "The mod's Workshop link", "Отправить на проверку": "Send for review", "Вернуться к правке": "Back to editing",
    "Проверяем мод в Steam…": "Checking the mod on Steam…",
    "Этот мод выложен другим аккаунтом Steam (или песня ещё не собрана).": "This mod was published by another Steam account (or the song isn't built yet).",
    "Steam не знает такой мод — проверьте ссылку; скрытый мод тоже не виден.": "Steam doesn't know this mod — check the link; a hidden mod isn't visible either.",
    "Этот мод уже привязан к другой песне.": "This mod is already linked to another song.",
    "Вернуться к правке? Собранный мод перестанет подходить — его нужно будет собрать и выложить заново.": "Back to editing? The built mod won't fit any more — it'll have to be built and published again.",
    "Новая версия на проверке. Пока её не одобрят, в игре и в списке — версия {n}.": "A new version is in review. Until it's approved, the game and the list have version {n}.",
    "Песня на проверке. После одобрения она появится в общем списке песен и в игре.": "The song is in review. Once approved it appears in the songs list and in the game.",
    "Песня опубликована (версия {n}) — она в общем списке и в игре.": "The song is published (version {n}) — it's in the songs list and in the game.",
    "Страница песни": "Song page",
    "Нужно поправить ноты? «Изменить» откроет новую версию: опубликованная остаётся в игре, пока новую не одобрят. Потом соберите мод и обновите его в Мастерской — звук загружать заново не нужно.": "Need to fix the notes? «Edit» opens a new version: the published one stays in the game until the new one is approved. Then build the mod and update it on the Workshop — no need to upload the sound again.",
    "Изменить": "Edit", "Проверку не прошла": "Didn't pass the review", "В игре и в списке остаётся версия {n}.": "The game and the list keep version {n}.",
    "Исправить": "Fix", "Кисть": "Brush", "Правка": "Edit", "Пробел": "Space",
    "#recHint": 'Press to the beat: <span class="k" data-k="up">↑</span><span class="k" data-k="down">↓</span><span class="k" data-k="left">←</span><span class="k" data-k="right">→</span> (or WASD), <span class="k" data-k="act">E</span> (or Space); hold longer than ~0.6 s for a hold.',
    "#liveHelp": "«● Record» — the song plays from here, you press keys to the beat, the notes place themselves (with directions; hold longer than ~0.6 s for a hold). Then fix them one by one. One <kbd>Ctrl</kbd>+<kbd>Z</kbd> undoes the whole recording.",
    "#help1": "Click the lower part of the lane to place a note or pick it; drag to move it (picked ones move together). Click the waveform to seek; the wheel scrolls, <kbd>Ctrl</kbd>+wheel zooms.",
    "#help2": "<b>Brush</b> — pick a direction and place notes one after another, each gets it. <b>Edit</b> — the buttons change the picked notes. Directions: <kbd>1</kbd>–<kbd>5</kbd> = ↑ ↓ ← → E, <kbd>0</kbd> = 🎲 (a random key every game), <kbd>Q</kbd> — the next one in turn, <kbd>B</kbd> — Brush / Edit. A hold takes any key.",
    "#help3": "<kbd>Shift</kbd>+click — add / remove a note from the pick, <kbd>Shift</kbd>+drag — pick a stretch, <kbd>Ctrl</kbd>+<kbd>A</kbd> — all, <kbd>Esc</kbd> — unpick. <kbd>Alt</kbd>+click (or drag) — erase. <kbd>Ctrl</kbd>+click a note — the next direction. <kbd>H</kbd> — hold, <kbd>Delete</kbd> — delete the picked. <kbd>Shift</kbd>+<kbd>←</kbd> <kbd>→</kbd> — move the picked one snap step, <kbd>←</kbd> <kbd>→</kbd> — to the next note. <kbd>Ctrl</kbd>+<kbd>C</kbd> / <kbd>Ctrl</kbd>+<kbd>V</kbd> — copy the picked and paste at the cursor (repeat a chorus). <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd> — undo / redo, <kbd>Ctrl</kbd>+<kbd>S</kbd> — save, <kbd>Space</kbd> — play / pause.",
    "#help4": "<kbd>X</kbd> — mirror the picked (← ↔ →, ↑ ↔ ↓): a chorus repeated the other way. <kbd>F</kbd> — fill by grid: a note on every snap step between the first and the last picked note (the brush's direction). <kbd>M</kbd> — metronome, «Speed» 0.75× / 0.5× — place notes slowly (and record too).",
  };
  // pieces inside other text (the editor's command lines)
  const FRAGMENTS = [["<папка>", "<folder>"]];

  const CY = /[А-Яа-яЁё]/;
  const NUM = "([\\d\\s.,\\u00a0\\u202f−+-]+?)", ANY = "(.+?)";
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // the patterns: longest first ("…{0} · выбрано {n}…" before "…{0}"); the English gets the values in order
  const PATTERNS = Object.keys(EN).filter(k => /\{(n|\d)\}/.test(k)).sort((a, b) => b.length - a.length).map(k => {
    const parts = k.split(/(\{n\}|\{\d\})/);
    const re = new RegExp("^" + parts.map(p => p === "{n}" ? NUM : /^\{\d\}$/.test(p) ? ANY : esc(p)).join("") + "$");
    // in the English: {d} = the d-th value caught (in order); {n} = the value of the key's k-th {n}
    const nIdx = []; let c = 0; parts.forEach(p => { if (p === "{n}") nIdx.push(c); if (/^\{(n|\d)\}$/.test(p)) c++; });
    return { re, en: EN[k], nIdx };
  });
  function fill(p, vals) {
    let k = 0;
    return p.en.replace(/\{(n|\d)\}/g, (m, x) => { const v = x === "n" ? vals[p.nIdx[k++]] : vals[+x]; return v !== undefined ? v : m; });
  }
  const missing = new Set();
  function toEn(core) {
    if (EN[core] !== undefined && !/\{(n|\d)\}/.test(core)) return EN[core];
    for (const p of PATTERNS) { const m = core.match(p.re); if (m) return fill(p, m.slice(1).map(v => toEn(v))); }
    let out = core; FRAGMENTS.forEach(([a, b]) => { out = out.split(a).join(b); });
    if (out !== core) return out;
    if (CY.test(core)) missing.add(core);
    return core;
  }
  function tr(s) {
    if (lang === "ru" || !CY.test(s)) return s;
    const m = s.match(/^(\s*)([\s\S]*?)(\s*)$/), core = m[2].replace(/\s+/g, " ");
    const out = toEn(core);
    return out === core ? s : m[1] + out + m[3];
  }

  function detect() {
    const saved = window.mlpStore && window.mlpStore.get(KEY);
    if (LANGS.includes(saved)) return saved;
    for (const l of (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""])) {
      const b = String(l).slice(0, 2).toLowerCase();
      if (LANGS.includes(b)) return b;
    }
    return "en";    // the fallback
  }
  let lang = detect(), chosen = null;
  const root = document.documentElement;
  root.lang = lang;

  // ---------------------------------------------------------------- the document
  const texts = new WeakMap(), attrs = new WeakMap(), blocks = new WeakMap();
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];
  const skip = el => !el || el.closest("script, style, textarea, [data-no-i18n]") || (el.closest("[data-i18n]") && !el.hasAttribute("data-i18n"));
  // force: the language changed — redo even the text this file wrote itself
  function doText(n, force) {
    const p = n.parentElement;
    if (!p || skip(p) || p.closest("[data-i18n]")) return;
    let r = texts.get(n);
    if (r && n.data === r.out && !force) return;
    if (!r || n.data !== r.out) { if (!CY.test(n.data) && !r) return; r = { ru: n.data }; }
    r.out = tr(r.ru); texts.set(n, r);
    if (n.data !== r.out) n.data = r.out;
  }
  function doAttr(el, a, force) {
    if (el.closest("script, style, [data-no-i18n]")) return;     // a textarea's own placeholder is translated, its text isn't
    const v = el.getAttribute(a); if (v === null) return;
    let m = attrs.get(el); if (!m) { m = {}; attrs.set(el, m); }
    let r = m[a];
    if (r && v === r.out && !force) return;
    if (!r || v !== r.out) { if (!CY.test(v) && !r) return; r = m[a] = { ru: v }; }
    r.out = tr(r.ru);
    if (v !== r.out) el.setAttribute(a, r.out);
  }
  function doBlock(el) {
    let ru = blocks.get(el);
    if (ru === undefined) { ru = el.innerHTML; blocks.set(el, ru); }
    const en = EN["#" + el.getAttribute("data-i18n")];
    const want = lang === "en" && en ? en : ru;
    if (el.innerHTML !== want) el.innerHTML = want;
  }
  function walk(node, force) {
    if (node.nodeType === 3) { doText(node, force); return; }
    if (node.nodeType !== 1) return;
    if (node.hasAttribute("data-i18n")) doBlock(node);
    ATTRS.forEach(a => { if (node.hasAttribute(a)) doAttr(node, a, force); });
    if (node.hasAttribute("data-i18n")) return;
    for (let c = node.firstChild; c; c = c.nextSibling) walk(c, force);
  }
  const obs = new MutationObserver(recs => {
    for (const r of recs) {
      if (r.type === "characterData") doText(r.target);
      else if (r.type === "attributes") doAttr(r.target, r.attributeName);
      else r.addedNodes.forEach(walk);
    }
  });
  obs.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });

  // English: hide the page until it's translated (no Russian flash)
  if (lang === "en") {
    root.classList.add("i18n-wait");
    const st = document.createElement("style"); st.textContent = "html.i18n-wait body { visibility: hidden; }"; document.head.appendChild(st);
  }
  function ready() { walk(root); root.classList.remove("i18n-wait"); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
  setTimeout(() => root.classList.remove("i18n-wait"), 2000);

  window.mlpLang = () => lang;
  window.mlpLocale = () => (lang === "ru" ? "ru-RU" : "en-US");
  window.mlpSetLang = function (l) {
    if (!LANGS.includes(l)) return;
    chosen = l; window.mlpStore.set(KEY, l);
    if (l === lang) return;
    lang = l; root.lang = l; walk(root, true);
    document.dispatchEvent(new CustomEvent("mlp-lang", { detail: l }));
  };
  // a choice made before "remember settings" was allowed: keep it once it is
  document.addEventListener("mlp-consent", () => { if (chosen) window.mlpStore.set(KEY, chosen); });
  // dialogs, canvas: tx("Удалить «{0}»?", title)
  window.tx = function (key, ...args) {
    let s = key;
    if (lang === "en") s = EN[key] !== undefined ? EN[key] : toEn(key);
    return s.replace(/\{(\d)\}/g, (m, i) => (args[+i] !== undefined ? args[+i] : m));
  };
  window.mlpI18nMissing = () => [...missing];
})();
