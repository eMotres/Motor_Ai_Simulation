@echo off
rem Start the motor_ai_sim API detached from any Claude session.
rem Registered as scheduled task "motor_ai_sim_api" (watchdog: fires every
rem 5 minutes, exits at once when the port is already served).  Manual
rem restart any time with:  schtasks /run /tn motor_ai_sim_api
rem
rem ASCII ONLY in this file.  cmd.exe reads a .cmd in the CONSOLE codepage, and
rem a multi-byte character in a rem line splits that line mid-word into
rem "'he' is not recognized as an internal or external command".
cd /d C:\Users\vadim\Projects\motor_ai_sim

rem Environment comes from scripts\api.env: GOOGLE_CLIENT_ID, AUTH_ENFORCE,
rem ADMIN_EMAILS, AUTH_SECRET.  That file is gitignored; scripts\api.env.example
rem is the template.  The OAuth client id used to be typed HERE and committed
rem (migration plan section 3): a value in git is a value nobody can rotate
rem without a commit, and this is the same line a real secret would be typed on
rem next.
rem
rem PARSED, not `call`ed.  `call some.env` does not work - cmd only executes
rem .bat/.cmd and hands anything else to the shell association, which hangs a
rem non-interactive task.  Parsing means api.env is a PLAIN KEY=value file:
rem byte-for-byte the same format as the server's /etc/motres/api.env, so a
rem value proven here is the value that ships.  `eol=#` skips comments,
rem `delims==` splits on the FIRST = and `tokens=1,*` keeps any further ones in
rem the value.  Sourced BEFORE the port probe so --print-env works while the
rem API is up.
if exist "%~dp0api.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0api.env") do set "%%A=%%B"
) else (
    echo [start_api] WARNING: scripts\api.env missing - copy api.env.example.
    echo [start_api] Google sign-in will be OFF and auth unenforced.
)

rem Dry check - proves the env file is read WITHOUT touching a running API:
rem   cmd /c scripts\start_api.cmd --print-env
if /i "%~1"=="--print-env" (
    echo GOOGLE_CLIENT_ID=%GOOGLE_CLIENT_ID%
    echo AUTH_ENFORCE=%AUTH_ENFORCE%
    echo ADMIN_EMAILS=%ADMIN_EMAILS%
    if defined AUTH_SECRET (echo AUTH_SECRET=^<set^>) else (echo AUTH_SECRET=^<unset^>)
    exit /b 0
)

rem NB: /c: makes the space literal - without it findstr ORs two patterns and
rem a mere SYN_SENT poll to :8001 read as "already serving".
netstat -ano | findstr /r /c:":8001 .*LISTENING" >nul && exit /b 0

python -m uvicorn motor_ai_sim.api:app --port 8001 --host 0.0.0.0 >> uvicorn_8001.out 2>> uvicorn_8001.err
