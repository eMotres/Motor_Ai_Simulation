' Run scripts\start_api.cmd with NO console window.
'
' The scheduled task "motor_ai_sim_api" runs in the interactive session, and on
' Windows 11 a .cmd launched there opens a Windows Terminal window with the
' uvicorn process in it (title "python.exe") every time the watchdog or a
' restart fires.  Closing that window kills the API.  This wrapper starts the
' same .cmd with window style 0 (hidden); stdout/stderr still go to
' uvicorn_8001.out/.err as start_api.cmd redirects them.
'
' Task action:  wscript.exe //B //Nologo "<repo>\scripts\start_api_hidden.vbs"
Dim sh, dir
Set sh = CreateObject("WScript.Shell")
dir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.Run "cmd.exe /c """ & dir & "start_api.cmd""", 0, False
