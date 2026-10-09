# mylang — руководство по языку (сгенерировано с помощью LLM)

> `mylang` — прототип статически типизированного компилируемого языка для скриптов и CLI/Unix-автоматизации.
>
> Документ описывает поведение версии `mylangc` v28. Компилятор — пакет `mylang/`, точка входа — `mylangc.py`.
>
> Планируется: порт компилятора на C23 и модель управления памятью (арена).

---

# 0. Назначение

Язык предназначен для автоматизации взаимодействия с Unix-подобными системами. Типичная задача — обвязка вокруг существующих CLI-утилит.

---

# 1. Как скомпилировать

```bash
python3 mylangc.py program.myl -o program
./program
```

`mylangc.py` — тонкая обёртка над пакетом `mylang/`.

---

# 2. Главные концепции

## 2.1. Внешние программы запускаются как есть

Пример:

```myl
fn main() = {
  let r = $(git status --short)

  if r.status == 0 {
    print(r.stdout)
  } else {
    print(r.stderr)
  }
}
```

`git` запускается как `git`. `mylang` читает `status`, `stdout`, `stderr` и что-то с ними делает.

Предполагаемый порядок работы:

```text
argv / environment
       ↓
   Unix-команда
       ↓
status + stdout + stderr
       ↓
Str / List / Map
       ↓
 if / for / функции
       ↓
Option / Result
       ↓
 следующая команда
```

Встроенных HTTP, JSON и прочих библиотек нет. Задача вроде «получить данные из API» решается вызовом `curl`, «разобрать JSON» — вызовом `jq`.

## 2.2. Что в языке, что через shell

Язык содержит набор примитивов, которых нет в shell или которые неудобно делать через shell:

- **Логика и типы.** Ветвления, циклы, функции, `?`, `match`, статическая типизация. Shell этого не умеет.
- **Структуры данных.** `List`, `Map`, `Option`, `Result`. В shell нет структурных типов.
- **Работа со строками и списками.** `split`, `trim`, `replace`, `map`, `filter`, `sort`, `group_by` — то, что в shell делается через `awk`/`sed`/`cut`, здесь делается методами.
- **Вывод.** `print`, `printnnl`, `eprint`, `eprintnnl`.
- **Точки контакта с окружением.** `args`, `getenv`, `exit`, `sleep`, `now`.
- **Файлы.** `read_file`, `write_file` — чтобы не терять различие между «файл не найден» и «файл пустой».
- **Запуск команд.** `$(...)`, `sh("...")`, `<<`.

Всё, что можно сделать через вызов внешней утилиты, делается через `$()` или `sh("...")`:

- сеть → `$(curl ...)`;
- JSON/YAML → `$("jq ...")`, `$("yq ...")`;
- файловые операции → `sh("mkdir -p ...")`, `sh("test -f ...")`;
- время и даты → `sh("date ...")`;
- работа с путями → конкатенация строк `+`.

## 2.3. `$()` и `sh()`

```myl
let r = $(git status --short)     // прямой запуск, argv-стиль
let r = sh("ls *.myl | wc -l")    // через sh -c
```

`$()` не вызывает shell. `sh("...")` вызывает.

## 2.4. `<<` — stdin

```myl
let data = "hello\nworld\n"
let r = $(grep hello) << data
print(r.stdout)
```

Работает для `$()` и для `sh()`. Данные передаются как есть, без интерпретации. Любые символы, кроме `\x00`.

## 2.5. Типы

```myl
let x = 42           // Int
let name = "Alice"   // Str
let ok = true        // Bool
```

Аннотации типов не пишутся. Компилятор выводит их сам.

Это ошибка:

```myl
let x = "hello"
print(x + 10)     // Str + Int не складывается
```

## 2.6. Внешние данные тоже типизированы

```myl
let host = args().get(0).or("localhost")   // Str
let r = $(uname -s)                        // CmdResult
```

Конкретные значения неизвестны до запуска. Типы известны на этапе компиляции. Если выражение невозможно типизировать статически — программа не компилируется.

## 2.7. Полиморфизм

```myl
fn id(x) = {
  x
}
```

Тип: `T -> T`. Работает с любыми типами:

```myl
print(id(42))
print(id("hello"))
```

Компилятор создаёт специализации (`myl_spec_id__Int`, `myl_spec_id__Str`). Динамического выбора типа в рантайме нет.

## 2.8. `if` — выражение

`if` возвращает значение последнего выражения в ветке.

Если результат не используется — обе ветви `Unit`:

```myl
if x > 0 {
  print("positive")
} else {
  print("non-positive")
}
```

Если результат используется — ветви совместимы по типу:

```myl
fn classify(x) = {
  if x > 0 {
    "positive"
  } else {
    "non-positive"
  }
}
```

`classify(10)` даёт `"positive"`. Обе ветви возвращают `Str`.

Без `else`:

```myl
if ready {
  print("go")
}
```

Такой `if` всегда `Unit`.

## 2.9. `Unit`

Тип «нет полезного значения». Возвращается, когда операции нечего возвращать:

```text
print(T) -> Unit
sleep(Int) -> Unit
exit(Int) -> Unit
```

`Unit` нужен, чтобы у любого выражения был тип.

## 2.10. Два способа сказать «значения нет»

`Option[T]` — «есть T или нет ничего». Используется там, где отсутствие значения — нормальная ситуация, не ошибка.

```myl
let x = xs.get(10)              // Option[Int]
let home = getenv("HOME")       // Option[Str]
let n = "42".to_int()           // Option[Int]
```

Обычно обрабатывается через `or(default)` или `match`.

`Result[T, E]` — «есть T или есть ошибка E». Используется там, где отсутствие значения — это провал операции.

```myl
let content = read_file("/etc/hostname")   // Result[Str, Str]
let r = $(some_command)                    // CmdResult
```

Обычно обрабатывается через `?` или `match`.

Правило простое: «ничего не найдено» — `Option`. «Не получилось» — `Result`.

## 2.11. `?`

Пробрасывает ошибку наверх. Семантика зависит от типа выражения слева.

**На `Option[T]`.** Возвращает `T`, если `Some`. Если `None` — возвращает `None` из объемлющей функции. Объемлющая функция должна возвращать `Option[U]`.

```myl
fn first_word(s) = {
  let words = s.trim().split(" ")
  let first = words.first()?
  Ok(first)
}
```

**На `Result[T, E]`.** Возвращает `T`, если `Ok`. Если `Err(e)` — возвращает `Err(e)` из объемлющей функции. Тип ошибки сохраняется. Объемлющая функция должна возвращать `Result[U, E]`.

```myl
fn read_config() = {
  let content = read_file("/etc/app.conf")?
  let first = content.lines().first()?
  Ok(first.trim())
}
```

**На `CmdResult`.** Проверяет `status != 0`. Если ненулевой — возвращает `Err(stderr)`. Тип ошибки всегда `Str`. Объемлющая функция должна возвращать `Result[?, Str]`. Сам `CmdResult` возвращается дальше:

```myl
fn check_git() = {
  let r = $(git status --short)?
  Ok(r.stdout)
}
```

Смешивать нельзя: `?` на `Option` внутри функции, возвращающей `Result`, не скомпилируется. Автоматической конвертации `None` в `Err` нет.

## 2.12. Коллекции

`List` и `Map` — для структурированных данных: аргументов, stdout, конфигов.

```myl
let lines = r.stdout.lines()
let errors = lines.filter(x => x.contains("ERROR"))
```

```myl
let counts = map()

for word in words {
  let cur = counts.get(word).or(0)
  counts = counts.set(word, cur + 1)
}
```

---

# 3. Базовый синтаксис

## 3.1. Программа

```myl
fn main() = {
  print("hello, world")
}
```

## 3.2. Функции

```text
fn имя(аргументы) = {
  тело
}
```

```myl
fn greet(name) = {
  "hello, {name}"
}

fn main() = {
  print(greet("Alice"))
}
```

Результат — последнее выражение тела.

## 3.3. Переменные

```myl
let x = 10
let name = "Alice"
```

Переприсваивание:

```myl
let x = 10
x = 20
```

Переприсваивать можно переменные, объявленные в текущей функции. Параметры функции — не переприсваиваемые.

## 3.4. Комментарии

```myl
// comment
let x = 42 // comment
```

---

# 4. Типы

| Тип | Что | Пример |
|---|---|---|
| `Int` | целое число, 64-бит | `42`, `-7` |
| `Str` | строка | `"hello"` |
| `Bool` | true/false | `true`, `false` |
| `Unit` | ничего полезного | `()` |
| `List[T]` | список | `[1, 2, 3]` |
| `Map[Str, T]` | map со строковыми ключами | `map()` |
| `Option[T]` | значение или его отсутствие | `Some(42)`, `None` |
| `Result[T, E]` | результат или ошибка | `Ok(42)`, `Err("bad")` |
| `CmdResult` | результат команды | `$(uname -s)` |

Тип функции: `(Тип1, Тип2, ...) -> Результат`.

`Int` — 64-битный.

`Float` отсутствует. Если понадобится — будет добавлен.

---

# 5. Литералы и операторы

## Int

```myl
0
42
-5
```

## Bool

```myl
true
false
```

## Str

```myl
"hello"
"hello\nworld"
```

## Интерполяция

```myl
let x = 42
print("x = {x}")
print("1 + 2 = {1 + 2}")
```

## Фигурные скобки в тексте

```myl
print("literal braces: {{x}}, value: {1 + 1}")
```

```text
literal braces: {x}, value: 2
```

## Regex-квантификаторы

`{4}`, `{2,8}`, `{2,}`, `{,5}` — не интерполяция, передаются как есть.

```myl
let dates = line.find_all("[0-9]{4}-[0-9]{2}-[0-9]{2}")
```

## Арифметика

```text
+  -  *  /  %
```

```myl
print(10 / 3)     // 3
print(10 % 3)     // 1
print(2 + 3 * 4)  // 14
```

Деление на ноль — runtime error с позицией:

```text
error: division by zero
  at script.myl:5:9
```

Код выхода — 1.

## Сравнение

```text
==  !=  <  >  <=  >=
```

## Логика

```text
&&  ||  !
```

---

# 6. Условия и циклы

## `if`

```myl
if x > 3 {
  print("big")
} else {
  print("small")
}
```

Как значение:

```myl
fn classify(x) = {
  if x > 0 {
    "positive"
  } else {
    "non-positive"
  }
}
```

Без `else` — `Unit`.

## `for`

```myl
for x in [1, 2, 3] {
  print(x)
}
```

Возвращает `Unit`.

## `while`

```myl
let i = 0
while i < 5 {
  print(i)
  i = i + 1
}
```

Условие — `Bool`. Возвращает `Unit`.

## `break` и `continue`

```myl
for x in xs {
  if x < 0 {
    continue
  }
  if x > 100 {
    break
  }
  print(x)
}
```

Вне цикла — ошибка компиляции:

```text
error: 'break' outside of loop
  at script.myl:3:5
```

## `return`

Возвращает значение из функции раньше срока. Только в обычных функциях, не в `main`.

```myl
fn first_positive(xs) = {
  for x in xs {
    if x > 0 {
      return x
    }
  }
  -1
}
```

`return` должен быть не последним statement. Если вернуть значение обычным путём — оно должно быть последним выражением функции.

Это правило, чтобы не было двух способов сделать одно и то же. Возможны только:

- последнее выражение — возврат значения;
- `return expr` в середине — ранний выход из глубокой вложенности.

`return` последним statement — ошибка:

```text
error: 'return' as last statement is redundant; use the final expression instead
```

`return` в `main` запрещён:

```text
error: 'return' is not allowed in 'main'; use exit(N)
```

`return` в лямбде тоже запрещён — лямбда не имеет своего типа возврата.

---

# 7. Вывод

| Функция | Куда | Newline |
|---|---|---|
| `print(s)` | stdout | да |
| `printnnl(s)` | stdout | нет |
| `eprint(s)` | stderr | да |
| `eprintnnl(s)` | stderr | нет |

```myl
printnnl("loading")
printnnl(".")
printnnl(".")
printnnl(".")
print("")
```

```text
loading...
```

```myl
eprint("warn: something")
```

Разделение потоков через shell:

```bash
./script > /dev/null       # только stderr
./script 2> /dev/null      # только stdout
```

---

# 8. Функции и полиморфизм

```myl
fn add(a, b) = {
  a + b
}
```

Тип: `add(Int, Int) -> Int`.

```myl
fn greet(name) = {
  "hi, {name}"
}
```

Тип: `greet(Str) -> Str`.

```myl
fn id(x) = {
  x
}
```

Тип: `id(T) -> T`.

---

# 9. Lambda

Синтаксис: `параметр => выражение`.

```myl
x => x * 2
s => s.upper()
x => x > 10
```

Только внутри операций над коллекциями:

```myl
let ys = xs.map(x => x * 2)
```

Поддерживаются `map`, `filter`, `each`, `group_by` для списков и `each` для Map.

Лямбда не замыкание. Видит только свой параметр. Внешние переменные недоступны.

Для Map — парная лямбда:

```myl
m.each((k, v) => print("{k} = {v}"))
```

---

# 10. List

```myl
let xs = [1, 2, 3]
let names = ["alice", "bob"]
let empty = []
```

Конкатенация:

```myl
let c = [1, 2] + [3, 4]   // [1, 2, 3, 4]
```

Пустой список получает тип из контекста.

## Методы

### `.len() -> Int`

```myl
["a", "b", "c"].len()   // 3
```

### `.map((T) -> U) -> List[U]`

```myl
[1, 2, 3].map(x => x * 2)   // [2, 4, 6]
```

### `.filter((T) -> Bool) -> List[T]`

```myl
[1, 2, 3, 4].filter(x => x > 2)   // [3, 4]
```

### `.each((T) -> Unit) -> Unit`

```myl
["a", "b"].each(x => print(x))
```

### `.join(Str) -> Str`

```myl
["a", "b", "c"].join(", ")   // "a, b, c"
```

### `.reverse() -> List[T]`

```myl
[1, 2, 3].reverse()   // [3, 2, 1]
```

### `.take(Int) -> List[T]`

```myl
[1, 2, 3, 4].take(2)   // [1, 2]
```

### `.drop(Int) -> List[T]`

```myl
[1, 2, 3, 4].drop(2)   // [3, 4]
```

### `.first() -> Option[T]`

```myl
["a", "b"].first().or("none")   // "a"
[].first().or("none")           // "none"
```

### `.last() -> Option[T]`

```myl
["a", "b"].last().or("none")   // "b"
```

### `.get(Int) -> Option[T]`

```myl
["a", "b"].get(1).or("none")    // "b"
["a", "b"].get(10).or("none")   // "none"
```

### `.sort() -> List[T]` (Int, Str)

```myl
[3, 1, 2].sort()   // [1, 2, 3]
```

### `.contains(T) -> Bool` (Int, Str, Bool)

```myl
[1, 2, 3].contains(2)   // true
```

### `.unique() -> List[T]` (Int, Str)

Сохраняет порядок первого вхождения.

```myl
[3, 1, 2, 3, 1].unique()   // [3, 1, 2]
```

### `.group_by((T) -> Str) -> Map[Str, List[T]]`

```myl
let groups = files.group_by(f => f.split(".").last().or("?"))
```

---

# 11. Map

```myl
let m = map()
```

Ключи — `Str`.

### `map() -> Map[Str, T]`

Тип значения уточняется использованием.

### `.set(Str, T) -> Map[Str, T]`

Иммутабельный. Возвращает новый Map.

```myl
let m = map()
m = m.set("alice", 30)
m = m.set("bob", 25)
```

### `.get(Str) -> Option[T]`

```myl
m.get("alice").or(0)   // 30
m.get("bob").or(0)     // 0
```

### `.has(Str) -> Bool`

```myl
m.has("alice")   // true
```

### `.keys() -> List[Str]`

Порядок ключей — порядок вставки.

### `.values() -> List[T]`

### `.items() -> List[Str]`

Возвращает строки вида `"key=value"`.

**Ограничение.** Формат предполагает, что `key` не содержит `=` и что `value` не содержит `=` (иначе невозможно однозначно разделить строку обратно на ключ и значение). Если значения могут содержать `=`, используйте `keys()` + `get()`:

```myl
for k in m.keys() {
  let v = m.get(k).or("?")
  print("{k} -> {v}")
}
```

### `.len() -> Int`

### `.delete(Str) -> Map[Str, T]`

Иммутабельный, как `set`.

### `.each(((Str, T) -> Unit)) -> Unit`

Парная лямбда:

```myl
m.each((name, age) => print("{name} is {age}"))
```

---

# 12. Option

```myl
let x = Some(42)
let y = None
```

`None` получает конкретный `T` из контекста.

### `.or(T) -> T`

```myl
Some(42).or(0)   // 42
None.or(0)       // 0
```

Чаще всего — после `get`, `first`, `last`, `to_int`, `getenv`.

---

# 13. Result

```myl
let r = Ok(42)
let r = Err("not found")
```

### `match`

Работает только с `Option` и `Result`, требует ровно два варианта.

```myl
fn lookup(m, k) = {
  match m.get(k) {
    Some(v) => Ok(v)
    None => Err("not found")
  }
}
```

```myl
match lookup(m, "a") {
  Ok(v) => print("found: {v}")
  Err(e) => print(e)
}
```

---

# 14. Как устроен `main`

`main` — обычная функция. `return` в ней запрещён.

**Без `?`** — main возвращает `Unit`. Рантайм возвращает exit code 0. Для досрочного выхода — `exit(N)`.

```myl
fn main() = {
  print("hello")
}
```

**С `?`** — main возвращает `Result[Unit, Str]`. Последнее выражение — `Ok(())`. При `Err` рантайм печатает сообщение в stderr и возвращает exit code 1.

```myl
fn main() = {
  let r = $(git status --short)?
  print(r.stdout)
  Ok(())
}
```

При ошибке:

```text
error: git: not a git repository
```

Для досрочного выхода с кодом — `exit(N)`.

---

# 15. Внешние команды

`$(...) -> CmdResult`

Поля:

```text
status : Int
stdout : Str
stderr : Str
```

```myl
let r = $(uname -s)
print(r.stdout.trim())
```

Пример:

```myl
let r = $(git status --short)

if r.status == 0 {
  if r.stdout.trim().len() == 0 {
    print("clean")
  } else {
    print("dirty")
    print(r.stdout)
  }
} else {
  print("not a git repo")
  print(r.stderr)
}
```

Подстановка выражений:

```myl
let r = $(ping -c1 -W1 {host})
```

`{host}` — mylang-выражение. Аргумент передаётся напрямую в `argv`, shell не вызывается.

`?` для `CmdResult`:

```myl
let r = $(cat /nonexistent)?
```

Ненулевой status превращается в `Err(stderr)`.

stdin:

```myl
let data = "one\ntwo\nthree\n"
let r = $(wc -l) << data
print(r.stdout.trim())   // 3
```

**Про NUL-байты.** Строки в mylang — C-strings, NUL-terminated. Внутри `\x00` быть не может. Если команда выдаёт бинарные данные с нулевыми байтами, всё после первого `\x00` теряется. Для бинарных данных — `base64`, `xxd`, `od` или временные файлы.

---

# 16. Shell

`sh(Str) -> CmdResult`

```myl
let r = sh("echo hello")
print(r.stdout.trim())
```

Pipe:

```myl
let r = sh("ls /etc | head -5")
```

Globbing/redirection:

```myl
let r = sh("ls *.myl 2>/dev/null | wc -l")
```

stdin:

```myl
let r = sh("sort -r") << data
```

---

# 17. Regex

POSIX ERE (`regcomp`/`regexec`, `REG_EXTENDED | REG_NEWLINE`). PCRE не используется. `\d`, `\w`, `\s` не поддерживаются. Используйте `[0-9]`, `[A-Za-z0-9_]`, `[[:space:]]`.

Регэкспы кэшируются. Невалидный регэксп — runtime error с позицией.

### `.matches(Str) -> Bool`

```myl
"hello123".matches("[0-9]+")   // true
```

### `.find_all(Str) -> List[Str]`

Возвращает непересекающиеся непустые совпадения.

```myl
"a1b22c333".find_all("[0-9]+")   // ["1", "22", "333"]
```

### `.replace_re(Str, Str) -> Str`

В строке замены `\N` — группа N, `\\` — backslash.

```myl
"a1b22c333".replace_re("[0-9]+", "N")   // aNbNcN

"2024-01-15".replace_re("([0-9]{4})-([0-9]{2})-([0-9]{2})", "\\3/\\2/\\1")
// 15/01/2024
```

Пример — парсинг лога:

```myl
let log = "2024-01-15 10:23:45 ERROR something bad\n2024-01-15 10:24:01 INFO ok"
let errors = log.lines().filter(l => l.matches("ERROR"))

for e in errors {
  print(e)
}
```

---

# 18. Файлы

### `read_file(Str) -> Result[Str, Str]`

```myl
let content = read_file("/etc/hostname")?
print(content.trim())
```

Ошибка:

```myl
match read_file("/nonexistent") {
  Ok(c) => print(c)
  Err(e) => print(e)
}
```

### `write_file(Str, Str) -> Result[Unit, Str]`

```myl
write_file("/tmp/test.txt", "hello\n")?
```

---

# 19. Аргументы и environment

### `args() -> List[Str]`

Имя программы не входит.

```bash
./program one two three
```

```myl
args()   // ["one", "two", "three"]
```

### `getenv(Str) -> Option[Str]`

```myl
match getenv("HOME") {
  Some(home) => print(home)
  None => print("no HOME")
}
```

---

# 20. Время и завершение

### `sleep(Int) -> Unit`

Миллисекунды.

```myl
sleep(500)
```

### `now() -> Int`

Миллисекунды с epoch. 64-битное значение.

```myl
let t0 = now()
sleep(100)
print("elapsed: {now() - t0} ms")
```

### `exit(Int) -> Unit`

```myl
if failed {
  exit(2)
}
```

В `main` это основной способ досрочного выхода с ненулевым кодом.

---

# 21. Методы Str

```text
len() -> Int
trim() -> Str
upper() -> Str
lower() -> Str
contains(Str) -> Bool
starts_with(Str) -> Bool
ends_with(Str) -> Bool
replace(Str, Str) -> Str           // литеральная замена
split(Str) -> List[Str]
lines() -> List[Str]               // разбивает по \n, пустые отбрасывает
repeat(Int) -> Str
pad_left(Int) -> Str
pad_right(Int) -> Str
to_int() -> Option[Int]            // пробелы по краям обрезаются
to_str() -> Str
matches(Str) -> Bool
find_all(Str) -> List[Str]
replace_re(Str, Str) -> Str
```

Примеры:

```myl
"hello".len()             // 5
"  hello  ".trim()        // "hello"
"hello".upper()           // "HELLO"
"HELLO".lower()           // "hello"
"hello".contains("ell")   // true
"hello".starts_with("he") // true
"hello".ends_with("lo")   // true
"hello".replace("l", "L") // "heLLo"
"a,b,c".split(",")        // ["a", "b", "c"]
"one\ntwo\nthree".lines() // ["one", "two", "three"]
"=".repeat(5)             // "====="
"42".pad_left(5)          // "   42"
"42".pad_right(5)         // "42   "
"42".to_int().or(-1)      // 42
"abc".to_int().or(-1)     // -1
"  -7  ".to_int().or(0)   // -7
```

---

# 22. Int и встроенные функции

### `Int.to_str() -> Str`

```myl
42.to_str()   // "42"
```

### Вывод

```text
print(T) -> Unit
printnnl(T) -> Unit
eprint(T) -> Unit
eprintnnl(T) -> Unit
```

### `abs(Int) -> Int`

```myl
abs(-5)   // 5
```

### `min(T, T) -> T`

`T` — `Int` или `Str`.

```myl
min(3, 7)                 // 3
min("apple", "banana")    // "apple"
```

### `max(T, T) -> T`

```myl
max(3, 7)                 // 7
max("apple", "banana")    // "banana"
```

### `unit() -> Unit`

```myl
let u = unit()
```

Обычно не нужно — `print("hello")` и `sleep(100)` уже возвращают `Unit`.

---

# 23. Справочник встроенных функций

| Имя | Сигнатура | Назначение |
|---|---|---|
| `print` | `print(T) -> Unit` | вывод + newline |
| `printnnl` | `printnnl(T) -> Unit` | вывод без newline |
| `eprint` | `eprint(T) -> Unit` | stderr + newline |
| `eprintnnl` | `eprintnnl(T) -> Unit` | stderr без newline |
| `unit` | `unit() -> Unit` | получить `Unit` |
| `Some` | `Some(T) -> Option[T]` | значение есть |
| `None` | `None -> Option[T]` | значения нет |
| `Ok` | `Ok(T) -> Result[T,E]` | успех |
| `Err` | `Err(E) -> Result[T,E]` | ошибка |
| `read_file` | `read_file(Str) -> Result[Str,Str]` | прочитать файл |
| `write_file` | `write_file(Str,Str) -> Result[Unit,Str]` | записать файл |
| `args` | `args() -> List[Str]` | аргументы |
| `getenv` | `getenv(Str) -> Option[Str]` | environment |
| `exit` | `exit(Int) -> Unit` | завершить процесс |
| `sleep` | `sleep(Int) -> Unit` | задержка в ms |
| `now` | `now() -> Int` | время в ms |
| `abs` | `abs(Int) -> Int` | модуль числа |
| `min` | `min(Int,Int) -> Int`, `min(Str,Str) -> Str` | минимум |
| `max` | `max(Int,Int) -> Int`, `max(Str,Str) -> Str` | максимум |
| `sh` | `sh(Str) -> CmdResult` | выполнить через shell |
| `map` | `map() -> Map[Str,T]` | создать Map |

---

# 24. Справочник методов

## `Str`

См. раздел 21.

## `Int`

```text
to_str() -> Str
```

## `List[T]`

```text
len() -> Int
map((T) -> U) -> List[U]
filter((T) -> Bool) -> List[T]
each((T) -> Unit) -> Unit
join(Str) -> Str
reverse() -> List[T]
take(Int) -> List[T]
drop(Int) -> List[T]
first() -> Option[T]
last() -> Option[T]
get(Int) -> Option[T]
sort() -> List[T]       // Int / Str
contains(T) -> Bool     // Int / Str / Bool
unique() -> List[T]     // Int / Str
group_by((T) -> Str) -> Map[Str, List[T]]
```

## `Map[Str,T]`

```text
get(Str) -> Option[T]
set(Str, T) -> Map[Str,T]
has(Str) -> Bool
keys() -> List[Str]
values() -> List[T]
items() -> List[Str]
len() -> Int
delete(Str) -> Map[Str,T]
each(((Str, T) -> Unit)) -> Unit
```

## `Option[T]`

```text
or(T) -> T
```

## `CmdResult`

```text
status : Int
stdout : Str
stderr : Str
```

---

# 25. Ограничения

- **Только `Int`, без `Float`.** Если понадобится — будет добавлен.
- **Строки NUL-terminated.** Внутри не может быть `\x00`.
- **`Map.items()` возвращает `"key=value"`.** Формат не подходит для значений, содержащих `=`. В этом случае используется `keys()` + `get()`.
- **Лямбды без замыканий.** Видят только свой параметр.
- **`Map` — линейный поиск.**
- **GNU C на выходе.** Codegen использует statement expressions `({ ... })`. Нужен gcc или clang.
- **Управление памятью не сделано.** Runtime не освобождает память. Планируется арена в C-версии компилятора.
- **Нет модулей, импортов, структур, кортежей.** Скрипт — один файл.

---

# 26. Пример

```myl
fn main() = {
  let r = $(git status --short)?
  let lines = r.stdout.lines().filter(x => x.trim().len() > 0)

  if lines.len() == 0 {
    print("clean")
  } else {
    print("dirty")
    lines.each(x => print(x))
  }

  Ok(())
}
```

Retry с backoff:

```myl
fn wait_service(url, max_attempts) = {
  let attempt = 0
  while attempt < max_attempts {
    let r = $(curl -sf {url})
    if r.status == 0 {
      return true
    }
    attempt = attempt + 1
    sleep(1000 * attempt)
  }
  false
}

fn main() = {
  if wait_service("http://localhost:8080/health", 5) {
    print("service is up")
  } else {
    eprint("service failed to start")
    exit(1)
  }
}
```