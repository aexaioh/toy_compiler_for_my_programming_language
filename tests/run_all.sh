#!/usr/bin/env bash
# run_all.sh — прогоняет все *.myl, печатает вывод и проверяет exit code
#
# Тесты, которые ДОЛЖНЫ упасть на компиляции, помечаются первой строкой:
#     // expect_compile_error
# Если такой тест скомпилировался — это FAIL (маркер устарел).
# Если тест без маркера не компилируется — тоже FAIL.

set -u

cd "$(dirname "$0")" || exit 1

# Тесты, которые должны завершиться с определённым exit code (не 0).
declare -A expected_exit=(
    [io6.myl]=2
    [dz1.myl]=1
    [dz2.myl]=1
    [dz3.myl]=1
    [sh4.myl]=1
    [re6.myl]=1
    [ret7.myl]=1
)

pass=0
fail=0

for f in *.myl; do
    [ -e "$f" ] || continue

    printf '\n=== %s ===\n' "$f"

    # Проверяем, помечен ли файл как «ожидается ошибка компиляции».
    # Первая строка файла должна быть "// expect_compile_error" (пробелы допустимы).
    expects_compile_error=0
    first_line=$(head -n1 "$f" 2>/dev/null)
    if [[ "$first_line" =~ ^[[:space:]]*//[[:space:]]*expect_compile_error[[:space:]]*$ ]]; then
        expects_compile_error=1
    fi

    if ! python3 mylangc.py "$f" -o test >/dev/null 2>&1; then
        if [ "$expects_compile_error" -eq 1 ]; then
            echo "    >>> OK (compile failed, as expected)"
            ((pass++))
        else
            echo ">>> [compile failed] $f"
            ((fail++))
        fi
        continue
    fi

    # Компиляция прошла. Но если файл помечен как «ожидается ошибка» —
    # это значит, что маркер устарел или ошибка в тесте.
    if [ "$expects_compile_error" -eq 1 ]; then
        echo "    >>> FAIL (expected compile error, but compiled successfully)"
        ((fail++))
        continue
    fi

    # Запускаем; stdout/stderr идут прямо в терминал (как раньше),
    # но нам нужен ещё и exit code.
    ./test
    rc=$?

    want="${expected_exit[$f]:-0}"

    if [ "$rc" -ne "$want" ]; then
        echo "    >>> FAIL (exit=$rc, expected=$want)"
        ((fail++))
    else
        echo "    >>> OK (exit=$rc)"
        ((pass++))
    fi
done

printf '\n----------------------------------------\n'
printf 'OK: %d   FAILED: %d\n' "$pass" "$fail"

[ "$fail" -eq 0 ]
