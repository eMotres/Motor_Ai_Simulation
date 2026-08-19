@echo off
rem Start the motor_ai_sim API detached from any Claude session.
rem Registered as scheduled task "motor_ai_sim_api" (watchdog: fires every
rem 5 minutes, exits at once when the port is already served).  Manual
rem restart any time with:  schtasks /run /tn motor_ai_sim_api
rem NB: /c: makes the space literal — without it findstr ORs two patterns and
rem a mere SYN_SENT poll to :8001 read as "already serving".
netstat -ano | findstr /r /c:":8001 .*LISTENING" >nul && exit /b 0
cd /d C:\Users\vadim\Projects\motor_ai_sim
python -m uvicorn motor_ai_sim.api:app --port 8001 --host 0.0.0.0 >> uvicorn_8001.out 2>> uvicorn_8001.err
