# Деплой и CI/CD

Рабочий сервис: http://185.249.152.10

## Конвейер

`.github/workflows/ci-cd.yml`:

1. **CI** на каждый пуш и пул-реквест: тесты бэка на Python 3.10 и 3.12; воспроизведение
   сохранённых расчётов `examples/runs/` утилитой организаторов; проверка типов и сборка фронта;
   сборка Docker-образа и проверка запущенного контейнера (`/api/health`, `/api/meta`, страница).
2. **CD** при пуше в `main`: образ `orbit-planner:<sha>` передаётся на сервер по SSH
   (`docker save` → `docker load`, без реестра); `deploy/deploy.sh` переключает версию, ждёт
   `/api/health` и при сбое возвращает предыдущую. Хранилище запусков — том `orbit-storage`,
   переживает обновления.
3. **Откат вручную**: Actions → CI/CD → Run workflow → `rollback`.

## Сервер

- Ubuntu 24.04, 1 vCPU, 1 ГБ RAM + swap; Docker, nginx.
- nginx на 80 порту проксирует на `127.0.0.1:8000`; фаервол открыт только для SSH, HTTP, HTTPS.
- Вход по SSH только по ключу, вход по паролю отключён.
- CI заходит пользователем `deploy`; его ключ ограничен принудительной командой
  `orbit-ci-entry`: загрузить образ, обновить `compose.yml`/`deploy.sh`, выкатить или откатить —
  без shell.
- Контейнер работает от непривилегированного пользователя, память ограничена 700 МБ, API — один
  процесс (запуски и блокировки в памяти).

## Первичная настройка (один раз)

```bash
ssh-keygen -t ed25519 -N "" -C orbit-planner-ci -f ~/.ssh/orbit_deploy
ssh root@HOST "bash -s -- '$(cat ~/.ssh/orbit_deploy.pub)'" < deploy/server-setup.sh
```

В настройках репозитория GitHub (Settings → Secrets and variables → Actions):

| Тип | Имя | Значение |
|---|---|---|
| Secret | `DEPLOY_SSH_KEY` | содержимое `~/.ssh/orbit_deploy` |
| Variable | `DEPLOY_HOST` | адрес сервера |
| Variable | `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -t ed25519 HOST` |

## Обслуживание

```bash
ssh root@HOST 'cd /opt/orbit-planner && docker compose -p orbit logs --tail 100 app'   # логи
ssh root@HOST 'cat /opt/orbit-planner/.current_tag /opt/orbit-planner/.previous_tag'   # версии
```
