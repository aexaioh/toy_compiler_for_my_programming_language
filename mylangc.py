#!/usr/bin/env python3
"""mylangc — CLI-обёртка компилятора mylang.

Использование:
    python3 mylangc.py <source.myl> [-o output]
    ./mylangc.py <source.myl> -o output     (если стоит +x)
"""

from mylang.driver import main

if __name__ == "__main__":
    main()
