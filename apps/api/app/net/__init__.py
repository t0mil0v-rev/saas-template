"""Собственный сетевой слой поверх uvicorn/ASGI.

Модули:

* :mod:`app.net.proxy`      - определение реального IP клиента за прокси;
* :mod:`app.net.ratelimit`  - token bucket на Redis с деградацией в память;
* :mod:`app.net.middleware` - конвейер middleware (порядок важен, см. docstring);
* :mod:`app.net.server`     - prefork-супервизор с SO_REUSEPORT и graceful restart.
"""
