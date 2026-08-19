@echo off
rem Start the motor_ai_sim API detached from any Claude session.
rem Registered as scheduled task "motor_ai_sim_api"; restart any time with:
rem   schtasks /run /tn motor_ai_sim_api
cd /d C:\Users\vadim\Projects\motor_ai_sim
python -m uvicorn motor_ai_sim.api:app --port 8001 --host 0.0.0.0 >> uvicorn_8001.out 2>> uvicorn_8001.err
