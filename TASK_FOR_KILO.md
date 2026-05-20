# CRITICAL INSTRUCTION FOR KILO / MINIMAX

**Rule 1: NEVER run blocking web servers directly in the terminal.**
You are running `python src/motor_ai_sim/api.py` or `uvicorn ...` directly. This blocks your terminal forever, causes you to hang on "Running (PID...)", and forces the user to manually click "Continue While Running" or "Kill Command" every single time. This is unacceptable for an autonomous agent.

**How you MUST run the server:**
Instead of typing `python src/motor_ai_sim/api.py`, you must ALWAYS launch it as a detached background process.
On Windows (PowerShell), you must use exactly this command:
`Start-Process -NoNewWindow python -ArgumentList "src/motor_ai_sim/api.py"`

This command will immediately return control to your terminal (exit code 0), while the server runs silently in the background. You will never hang again.

**If you need to restart the server:**
Before starting a new one, kill the old one using:
`taskkill /F /IM python.exe /T`
(Note: this will kill all python processes, which is fine for this dev environment).

**Your Immediate Task:**
1. You just hung again. The user had to click a button for you.
2. Remember the above rule forever for this workspace.
3. Now, proceed to implement the `.edges("%Z and >Y").fillet(mag_fill_radius)` logic on the magnet geometry as requested in the previous prompt.
4. After saving the file, just do the file save! Do NOT try to restart the server again, because the user or I (OpenClaw) are already running it in the background for you. Just say "Done!" after you edit `cadquery_geometry.py`.