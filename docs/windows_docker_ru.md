# Docker daemon на Windows

Отдельно скачивать Docker daemon для этого проекта не нужно. Установите
**Docker Desktop for Windows**: он уже включает Docker Engine (daemon), CLI и
Docker Compose.

Официальная установка:
<https://docs.docker.com/desktop/setup/install/windows-install/>

## Быстрый запуск

### Вариант для обычного пользователя — двойной клик

Распакуйте ZIP полностью и запустите:

```text
INSTALL_AND_RUN_WINDOWS.cmd
```

Что делает скрипт:

1. проверяет аппаратную виртуализацию;
2. включает системные компоненты WSL 2, если они отсутствуют;
3. обновляет WSL;
4. устанавливает официальный Docker Desktop через WinGet;
5. запускает Docker Desktop и до 10 минут ожидает daemon;
6. проверяет режим Linux containers и Docker Compose;
7. создает `.env`, собирает контейнер и ожидает `/healthz`;
8. выводит адрес dashboard и Swagger.

Скрипт не включает небезопасный TCP-доступ к daemon. Перед передачей WinGet
флагов принятия соглашений он показывает ссылку на лицензию Docker и требует
явно ввести `YES`; без этого установка прекращается. Docker Desktop также может
попросить подтверждение при первом запуске. После первого включения WSL Windows
может потребовать перезагрузку — тогда перезагрузите ПК и повторно запустите тот
же CMD-файл.

### Ручная установка

1. В BIOS/UEFI включите аппаратную виртуализацию.
2. Откройте PowerShell от имени администратора и установите WSL:

   ```powershell
   wsl --install
   wsl --update
   ```

3. Установите Docker Desktop, выберите backend WSL 2 и перезагрузите Windows.
4. Запустите Docker Desktop и дождитесь `Engine running`.
5. В обычном PowerShell из каталога проекта выполните:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\scripts\check_docker_windows.ps1
   .\scripts\start_windows.ps1
   ```

Dashboard откроется на <http://localhost:8000/>, Swagger — на
<http://localhost:8000/docs>.

## Что именно находится в архиве

В ZIP находятся Dockerfile, Compose-конфигурация и установочный скрипт. Сам
Docker Engine не копируется в архив: на Windows daemon работает внутри
служебной Linux VM/WSL-среды, устанавливается на уровне операционной системы и
обновляется отдельно от приложения. Такой способ нужен для сетей контейнеров,
изоляции, volume и корректных обновлений безопасности.

## Если `docker` есть, но daemon не отвечает

Симптомы: `Cannot connect to the Docker daemon`, ошибка pipe
`dockerDesktopLinuxEngine`, либо `docker version` показывает только Client.

Проверяйте по порядку:

1. Docker Desktop действительно запущен и завершил старт.
2. `wsl --status` и `wsl --version` выполняются без ошибки.
3. В Docker Desktop → Settings → General включен WSL 2 based engine.
4. Выполните `wsl --shutdown`, затем перезапустите Docker Desktop.
5. Проверьте контекст: `docker context use desktop-linux`.
6. Посмотрите диагностику Docker Desktop: Troubleshoot → Get support.

Не открывайте TCP-сокет daemon без TLS. Для локального проекта используется
штатный защищенный канал Docker Desktop.

## Linux

На Linux можно установить Docker Engine и Compose plugin по официальной
инструкции: <https://docs.docker.com/engine/install/>. Пользователь должен иметь
право обращаться к сокету Docker; добавление в группу `docker` эквивалентно
расширенным локальным правам.
